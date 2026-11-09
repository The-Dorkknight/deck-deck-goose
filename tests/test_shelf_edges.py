# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Edge cases and bug checks for Deck Deck Goose (simulated Windows PC)."""
import json
import ntpath
import os
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_shelf_windows import Base  # noqa: E402
from ddg import jobs, vdf  # noqa: E402
from ddg.shelf import common, steam, winplat  # noqa: E402

AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


class WindowsPathRules(unittest.TestCase):
    def setUp(self):
        self.old = common._path
        common._path = ntpath

    def tearDown(self):
        common._path = self.old

    def test_long_path_prefix(self):
        self.assertEqual(common.lp("C:\\Games\\X", windows=True), "\\\\?\\C:\\Games\\X")
        self.assertEqual(common.lp("\\\\nas\\share\\X", windows=True), "\\\\?\\UNC\\nas\\share\\X")
        self.assertEqual(common.lp("\\\\?\\C:\\X", windows=True), "\\\\?\\C:\\X")
        self.assertEqual(common.unlp("\\\\?\\C:\\X"), "C:\\X")
        self.assertEqual(common.unlp("\\\\?\\UNC\\nas\\share"), "\\\\nas\\share")
        self.assertEqual(common.lp("/home/x"), "/home/x" if os.name != "nt" else common.lp("/home/x"))


class VdfEdges(unittest.TestCase):
    def test_bom_crlf_and_odd_numbers(self):
        text = '\ufeff"AppState"\r\n{\r\n\t"appid"\t\t"1"\r\n\t"SizeOnDisk"\t\t"lots"\r\n}\r\n'
        self.assertEqual(vdf.loads(text)["AppState"]["SizeOnDisk"], "lots")


class Layout(Base):
    def test_duplicate_library_spellings_and_missing_drive(self):
        for f in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
            d = vdf.load_file(self.root / f)
            d["libraryfolders"]["2"] = {"path": str(self.drive("D") / "SteamLibrary") + os.sep, "apps": {}}
            d["libraryfolders"]["3"] = {"path": str(self.base / "Z" / "SteamLibrary"), "apps": {"9": "1"}}
            vdf.dump_file(d, self.root / f)
        libs = steam.libraries(self.root)
        self.assertEqual(len(libs), 3)                        # C, D (once), unplugged Z
        self.assertFalse([l for l in libs if "Z" in str(l.path)][0].available)
        self.assertEqual(len(steam.list_games()), 6)
        t = steam.move_targets(steam.find_game("5100010"))
        z = [x for x in t if "Z" in x["path"]][0]
        self.assertFalse(z["ok"])
        steam.scan_junk()                                      # must not crash on the missing drive

    def test_bad_numbers_in_manifest(self):
        mf = self.root / "steamapps/appmanifest_5100020.acf"
        d = vdf.load_file(mf)
        d["AppState"]["SizeOnDisk"] = "lots"
        d["AppState"]["StateFlags"] = ""
        vdf.dump_file(d, mf)
        g = steam.find_game("5100020")
        self.assertEqual((g.size, g.state), (0, 0))
        self.assertFalse(steam.move_game(g, str(self.drive("E") / "SteamLibrary"), self.job())[-1].ok)

    def test_downloading_game_leftovers_kept(self):
        (self.root / "steamapps/downloading/5100030").mkdir(parents=True)
        cats = {d["path"] for d in steam.scan_junk()}
        self.assertNotIn(str(self.root / "steamapps/downloading/5100030"), cats)


class MoveEdges(Base):
    def setUp(self):
        super().setUp()
        os.environ["DDG_FORCE_COPY"] = "1"
        self.g = self.root / "steamapps/common/Star Courier"

    def move(self, appid="5100010", target=None):
        return steam.move_game(steam.find_game(appid), str(target or self.drive("E") / "SteamLibrary"), self.job())

    def test_empty_folders_hidden_files_and_long_paths(self):
        (self.g / "empty/also_empty").mkdir(parents=True)
        (self.g / ".hidden").write_text("h")
        (self.g / "zero.bin").write_bytes(b"")
        deep = self.g
        for i in range(12):
            deep = deep / ("a_rather_long_folder_name_number_%02d" % i)
        deep.mkdir(parents=True)
        (deep / "deep_file.txt").write_text("deep")
        self.assertGreater(len(str(deep / "deep_file.txt")), 260)
        steps = self.move()
        self.assertTrue(all(s.ok for s in steps), steps)
        new = self.drive("E") / "SteamLibrary/steamapps/common/Star Courier"
        self.assertTrue((new / "empty/also_empty").is_dir())
        self.assertEqual((new / ".hidden").read_text(), "h")
        self.assertEqual((new / "zero.bin").read_bytes(), b"")
        self.assertEqual((new / deep.relative_to(self.g) / "deep_file.txt").read_text(), "deep")

    def test_links_inside_a_game_stop_the_copy_safely(self):
        os.symlink(str(self.base / "home"), str(self.g / "link_to_elsewhere"))
        steps = self.move()
        self.assertFalse(steps[-1].ok)
        self.assertIn("link or junction", steps[-1].detail)
        self.assertTrue((self.root / "steamapps/appmanifest_5100010.acf").exists())
        self.assertEqual([p for p in self.drive("E").rglob("*") if p.is_file()], [])

    def test_steam_started_during_copy_undoes_move(self):
        orig, n = steam._copy_file, [0]

        def start_steam(s, d, j, done):
            n[0] += 1
            if n[0] == 2:
                self.set_running(True)
            orig(s, d, j, done)
        steam._copy_file = start_steam
        try:
            steps = self.move()
        finally:
            steam._copy_file = orig
        self.assertIn("Steam was started", steps[-1].detail)
        self.assertTrue((self.root / "steamapps/appmanifest_5100010.acf").exists())
        self.assertTrue((self.g / "data.pak").exists())
        self.assertFalse((self.drive("E") / "SteamLibrary/steamapps/common/Star Courier").exists())
        self.assertFalse(steam._journal().exists())

    def test_steam_started_during_same_drive_move_undoes_it(self):
        os.environ.pop("DDG_FORCE_COPY")
        real = os.rename

        def rename(a, b):
            real(a, b)
            if "Moss Valley" in str(a) and "Games2" in str(b):
                self.set_running(True)
        steam.os.rename = rename
        try:
            steps = steam.move_game(steam.find_game("5100020"), str(self.drive("C") / "Games2"), self.job())
        finally:
            steam.os.rename = real
        self.assertIn("Steam was started", steps[-1].detail)
        self.assertTrue((self.root / "steamapps/common/Moss Valley/data.pak").exists())

    def test_compression_record_dropped_after_copy_kept_after_rename(self):
        steam._record("compressed", "5100010", {"saved": 5})
        steam._record("compressed", "5100020", {"saved": 7})
        steps = self.move()
        self.assertTrue(any(s.name == "Compression" for s in steps))
        self.assertNotIn("5100010", steam.records("compressed"))
        os.environ.pop("DDG_FORCE_COPY")
        steam.move_game(steam.find_game("5100020"), str(self.drive("C") / "Games2"), self.job())
        self.assertIn("5100020", steam.records("compressed"))  # a rename keeps the files as they were

    def test_double_start_refused(self):
        gate = threading.Event()
        r = jobs.Runner()
        r.start("slow", lambda j: (gate.wait(5), [])[1])
        with self.assertRaises(RuntimeError):
            r.start("move again", lambda j: [])
        gate.set()
        r.wait(5)

    @unittest.skipIf(AS_ROOT, "root can write anywhere")
    def test_unwritable_target(self):
        os.chmod(str(self.drive("E")), 0o555)
        try:
            steam._WRITE_CACHE.clear()
            self.assertIn("cannot write", self.move()[-1].detail)
            self.assertFalse([t for t in steam.move_targets(steam.find_game("5100010"))
                              if t["path"].startswith(str(self.drive("E")))][0]["ok"])
        finally:
            os.chmod(str(self.drive("E")), 0o755)


class WriteProbe(Base):
    def test_probe_leaves_nothing_and_is_cached(self):
        d = str(self.drive("E"))
        self.assertTrue(steam._can_write(d))
        self.assertEqual(os.listdir(d), [])
        steam._WRITE_CACHE[common.norm(d)] = (steam.time.time(), False)
        self.assertFalse(steam._can_write(d))
        self.assertTrue(steam._can_write(d, fresh=True))

    def test_status_does_not_probe_per_game(self):
        calls = [0]
        orig = os.mkdir

        def counting(p, *a, **k):
            if ".ddg-write-test" in str(p):
                calls[0] += 1
            return orig(p, *a, **k)
        steam.os.mkdir = counting
        steam._WRITE_CACHE.clear()
        try:
            steam.games_status()
        finally:
            steam.os.mkdir = orig
        self.assertLessEqual(calls[0], len(self.spec["drives"]))


class CompressEdges(Base):
    def test_uncompress_needs_space(self):
        g = steam.find_game("5100010")
        steam.compress_game(g, self.job())
        os.environ["DDG_FAKE_WIN_FREE_BYTES"] = "1000"
        self.assertIn("Not enough free space", steam.uncompress_game(g, self.job())[0].detail)

    def test_cancel_during_estimate(self):
        job = self.job()
        job.cancel_event.set()
        jobs.run_now(job, lambda j: steam.estimate_game(steam.find_game("5100010"), j))
        self.assertEqual(job.state, "cancelled")
        self.assertNotIn("5100010", steam.records("estimates"))

    @unittest.skipIf(AS_ROOT, "root can read anything")
    def test_unreadable_file_skipped_in_estimate(self):
        f = self.root / "steamapps/common/Star Courier/levels.dat"
        os.chmod(str(f), 0)
        try:
            self.assertTrue(steam.estimate_game(steam.find_game("5100010"), self.job())[0].ok)
        finally:
            os.chmod(str(f), 0o644)


class WebEdges(Base):
    def setUp(self):
        super().setUp()
        import http.client
        from ddg import webui
        self.http = http.client
        self.srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        self.srv.token = "tok"
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def req(self, method, path, body=b"", headers=None):
        c = self.http.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"X-Token": "tok", "Content-Type": "application/json"}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_dns_rebinding_host_refused(self):
        self.assertEqual(self.req("GET", "/", headers={"Host": "evil.example.com"})[0], 403)
        self.assertEqual(self.req("POST", "/api/junk/clean", b'{"ids":[]}', {"Host": "evil.example.com"})[0], 403)

    def test_post_without_token_and_bad_json(self):
        self.assertEqual(self.req("POST", "/api/junk/clean", b'{"ids":[]}', {"X-Token": ""})[0], 403)
        st, data = self.req("POST", "/api/games/move", b"{not json")
        self.assertIn(b"game not found", data)
        st, data = self.req("POST", "/api/games/move", b'["a list"]')
        self.assertIn(b"game not found", data)

    def test_games_busy_while_job_runs(self):
        from ddg.jobs import RUNNER
        gate = threading.Event()
        RUNNER.start("slow", lambda j: (gate.wait(5), [])[1])
        try:
            self.assertTrue(json.loads(self.req("GET", "/api/games")[1])["busy"])
            self.assertIn(b"still running", self.req("POST", "/api/quit", b"{}")[1])
        finally:
            gate.set()
            RUNNER.wait(5)


if __name__ == "__main__":
    unittest.main()


class ReviewerFindings(Base):
    """Regression tests for the bugs found in the independent review (0.1.1)."""

    def test_lock_is_per_thread_and_never_goes_negative(self):
        got, release, b_result = threading.Event(), threading.Event(), []

        def holder():
            with steam.job_lock() as mine:
                self.assertTrue(mine)
                got.set()
                release.wait(5)
        t = threading.Thread(target=holder)
        t.start()
        got.wait(5)
        with steam.job_lock() as mine:
            b_result.append(mine)
        release.set()
        t.join()
        self.assertEqual(b_result, [False])
        self.assertEqual(getattr(steam._LOCK, "depth", 0), 0)
        with steam.job_lock() as mine:
            self.assertTrue(mine)

    def test_recovery_from_another_thread_cannot_break_a_live_move(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        gate, inside = threading.Event(), threading.Event()
        orig = steam.add_library

        def slow(root, path):
            inside.set()
            gate.wait(5)
            return orig(root, path)
        steam.add_library = slow
        res = {}
        target = str(self.drive("E") / "SteamLibrary")
        t = threading.Thread(target=lambda: res.update(r=steam.move_game(steam.find_game("5100010"), target, self.job())))
        t.start()
        try:
            inside.wait(5)
            self.assertEqual(steam.recover_moves(), [])        # refused: someone else holds the lock
        finally:
            gate.set()
            t.join()
            steam.add_library = orig
        self.assertTrue(all(s.ok for s in res["r"]), res["r"])
        self.assertTrue((Path(target) / "steamapps/common/Star Courier/data.pak").exists())

    def test_old_copy_kept_if_new_copy_vanished(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        orig = steam.set_app_library

        def sabotage(root, appid, size, src, dst):
            steam._rm(Path(dst) / "steamapps/common/Star Courier")   # something deleted the new copy
            return orig(root, appid, size, src, dst)
        steam.set_app_library = sabotage
        try:
            steps = steam.move_game(steam.find_game("5100010"), str(self.drive("E") / "SteamLibrary"), self.job())
        finally:
            steam.set_app_library = orig
        self.assertTrue(any("safety stop" in s.detail for s in steps), steps)
        self.assertTrue((self.root / "steamapps/common/Star Courier/data.pak").exists())

    def test_unrecovered_journal_blocks_new_moves(self):
        steam._write_journal({"appid": "5100020", "name": "Moss Valley", "root": str(self.root),
                              "src": str(self.root / "steamapps"), "dst": str(self.drive("E") / "L/steamapps"),
                              "items": ["common/Moss Valley"], "mode": "copy", "phase": "cleanup"})
        orig = steam._rm

        def busy(path):
            raise PermissionError(13, "in use")
        steam._rm = busy
        (self.drive("E") / "L/steamapps/common/Moss Valley").mkdir(parents=True)
        try:
            steps = steam.move_game(steam.find_game("5100010"), str(self.drive("E") / "SteamLibrary"), self.job())
        finally:
            steam._rm = orig
        self.assertIn("earlier move", steps[-1].detail)
        self.assertTrue(steam._journal().exists())
        self.assertTrue((self.root / "steamapps/common/Star Courier/data.pak").exists())

    def test_shared_install_folder_refused(self):
        vdf.dump_file({"AppState": {"appid": "5100011", "name": "Star Courier Multiplayer", "StateFlags": "4",
                                    "installdir": "star courier" if os.name == "nt" else "Star Courier"}},
                      self.root / "steamapps/appmanifest_5100011.acf")
        os.environ["DDG_FORCE_COPY"] = "1"
        why = steam.move_game(steam.find_game("5100010"), str(self.drive("E") / "SteamLibrary"), self.job())[-1].detail
        self.assertIn("shares its folder", why)
        self.assertTrue((self.root / "steamapps/common/Star Courier").is_dir())

    def test_finished_rename_recovery_tidies_and_next_move_works(self):
        orig = steam.set_app_library
        steam.set_app_library = lambda *a: (_ for _ in ()).throw(KeyboardInterrupt("power cut"))
        try:
            with self.assertRaises(KeyboardInterrupt):
                steam.move_game(steam.find_game("5100020"), str(self.drive("C") / "Games2"), self.job())
        finally:
            steam.set_app_library = orig
        self.assertIn("finished", steam.recover_moves()[0].detail)
        self.assertEqual(list((self.root / "steamapps").glob("*" + steam.MOVING)), [])
        self.assertIn("5100020", self.apps()[common.norm(self.drive("C") / "Games2")])
        # leave a stale set-aside file behind on purpose: the next move must still work
        (self.drive("C") / "Games2/steamapps" / ("appmanifest_5100020.acf" + steam.MOVING)).write_text("old")
        steps = steam.move_game(steam.find_game("5100020"), str(self.root), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)

    def test_cancel_part_way_through_compress_is_reported_and_recorded(self):
        g = steam.find_game("5100010")
        job = self.job()
        orig = winplat.compact

        def compact_then_cancel(files, algo):
            out = orig(files, algo)
            job.cancel_event.set()
            return out
        from ddg.shelf import estimate
        old_gain = estimate.MIN_FILE_GAIN
        estimate.MIN_FILE_GAIN = -1      # make every file "worth it" so there are several batches
        winplat.compact = compact_then_cancel
        steam.winplat.compact = compact_then_cancel
        try:
            for i in range(150):
                (g.dir / ("extra_%03d.txt" % i)).write_bytes(b"compressible text " * 2000)
            jobs.run_now(job, lambda j: steam.compress_game(g, j))
        finally:
            winplat.compact = orig
            steam.winplat.compact = orig
            estimate.MIN_FILE_GAIN = old_gain
        self.assertIn("Cancelled part-way", job.steps[0].detail)
        self.assertIn("5100010", steam.records("compressed"))       # so Uncompress is offered

    def test_uncompress_errors_keep_the_record(self):
        g = steam.find_game("5100010")
        steam.compress_game(g, self.job())
        os.environ["DDG_FAKE_COMPACT_ERROR"] = "1"
        self.assertFalse(steam.uncompress_game(g, self.job())[0].ok)
        self.assertIn("5100010", steam.records("compressed"))

    def test_junctions_and_links_never_followed(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("keep")
        os.symlink(str(outside), str(self.root / "logs" / "linked_dir"))
        os.symlink(str(outside), str(self.root / "steamapps/common/Star Courier/mods_link"))
        ids = [d["id"] for d in steam.scan_junk() if d["category"] == "steam-cache" and d["path"].endswith("logs")]
        self.assertTrue(all(s.ok for s in steam.clean_junk(ids, self.job())))
        self.assertEqual((outside / "precious.txt").read_text(), "keep")
        from ddg.shelf import estimate
        names = [os.path.basename(p) for p, _ in estimate.walk_files(str(self.root / "steamapps/common/Star Courier"))]
        self.assertNotIn("precious.txt", names)
        steam._rm(self.root / "steamapps/common/Star Courier")
        self.assertEqual((outside / "precious.txt").read_text(), "keep")

    def test_library_list_backups_rotate_and_bad_bytes_refused(self):
        for _ in range(5):
            steam.write_libraryfolders(self.root, steam.read_libraryfolders(self.root, strict=True))
            steam.time.sleep(1.05)
        self.assertLessEqual(len(list((self.root / "config").glob("libraryfolders.vdf" + steam.BACKUP + "-*"))), 3)
        f = self.root / "config/libraryfolders.vdf"
        f.write_bytes(f.read_bytes().replace(b'"label"\t\t""', b'"label"\t\t"\xff\xfe"', 1))
        with self.assertRaises(ValueError):
            steam.read_libraryfolders(self.root, strict=True)
        self.assertIn("could not be read", steam.move_game(
            steam.find_game("5100010"), str(self.drive("E") / "SteamLibrary"), self.job())[-1].detail)

    def test_odd_token_header_and_frame_headers(self):
        import http.client
        from ddg import webui
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "tok"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.putrequest("GET", "/api/games")
            c.putheader("X-Token", "tök".encode("latin-1"))
            c.endheaders()
            r = c.getresponse()
            self.assertEqual(r.status, 403)
            r.read()
            c.close()
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("GET", "/")
            r = c.getresponse()
            self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
            r.read()
            c.close()
        finally:
            srv.shutdown()
            srv.server_close()

    def test_cli_ctrl_c_stops_cleanly(self):
        import signal
        import subprocess
        import time as _t
        src = str(Path(__file__).resolve().parent.parent / "src")
        env = dict(os.environ, PYTHONPATH=src, DDG_FORCE_COPY="1")
        p = subprocess.Popen([sys.executable, "-c",
                              "import sys,time; sys.path.insert(0, %r)\n"
                              "from ddg.shelf import steam\n"
                              "orig = steam._copy_file\n"
                              "def slow(*a):\n    time.sleep(0.3); orig(*a)\n"
                              "steam._copy_file = slow\n"
                              "from ddg.__main__ import main\n"
                              "sys.exit(main(['move-game', '5100010', %r]))" % (src, str(self.drive("E") / "SteamLibrary"))],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        _t.sleep(1.5)
        p.send_signal(signal.SIGINT)
        out, _ = p.communicate(timeout=60)
        self.assertNotEqual(p.returncode, 0, out)
        self.assertIn("Cancelled", out)
        self.assertTrue((self.root / "steamapps/appmanifest_5100010.acf").exists())
        self.assertFalse(steam._journal().exists())
