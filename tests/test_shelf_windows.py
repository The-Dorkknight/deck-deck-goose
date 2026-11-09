# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Tests on a simulated Windows PC (see fake_win.py)."""
import errno
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import fake_win  # noqa: E402
from ddg import jobs, vdf  # noqa: E402
from ddg.shelf import common, estimate, steam, winplat  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.spec = fake_win.build(self.base)
        self.root = Path(self.spec["steam_roots"][0])
        self.env = dict(os.environ)
        os.environ.update(HOME=str(self.base / "home"), DDG_FAKE_WIN=str(self.base / "spec.json"), DDG_PLATFORM="windows")
        os.environ.pop("DDG_FORCE_COPY", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        for dp, dns, fns in os.walk(self.tmp.name):   # read-only files in the fake games
            for n in dns + fns:
                try:
                    os.chmod(os.path.join(dp, n), 0o755)
                except OSError:
                    pass
        self.tmp.cleanup()

    def job(self):
        return jobs.Job("t")

    def set_spec(self, **kw):
        self.spec.update(kw)
        (self.base / "spec.json").write_text(json.dumps(self.spec))

    def set_running(self, on):
        st = winplat.fake_state()
        st["steam_running"] = on
        winplat.save_fake_state(st)

    def apps(self, f="config/libraryfolders.vdf"):
        d = vdf.load_file(self.root / f)["libraryfolders"]
        return {common.norm(e["path"]): set(e.get("apps", {})) for e in d.values()}

    def drive(self, letter):
        return self.base / letter


# ----------------------------------------------------------------- basics
class Basics(Base):
    def test_vdf_windows_paths(self):
        d = {"libraryfolders": {"1": {"path": "D:\\SteamLibrary", "apps": {}}}}
        text = vdf.dumps(d)
        self.assertIn('"D:\\\\SteamLibrary"', text)
        self.assertEqual(vdf.loads(text), d)

    def test_safe_names(self):
        for good in ("Portal 2", "Ünïcode ゲーム", "A-B_C (2)"):
            self.assertTrue(steam.safe_name(good), good)
        for bad in ("", ".", "..", "a\\b", "a/b", "C:x", "name.", "name ", 'q"', "a*b"):
            self.assertFalse(steam.safe_name(bad), bad)

    def test_discovery_and_art(self):
        games = {g.appid: g for g in steam.list_games()}
        self.assertEqual(len(games), 6)
        self.assertTrue(games["5100040"].tool)
        self.assertIn("update required", steam.decode_state(games["5100030"].state))
        self.assertIn("library_600x900", steam.art_path(self.root, "5100010").name)
        self.assertEqual(steam.art_path(self.root, "5100020").name, "5100020_library_600x900.jpg")
        self.assertIsNone(steam.art_path(self.root, "..\\x"))

    def test_targets(self):
        t = {x["label"].split(" - ")[0]: x for x in steam.move_targets(steam.find_game("5100010"))}
        self.assertTrue(t["Games (D:)"]["ok"])
        self.assertTrue(t["Spare (E:)"]["ok"] and t["Spare (E:)"]["new"])
        self.assertFalse(t["USB (F:)"]["ok"])
        self.assertIn("4 GB", t["USB (F:)"]["why"])
        self.assertTrue(t["Travel (G:)"]["ok"])            # exFAT allowed with a note
        self.assertFalse(t["NAS (N:)"]["ok"])
        self.assertNotIn("Windows (C:)", t)               # already the game's drive

    def test_cli_version(self):
        out = subprocess.run([sys.executable, "-m", "ddg", "--version"], cwd=str(ROOT / "src"),
                             capture_output=True, text=True).stdout
        self.assertRegex(out, r"Deck Deck Goose \S+ \(Built \d{4}-\d\d-\d\d \d\d:\d\d\)")


# ------------------------------------------------------------------ moves
class Moves(Base):
    def check_moved(self, appid, dst_lib, installdir="Star Courier"):
        dst, src = Path(dst_lib) / "steamapps", self.root / "steamapps"
        self.assertTrue((dst / ("appmanifest_%s.acf" % appid)).is_file())
        self.assertFalse((src / ("appmanifest_%s.acf" % appid)).exists())
        self.assertTrue((dst / "common" / installdir / "bin/game.exe").exists())
        self.assertTrue((dst / "common" / installdir / "readme.txt").exists())
        self.assertFalse((src / "common" / installdir).exists())
        for f in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
            a = self.apps(f)
            self.assertIn(appid, a[common.norm(dst_lib)])
            self.assertNotIn(appid, a[common.norm(self.root)])
        self.assertEqual(common.norm(steam.find_game(appid).library.path), common.norm(dst_lib))
        self.assertFalse(steam._journal().exists())

    def test_move_to_existing_library_on_other_drive(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        steps = steam.move_game(steam.find_game("5100010"), str(self.drive("D") / "SteamLibrary"), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        self.check_moved("5100010", self.drive("D") / "SteamLibrary")
        self.assertTrue((self.drive("D") / "SteamLibrary/steamapps/workshop/content/5100010/999/mod.txt").exists())

    def test_move_to_new_library_and_back(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        target = self.drive("E") / "SteamLibrary"
        steps = steam.move_game(steam.find_game("5100010"), str(target), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        self.check_moved("5100010", target)
        marker = vdf.load_file(target / "libraryfolder.vdf")["libraryfolder"]["contentid"]
        for f in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
            new = [e for e in vdf.load_file(self.root / f)["libraryfolders"].values()
                   if common.norm(e["path"]) == common.norm(target)]
            self.assertEqual(new[0]["contentid"], marker)
        steps = steam.move_game(steam.find_game("5100010"), str(self.root), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)

    def test_same_drive_is_instant_rename(self):
        target = self.drive("C") / "Games2"
        steps = steam.move_game(steam.find_game("5100020"), str(target), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertTrue(any("instant" in s.name for s in steps))
        self.check_moved("5100020", target, "Moss Valley")

    def test_exfat_target_gets_note(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        steps = steam.move_game(steam.find_game("5100020"), str(self.drive("G") / "SteamLibrary"), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        notes = " ".join(s.detail for s in steps if s.name == "Note about this drive")
        self.assertIn("removable", notes)
        self.assertIn("exFAT", notes)

    def test_refusals_change_nothing(self):
        g = steam.find_game("5100010")
        before = sorted(str(p) for p in (self.root / "steamapps").rglob("*"))
        self.set_running(True)
        self.assertIn("Close Steam", steam.move_game(g, str(self.drive("E") / "L"), self.job())[-1].detail)
        self.set_running(False)
        cases = [
            (steam.find_game("5100030"), self.drive("E") / "L", "update"),
            (g, self.drive("F") / "SteamLibrary", "4 GB"),
            (g, self.drive("N") / "SteamLibrary", "network"),
            (g, self.drive("D") / "SteamLibrary" / "steamapps", "library folder itself"),
            (g, self.drive("E") / "Missing" / "Deeper", "does not exist"),
            (g, self.root, "already in that library"),
        ]
        for game, target, why in cases:
            self.assertIn(why, steam.move_game(game, str(target), self.job())[-1].detail, why)
        os.environ["DDG_FORCE_COPY"] = "1"
        os.environ["DDG_FAKE_WIN_FREE_BYTES"] = str(10 ** 6)
        self.assertIn("Not enough space", steam.move_game(g, str(self.drive("E") / "L"), self.job())[-1].detail)
        os.environ.pop("DDG_FAKE_WIN_FREE_BYTES")
        (self.drive("E") / "L/steamapps/common/Star Courier").mkdir(parents=True)
        self.assertIn("already exists", steam.move_game(g, str(self.drive("E") / "L"), self.job())[-1].detail)
        drives = self.spec["drives"]
        drives[2]["readonly"] = True
        self.set_spec(drives=drives)
        self.assertIn("read-only", steam.move_game(g, str(self.drive("E") / "M"), self.job())[-1].detail)
        self.assertEqual(before, sorted(str(p) for p in (self.root / "steamapps").rglob("*")))

    def test_cancel_and_failures_roll_back(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        target = str(self.drive("E") / "SteamLibrary")
        orig = steam._copy_file
        for kind in ("cancel", "disk full", "bad copy"):
            n = [0]

            def patched(s, d, j, done, kind=kind):
                n[0] += 1
                if n[0] == 3 and kind == "cancel":
                    j.cancel_event.set()
                if n[0] == 3 and kind == "disk full":
                    raise OSError(errno.ENOSPC, "There is not enough space on the disk")
                orig(s, d, j, done)
            steam._copy_file = patched
            if kind == "bad copy":
                steam._verify = lambda s, d: (False, "simulated")
            job = self.job()
            try:
                jobs.run_now(job, lambda j: steam.move_game(steam.find_game("5100010"), target, j))
            finally:
                steam._copy_file = orig
                steam._verify = Moves._verify_orig
            self.assertTrue((self.root / "steamapps/appmanifest_5100010.acf").exists(), kind)
            self.assertTrue((self.root / "steamapps/common/Star Courier/data.pak").exists(), kind)
            self.assertEqual([p for p in self.drive("E").rglob("*") if p.is_file()], [], kind)
            self.assertFalse(steam._journal().exists(), kind)
        self.assertEqual(job.state, "failed")

    def test_crash_recovery_both_ways(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        target = str(self.drive("E") / "SteamLibrary")
        for fn, expect in (("add_library", "rolled back"), ("set_app_library", "finished")):
            orig = getattr(steam, fn)

            def boom(*a, fn=fn):
                if fn == "add_library":
                    sa = self.root / "steamapps"
                    if (sa / "appmanifest_5100020.acf").exists():
                        os.rename(str(sa / "appmanifest_5100020.acf"), str(sa / ("appmanifest_5100020.acf" + steam.MOVING)))
                raise KeyboardInterrupt("power cut")
            setattr(steam, fn, boom)
            try:
                with self.assertRaises(KeyboardInterrupt):
                    steam.move_game(steam.find_game("5100020"), target, self.job())
            finally:
                setattr(steam, fn, orig)
            self.assertIn(expect, steam.recover_moves()[0].detail)
        self.assertTrue((self.drive("E") / "SteamLibrary/steamapps/common/Moss Valley/data.pak").exists())
        self.assertFalse((self.root / "steamapps/common/Moss Valley").exists())

    def test_file_in_use_during_cleanup(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        orig, n = steam._rm, [0]

        def busy(path):
            if Path(str(path)).as_posix().endswith("common/Moss Valley") and n[0] == 0:
                n[0] += 1
                raise PermissionError(13, "The process cannot access the file because it is being used")
            orig(path)
        steam._rm = busy
        try:
            steps = steam.move_game(steam.find_game("5100020"), str(self.drive("E") / "SteamLibrary"), self.job())
        finally:
            steam._rm = orig
        old = [s for s in steps if s.name == "Old copy"]
        self.assertTrue(old and not old[0].ok and "works from" in old[0].detail)
        self.assertTrue((self.drive("E") / "SteamLibrary/steamapps/appmanifest_5100020.acf").exists())
        self.assertIn("finished", steam.recover_moves()[0].detail)
        self.assertFalse((self.root / "steamapps/common/Moss Valley").exists())

    def test_cross_volume_rename_falls_back_to_copy(self):
        real, hits = os.rename, [0]

        def rename(a, b):
            if Path(str(a)).as_posix().endswith("common/Moss Valley") and hits[0] == 0:
                hits[0] += 1
                e = OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")
                raise e
            return real(a, b)
        steam.os.rename = rename
        try:
            steps = steam.move_game(steam.find_game("5100020"), str(self.drive("C") / "Games2"), self.job())
        finally:
            steam.os.rename = real
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertTrue(any(s.name == "Copied and checked" for s in steps))

    def test_odd_manifests(self):
        sa = self.root / "steamapps"
        vdf.dump_file({"AppState": {"appid": "5200001", "name": "Evil", "StateFlags": "4",
                                    "installdir": "..\\..\\Windows"}}, sa / "appmanifest_5200001.acf")
        vdf.dump_file({"AppState": {"appid": "5200002", "name": "Empty", "StateFlags": "4",
                                    "installdir": ""}}, sa / "appmanifest_5200002.acf")
        for a in ("5200001", "5200002"):
            g = steam.find_game(a)
            self.assertEqual(g.items(), [])
            self.assertFalse(steam.move_game(g, str(self.drive("E") / "L"), self.job())[-1].ok)
            self.assertFalse(steam.compress_game(g, self.job())[0].ok)
        (self.drive("D") / "SteamLibrary/steamapps/appmanifest_5100010.acf").write_bytes(
            (sa / "appmanifest_5100010.acf").read_bytes())
        self.assertIn("2 libraries", steam.find_game("5100010").extra["problem"])

    def test_broken_and_missing_library_list(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        for f in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
            (self.root / f).write_text('"libraryfolders"\n{\n')
        self.assertIn("could not be read", steam.move_game(
            steam.find_game("5100010"), str(self.drive("E") / "SteamLibrary"), self.job())[-1].detail)
        for f in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
            (self.root / f).unlink()
        steps = steam.move_game(steam.find_game("5100010"), str(self.drive("E") / "SteamLibrary"), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        d = vdf.load_file(self.root / "steamapps/libraryfolders.vdf")["libraryfolders"]
        self.assertEqual(common.norm(d["0"]["path"]), common.norm(self.root))


Moves._verify_orig = staticmethod(steam._verify)


# ------------------------------------------------------------------- junk
class Junk(Base):
    def test_scan(self):
        j = {d["category"] + ":" + os.path.basename(d["path"]): d for d in steam.scan_junk()}
        self.assertTrue(j["shader-orphan:555"]["default"])
        self.assertTrue(j["download:777"]["default"])
        self.assertFalse(j["orphan-folder:Old Game Leftovers"]["default"])
        self.assertFalse(j["redist:_CommonRedist"]["default"])
        self.assertIn("verify", j["redist:_CommonRedist"]["warning"])
        self.assertFalse(j["shader:5100010"]["default"])
        self.assertTrue(j["steam-cache:logs"]["default"])

    def test_clean_defaults_only(self):
        steps = steam.clean_junk([d["id"] for d in steam.scan_junk() if d["default"]], self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        sa = self.root / "steamapps"
        self.assertFalse((sa / "shadercache/555").exists())
        self.assertTrue((sa / "common/Old Game Leftovers/settings.ini").exists())
        self.assertTrue((sa / "common/Star Courier/_CommonRedist").exists())
        self.assertEqual(steam.clean_junk(["x:C:\\Windows"], self.job())[-1].detail, "nothing selected")

    def test_clean_redist_with_read_only_files(self):
        ro = self.root / "steamapps/common/Star Courier/_CommonRedist/DirectX/setup.bin"
        os.chmod(str(ro), 0o444)
        ids = [d["id"] for d in steam.scan_junk() if d["category"] == "redist"]
        self.assertTrue(all(s.ok for s in steam.clean_junk(ids, self.job())))
        self.assertFalse(ro.exists())
        self.assertTrue((self.root / "steamapps/common/Star Courier/bin/game.exe").exists())


# ------------------------------------------------------------ compression
class Compression(Base):
    def test_model(self):
        self.assertLess(estimate.model_size(b"\0" * 65536, "XPRESS8K"), 65536)
        self.assertEqual(estimate.model_size(os.urandom(65536), "XPRESS8K"), 65536)
        self.assertLessEqual(estimate.model_size(b"ab" * 40000, "LZX"), estimate.model_size(b"ab" * 40000, "XPRESS4K"))

    def test_not_ntfs(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        steam.move_game(steam.find_game("5100020"), str(self.drive("G") / "SteamLibrary"), self.job())
        self.assertIn("NTFS", steam.estimate_game(steam.find_game("5100020"), self.job())[0].detail)

    def test_estimate_compress_update_uncompress(self):
        g = steam.find_game("5100010")
        self.assertTrue(steam.estimate_game(g, self.job(), "XPRESS8K")[0].ok)
        est = steam.records("estimates")["5100010"]
        self.assertGreater(est["saved"], 0)
        steps = steam.compress_game(g, self.job(), "XPRESS8K")
        self.assertTrue(steps[0].ok, steps)
        calls = winplat.fake_state()["compact_calls"]
        names = [n for c in calls for n in c["files"]]
        self.assertIn("levels.dat", names)
        self.assertIn("game.exe", names)
        self.assertNotIn("intro.bk2", names)
        self.assertNotIn("data.pak", names)
        self.assertEqual(calls[0]["algo"], "XPRESS8K")
        saved = steam.records("compressed")["5100010"]["saved"]
        self.assertGreater(saved, 0)
        self.assertNotIn("5100010", steam.records("estimates"))
        # now everything is already compressed: nothing more to gain
        steam.estimate_game(g, self.job())
        e2 = steam.records("estimates")["5100010"]
        self.assertEqual(e2["saved"], 0)
        self.assertGreater(e2["already"], 0)
        # a Steam update rewrites a file: Windows stores it uncompressed again
        lv = g.dir / "levels.dat"
        lv.write_bytes(lv.read_bytes() + b" patch")
        steam.estimate_game(g, self.job())
        self.assertGreater(steam.records("estimates")["5100010"]["saved"], 0)
        steps = steam.uncompress_game(g, self.job())
        self.assertTrue(steps[0].ok, steps)
        self.assertNotIn("5100010", steam.records("compressed"))
        self.assertIsNone(calls and winplat.fake_state()["compact_calls"][-1]["algo"])

    def test_directstorage_left_alone(self):
        g = steam.find_game("5100060")
        steam.estimate_game(g, self.job())
        self.assertTrue(steam.records("estimates")["5100060"]["directstorage"])
        self.assertIn("DirectStorage", steam.compress_game(g, self.job())[0].detail)
        self.assertTrue(steam.compress_game(g, self.job(), force=True)[0].ok)

    def test_compact_errors_reported(self):
        os.environ["DDG_FAKE_COMPACT_ERROR"] = "1"
        steps = steam.compress_game(steam.find_game("5100010"), self.job())
        self.assertFalse(steps[0].ok)
        self.assertIn("Access is denied", steps[0].detail)

    def test_refusals(self):
        self.assertFalse(steam.compress_game(steam.find_game("5100010"), self.job(), "ZIP")[0].ok)
        self.assertFalse(steam.compress_game(steam.find_game("5100030"), self.job())[0].ok)
        self.set_running(True)
        self.assertIn("Close Steam", steam.compress_game(steam.find_game("5100010"), self.job())[0].detail)


# ------------------------------------------------------- locks & settings
class LocksAndSettings(Base):
    def test_second_window(self):
        common.config_dir().mkdir(parents=True, exist_ok=True)
        fd = os.open(str(common.config_dir() / "steam.lock"), os.O_RDWR | os.O_CREAT)
        self.assertTrue(winplat.lock_file(fd))
        try:
            g = steam.find_game("5100010")
            self.assertIn("Another", steam.move_game(g, str(self.drive("E") / "L"), self.job())[0].detail)
            self.assertIn("Another", steam.compress_game(g, self.job())[0].detail)
            self.assertIn("Another", steam.clean_junk([], self.job())[0].detail)
        finally:
            winplat.unlock_file(fd)
            os.close(fd)

    def test_parallel_records(self):
        ts = [threading.Thread(target=steam._record, args=("estimates", str(i), {"saved": i})) for i in range(40)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(steam.records("estimates")), 40)


# -------------------------------------------------------------------- web
class Web(Base):
    def test_api_guarded(self):
        import http.client
        from ddg import webui
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "tok"
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def req(method, path, body=None, token="tok"):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                c.request(method, path, body=json.dumps(body) if body is not None else None,
                          headers={"X-Token": token, "Content-Type": "application/json"})
                r = c.getresponse()
                data = r.read()
                c.close()
                return r.status, data
            self.assertEqual(req("GET", "/api/art/5100010")[0], 403)
            self.assertEqual(req("GET", "/api/art/5100010?k=tok")[0], 200)
            self.assertEqual(req("GET", "/api/games", token="bad")[0], 403)
            self.assertIn(b"not available", req("POST", "/api/games/move", {"appid": "5100010", "target": "C:\\Windows"})[1])
            self.assertIn(b"unknown compression", req("POST", "/api/games/compress", {"appid": "5100010", "algo": "x"})[1])
            st, data = req("GET", "/api/games")
            self.assertEqual(len(json.loads(data)["games"]), 6)
            self.assertTrue(json.loads(data)["simulated"])
        finally:
            srv.shutdown()
            srv.server_close()


# ---------------------------------------------------------------- project
if __name__ == "__main__":
    unittest.main()
