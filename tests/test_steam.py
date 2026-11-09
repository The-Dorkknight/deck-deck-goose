# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import fake_steam  # noqa: E402
from ddg import core, jobs, steam, vdf  # noqa: E402


class SteamBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.home = t / "home/matthew"
        self.home.mkdir(parents=True)
        self.drive2 = t / "mnt/drive2"
        self.drive3 = t / "mnt/drive3"
        self.usb = t / "mnt/usbstick"
        for d in (self.drive2, self.drive3, self.usb):
            d.mkdir(parents=True)
        self.root = fake_steam.build(self.home, self.drive2)
        lsblk = {"blockdevices": [
            {"name": "sdb1", "path": "/dev/sdb1", "label": "Games2", "fstype": "ext4", "size": 10 ** 12,
             "rm": False, "type": "part", "mountpoints": [str(self.drive2)]},
            {"name": "sdc1", "path": "/dev/sdc1", "label": "Spare", "fstype": "btrfs", "size": 10 ** 12,
             "rm": False, "type": "part", "mountpoints": [str(self.drive3)]},
            {"name": "sdd1", "path": "/dev/sdd1", "label": "USB", "fstype": "exfat", "size": 32 * 10 ** 9,
             "rm": True, "type": "part", "mountpoints": [str(self.usb)]}]}
        (t / "lsblk.json").write_text(json.dumps(lsblk))
        (t / "mounts").write_text(
            "/dev/sda2 / btrfs rw,compress=zstd:1 0 0\n"
            "/dev/sda2 %s btrfs rw,compress=zstd:1 0 0\n"
            "/dev/sdb1 %s ext4 rw 0 0\n/dev/sdc1 %s btrfs rw 0 0\n/dev/sdd1 %s exfat rw 0 0\n"
            % (self.home, self.drive2, self.drive3, self.usb))
        self.old_prefixes = core.DATA_PREFIXES
        core.DATA_PREFIXES = (str(t / "mnt") + "/",)
        steam._DISK_CACHE["t"] = 0.0
        self.env = dict(os.environ)
        os.environ.update(HOME=str(self.home), THISPC_FAKE_TOOLS="1",
                          THISPC_LSBLK_JSON=str(t / "lsblk.json"), THISPC_MOUNTS_FILE=str(t / "mounts"))
        os.environ.pop("THISPC_FORCE_COPY", None)

    def tearDown(self):
        core.DATA_PREFIXES = self.old_prefixes
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()

    def job(self):
        return jobs.Job("test")

    def lib_apps(self, f="config/libraryfolders.vdf"):
        d = vdf.load_file(self.root / f)["libraryfolders"]
        return {os.path.realpath(e["path"]): set(e.get("apps", {})) for e in d.values()}


class VdfTests(unittest.TestCase):
    def test_roundtrip_escapes_comments(self):
        text = ('// comment\n"AppState"\n{\n\t"name"\t\t"Say \\"hi\\" C:\\\\Games"\n'
                '\t"empty"\t\t""\n\t"sub"\n\t{\n\t\t"a"\t\t"1"\n\t}\n}\n')
        d = vdf.loads(text)
        self.assertEqual(d["AppState"]["name"], 'Say "hi" C:\\Games')
        self.assertEqual(d["AppState"]["sub"], {"a": "1"})
        self.assertEqual(vdf.loads(vdf.dumps(d)), d)

    def test_bad_input_raises(self):
        for bad in ('"a"\n{\n"b" "c"\n', '"a"', '}'):
            with self.assertRaises(ValueError):
                vdf.loads(bad)


class DiscoveryTests(SteamBase):
    def test_finds_games_tools_and_libraries(self):
        games = {g.appid: g for g in steam.list_games()}
        self.assertEqual(set(games), {"4100010", "4100020", "4100030", "4100040", "4100050"})
        self.assertTrue(games["4100040"].tool)
        self.assertFalse(games["4100010"].tool)
        self.assertEqual(os.path.realpath(str(games["4100050"].library.path)),
                         os.path.realpath(str(self.drive2 / "SteamLibrary")))
        self.assertIn("update required", steam.decode_state(games["4100030"].state))

    def test_artwork_new_old_and_custom(self):
        self.assertIn("library_600x900", steam.art_path(self.root, "4100010").name)
        self.assertEqual(steam.art_path(self.root, "4100020").name, "4100020_library_600x900.jpg")
        grid = self.root / "userdata/123/config/grid"
        grid.mkdir(parents=True)
        (grid / "4100010p.png").write_bytes(b"png")
        self.assertEqual(steam.art_path(self.root, "4100010"), grid / "4100010p.png")
        self.assertIsNone(steam.art_path(self.root, "../etc"))
        self.assertIsNone(steam.art_path(self.root, "4199999"))

    def test_flatpak_steam_found(self):
        flat = self.home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps"
        flat.mkdir(parents=True)
        roots = dict((str(p), f) for p, f in steam.steam_roots())
        self.assertIn(True, roots.values())

    def test_targets(self):
        g = steam.find_game("4100010")
        t = {os.path.basename(os.path.dirname(x["path"])) if x["new"] else "lib2": x for x in steam.move_targets(g)}
        self.assertTrue(t["lib2"]["ok"])
        self.assertTrue(t["drive3"]["ok"] and t["drive3"]["new"])
        self.assertFalse(t["usbstick"]["ok"])
        self.assertIn("exfat", t["usbstick"]["why"])
        self.assertNotIn("drive2", t)   # already has a library


class MoveTests(SteamBase):
    def _check_moved(self, appid, dst_lib):
        dst = Path(dst_lib) / "steamapps"
        src = self.root / "steamapps"
        self.assertTrue((dst / ("appmanifest_%s.acf" % appid)).is_file())
        self.assertFalse((src / ("appmanifest_%s.acf" % appid)).exists())
        self.assertTrue((dst / "common/StarCourier/bin/game.sh").stat().st_mode & 0o100)
        self.assertEqual(os.readlink(str(dst / "common/StarCourier/start.sh")), "bin/game.sh")
        self.assertEqual((dst / "compatdata" / appid / "pfx/drive_c/users/steamuser/Saved Games/save1.dat")
                         .read_text(), "level 7")
        self.assertEqual(os.readlink(str(dst / "compatdata" / appid / "pfx/dosdevices/c:")), "../drive_c")
        self.assertTrue((dst / "workshop/content" / appid / "999/mod.txt").exists())
        self.assertTrue((dst / ("workshop/appworkshop_%s.acf" % appid)).exists())
        for rel in ("common/StarCourier", "compatdata/" + appid, "shadercache/" + appid):
            self.assertFalse(os.path.lexists(str(src / rel)), rel)
        for f in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
            apps = self.lib_apps(f)
            self.assertIn(appid, apps[os.path.realpath(str(dst_lib))], f)
            self.assertNotIn(appid, apps[os.path.realpath(str(self.root))], f)
        g = steam.find_game(appid)
        self.assertEqual(os.path.realpath(str(g.library.path)), os.path.realpath(str(dst_lib)))
        self.assertFalse(steam._journal().exists())

    def test_move_same_drive_rename(self):
        steps = steam.move_game(steam.find_game("4100010"), str(self.drive2 / "SteamLibrary"), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertTrue(any("instant" in s.name for s in steps))
        self._check_moved("4100010", self.drive2 / "SteamLibrary")

    def test_move_other_drive_copy_and_new_library(self):
        os.environ["THISPC_FORCE_COPY"] = "1"
        target = self.drive3 / "SteamLibrary"
        steps = steam.move_game(steam.find_game("4100010"), str(target), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertTrue(any(s.name == "Copied and checked" for s in steps))
        self._check_moved("4100010", target)
        marker = vdf.load_file(target / "libraryfolder.vdf")["libraryfolder"]
        for f in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
            entries = vdf.load_file(self.root / f)["libraryfolders"].values()
            new = [e for e in entries if os.path.realpath(e["path"]) == os.path.realpath(str(target))]
            self.assertEqual(len(new), 1)
            self.assertEqual(new[0]["contentid"], marker["contentid"])
        self.assertTrue(list((self.root / "config").glob("libraryfolders.vdf" + steam.BACKUP + "-*")))
        # and back again
        steps = steam.move_game(steam.find_game("4100010"), str(self.root), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertTrue((self.root / "steamapps/common/StarCourier/data.pak").exists())

    def test_refusals_change_nothing(self):
        g = steam.find_game("4100010")
        before = sorted(str(p) for p in (self.root / "steamapps").rglob("*"))
        os.environ["THISPC_FAKE_STEAM_RUNNING"] = "1"
        self.assertIn("Close Steam", steam.move_game(g, str(self.drive2 / "SteamLibrary"), self.job())[-1].detail)
        os.environ.pop("THISPC_FAKE_STEAM_RUNNING")
        (self.home / ".thispc-fake-state.json").unlink(missing_ok=True)
        self.assertIn("update", steam.move_game(steam.find_game("4100030"), str(self.drive3 / "L"), self.job())[-1].detail)
        self.assertIn("exfat", steam.move_game(g, str(self.usb / "SteamLibrary"), self.job())[-1].detail)
        os.environ["THISPC_FORCE_COPY"] = "1"
        os.environ["THISPC_FAKE_FREE_BYTES"] = str(10 ** 6)
        self.assertIn("Not enough space", steam.move_game(g, str(self.drive3 / "L"), self.job())[-1].detail)
        os.environ.pop("THISPC_FAKE_FREE_BYTES")
        (self.drive3 / "L/steamapps/common/StarCourier").mkdir(parents=True)
        self.assertIn("already exists", steam.move_game(g, str(self.drive3 / "L"), self.job())[-1].detail)
        self.assertEqual(before, sorted(str(p) for p in (self.root / "steamapps").rglob("*")))

    def test_cancel_during_copy_leaves_game_untouched(self):
        os.environ["THISPC_FORCE_COPY"] = "1"
        g = steam.find_game("4100010")
        job = self.job()
        orig = steam._copy_file
        calls = [0]

        def slow(s, d, j, done):
            calls[0] += 1
            if calls[0] == 2:
                j.cancel_event.set()
            orig(s, d, j, done)
        steam._copy_file = slow
        try:
            jobs.run_now(job, lambda j: steam.move_game(g, str(self.drive3 / "SteamLibrary"), j))
        finally:
            steam._copy_file = orig
        self.assertEqual(job.state, "cancelled")
        self.assertTrue((self.root / "steamapps/appmanifest_4100010.acf").exists())
        self.assertTrue((self.root / "steamapps/common/StarCourier/data.pak").exists())
        leftovers = [p for p in (self.drive3).rglob("*") if p.is_file()]
        self.assertEqual(leftovers, [])
        self.assertFalse(steam._journal().exists())

    def test_verify_failure_rolls_back(self):
        os.environ["THISPC_FORCE_COPY"] = "1"
        orig = steam._verify
        steam._verify = lambda s, d: (False, "simulated bad copy")
        try:
            steps = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        finally:
            steam._verify = orig
        self.assertFalse(steps[-1].ok)
        self.assertIn("untouched", steps[-1].detail)
        self.assertTrue((self.root / "steamapps/appmanifest_4100010.acf").exists())
        self.assertEqual([p for p in self.drive3.rglob("*") if p.is_file()], [])

    def test_recover_after_crash_mid_commit(self):
        """Simulate power loss after the old manifest was set aside but before
        the new one was written: recovery must put the game back."""
        os.environ["THISPC_FORCE_COPY"] = "1"
        orig = steam.add_library

        def boom(root, path):
            src = self.root / "steamapps"
            os.rename(str(src / "appmanifest_4100010.acf"), str(src / ("appmanifest_4100010.acf" + steam.MOVING)))
            raise KeyboardInterrupt("power cut")
        steam.add_library = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        finally:
            steam.add_library = orig
        self.assertTrue(steam._journal().exists())
        steps = steam.recover_moves()
        self.assertIn("rolled back", steps[0].detail)
        self.assertTrue((self.root / "steamapps/appmanifest_4100010.acf").exists())
        self.assertTrue((self.root / "steamapps/compatdata/4100010/pfx").is_dir())
        self.assertFalse((self.drive3 / "SteamLibrary/steamapps/common/StarCourier").exists())

    def test_recover_after_crash_in_cleanup(self):
        os.environ["THISPC_FORCE_COPY"] = "1"
        orig = steam.set_app_library

        def boom(*a):
            raise KeyboardInterrupt("power cut")
        steam.set_app_library = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        finally:
            steam.set_app_library = orig
        steps = steam.recover_moves()
        self.assertIn("finished", steps[0].detail)
        self.assertFalse((self.root / "steamapps/common/StarCourier").exists())
        self.assertTrue((self.drive3 / "SteamLibrary/steamapps/common/StarCourier/data.pak").exists())

    def test_flatpak_steam_gets_access(self):
        flat_root = self.home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
        flat_root.parent.mkdir(parents=True)
        os.rename(str(self.root), str(flat_root))
        for f in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
            d = vdf.load_file(flat_root / f)
            d["libraryfolders"]["0"]["path"] = str(flat_root)
            vdf.dump_file(d, flat_root / f)
        self.root = flat_root
        os.environ["THISPC_FORCE_COPY"] = "1"
        steps = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        st = json.loads((self.home / ".thispc-fake-state.json").read_text())
        self.assertIn(os.path.realpath(str(self.drive3 / "SteamLibrary")),
                      st["overrides"]["com.valvesoftware.Steam"])


class JunkTests(SteamBase):
    def test_scan_categories_and_defaults(self):
        j = {d["category"] + ":" + os.path.basename(d["path"]): d for d in steam.scan_junk()}
        self.assertTrue(j["shader-orphan:555"]["default"])
        self.assertFalse(j["shader:4100010"]["default"])
        self.assertTrue(j["download:777"]["default"])
        self.assertFalse(j["orphan-prefix:999"]["default"])
        self.assertIn("SAVE", j["orphan-prefix:999"]["warning"])
        self.assertNotIn("orphan-prefix:3123456789", j)   # non-Steam shortcut prefix kept
        self.assertFalse(j["orphan-folder:Old Game Leftovers"]["default"])
        self.assertNotIn("orphan-folder:StarCourier", j)
        self.assertTrue(j["steam-cache:logs"]["default"])

    def test_clean_defaults_only(self):
        ids = [d["id"] for d in steam.scan_junk() if d["default"]]
        steps = steam.clean_junk(ids, self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        sa = self.root / "steamapps"
        self.assertFalse((sa / "shadercache/555").exists())
        self.assertFalse((sa / "downloading/777").exists())
        self.assertTrue((sa / "compatdata/999").exists())
        self.assertTrue((sa / "common/Old Game Leftovers/mods.txt").exists())
        self.assertTrue((sa / "shadercache/4100010").exists())
        self.assertTrue((self.root / "logs").is_dir())
        self.assertEqual(list((self.root / "logs").iterdir()), [])

    def test_clean_ignores_unknown_ids_and_needs_steam_closed(self):
        steps = steam.clean_junk(["shader:/etc", "partial:%s" % self.home], self.job())
        self.assertEqual(steps[-1].detail, "nothing selected")
        os.environ["THISPC_FAKE_STEAM_RUNNING"] = "1"
        logs = [d["id"] for d in steam.scan_junk() if d["category"] == "steam-cache"]
        self.assertIn("Close Steam", steam.clean_junk(logs, self.job())[0].detail)   # not safe while running
        orphan = [d["id"] for d in steam.scan_junk() if d["category"] == "shader-orphan"]
        self.assertTrue(all(x.ok for x in steam.clean_junk(orphan, self.job())))  # safe while running


class CompressTests(SteamBase):
    def test_not_btrfs(self):
        g = steam.find_game("4100050")       # on ext4 drive2
        steps = steam.compress_game(g, self.job())
        self.assertFalse(steps[0].ok)
        self.assertIn("ext4", steps[0].detail)

    def test_btrfs_compresses_only_worthwhile_files(self):
        g = steam.find_game("4100010")       # home is btrfs in the fake mounts
        self.assertEqual(steam.compression_info(g.dir)["mount_compress"], "zstd:1")
        steps = steam.compress_game(g, self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        calls = json.loads((self.home / ".thispc-fake-state.json").read_text())["btrfs_calls"]
        defrag = [c for c in calls if c[:2] == ["filesystem", "defragment"]]
        self.assertEqual(defrag[0][:3], ["filesystem", "defragment", "-czstd"])
        files = [os.path.basename(f) for c in defrag for f in c[4:]]
        self.assertIn("levels.dat", files)
        self.assertNotIn("intro.bk2", files)      # video: skipped by format
        self.assertNotIn("data.pak", files)       # random data: would not shrink
        self.assertIn(["property", "set", str(g.dir), "compression", "zstd"], calls)
        self.assertIn("4100010", steam.compressed_record())

    def test_estimate_records_and_changes_nothing(self):
        g = steam.find_game("4100010")
        before = {p: p.stat().st_mtime for p in g.dir.rglob("*") if p.is_file()}
        steps = steam.estimate_game(g, self.job())
        self.assertTrue(steps[0].ok, steps)
        rec = steam.estimates_record()["4100010"]
        self.assertGreater(rec["saved"], 0)
        self.assertLess(rec["saved"], rec["total"])
        self.assertEqual(before, {p: p.stat().st_mtime for p in g.dir.rglob("*") if p.is_file()})
        self.assertNotIn("btrfs_calls", json.loads((self.home / ".thispc-fake-state.json").read_text())
                         if (self.home / ".thispc-fake-state.json").exists() else {})
        status = {x["appid"]: x for x in steam.games_status()["games"]}
        self.assertEqual(status["4100010"]["estimate"]["saved"], rec["saved"])

    def test_compress_refuses_game_needing_update(self):
        self.assertFalse(steam.compress_game(steam.find_game("4100030"), self.job())[0].ok)

    def test_permission_error_reported(self):
        os.environ["THISPC_FAKE_BTRFS_EPERM"] = "1"
        steps = steam.compress_game(steam.find_game("4100010"), self.job())
        self.assertFalse(steps[0].ok)
        self.assertIn("not permitted", steps[0].detail)


class RunnerTests(SteamBase):
    def test_one_job_at_a_time(self):
        gate = threading.Event()
        r = jobs.Runner()
        r.start("slow", lambda j: (gate.wait(5), [core.Step("x", True)])[1])
        with self.assertRaises(RuntimeError):
            r.start("second", lambda j: [])
        gate.set()
        r.wait(5)
        self.assertEqual(r.current.state, "done")

    def test_status_shape(self):
        s = steam.games_status()
        self.assertEqual(len(s["games"]), 5)
        self.assertTrue(all("targets" in g for g in s["games"]))
        self.assertTrue(any(l["can_compress"] for l in s["libraries"]))


if __name__ == "__main__":
    unittest.main()


class WebTests(SteamBase):
    def test_art_and_move_api_guarded(self):
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
                return r.status, r.read()
            self.assertEqual(req("GET", "/api/art/4100010")[0], 403)
            st, data = req("GET", "/api/art/4100010?k=tok")
            self.assertEqual(st, 200)
            self.assertTrue(data.startswith(b"\xff\xd8\xff"))
            self.assertEqual(req("GET", "/api/art/..%2F..%2Fetc?k=tok")[0], 404)
            self.assertEqual(req("GET", "/api/games", token="bad")[0], 403)
            st, data = req("POST", "/api/games/move", {"appid": "4100010", "target": "/etc"})
            self.assertIn(b"not available", data)
            st, data = req("GET", "/api/games")
            self.assertEqual(len(json.loads(data)["games"]), 5)
        finally:
            srv.shutdown()
            srv.server_close()
