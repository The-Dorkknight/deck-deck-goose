# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Regression tests for bugs found by the independent GameShelf review that
also applied to this engine, plus the 'safe while Steam runs' rules (0.3.0)."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_steam import SteamBase  # noqa: E402
from ddg import jobs, steam, vdf  # noqa: E402


class ReviewFixes(SteamBase):
    def test_lock_is_per_thread(self):
        got, release, other = threading.Event(), threading.Event(), []

        def holder():
            with steam.job_lock() as mine:
                self.assertTrue(mine)
                got.set()
                release.wait(5)
        t = threading.Thread(target=holder)
        t.start()
        got.wait(5)
        with steam.job_lock() as mine:
            other.append(mine)
        release.set()
        t.join()
        self.assertEqual(other, [False])
        with steam.job_lock() as mine:
            self.assertTrue(mine)

    def test_recovery_from_another_thread_cannot_break_a_live_move(self):
        os.environ["THISPC_FORCE_COPY"] = "1"
        gate, inside = threading.Event(), threading.Event()
        orig = steam.add_library

        def slow(root, path):
            inside.set()
            gate.wait(5)
            return orig(root, path)
        steam.add_library = slow
        res = {}
        target = str(self.drive3 / "SteamLibrary")
        t = threading.Thread(target=lambda: res.update(r=steam.move_game(steam.find_game("4100010"), target, self.job())))
        t.start()
        try:
            inside.wait(5)
            self.assertEqual(steam.recover_moves(), [])
        finally:
            gate.set()
            t.join()
            steam.add_library = orig
        self.assertTrue(all(s.ok for s in res["r"]), res["r"])
        self.assertTrue((Path(target) / "steamapps/common/StarCourier/data.pak").exists())

    def test_old_copy_kept_if_new_copy_vanished(self):
        os.environ["THISPC_FORCE_COPY"] = "1"
        orig = steam.set_app_library

        def sabotage(root, appid, size, src, dst):
            steam._rm(Path(dst) / "steamapps/common/StarCourier")
            return orig(root, appid, size, src, dst)
        steam.set_app_library = sabotage
        try:
            steps = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        finally:
            steam.set_app_library = orig
        self.assertTrue(any("safety stop" in s.detail for s in steps), steps)
        self.assertTrue((self.root / "steamapps/common/StarCourier/data.pak").exists())

    def test_unrecovered_journal_blocks_new_moves(self):
        steam._write_journal({"appid": "4100020", "name": "Moss Valley", "root": str(self.root),
                              "src": str(self.root / "steamapps"), "dst": str(self.drive3 / "L/steamapps"),
                              "items": ["common/Moss Valley"], "mode": "copy", "phase": "cleanup"})
        (self.drive3 / "L/steamapps/common/Moss Valley").mkdir(parents=True)
        orig = steam._rm
        steam._rm = lambda p: (_ for _ in ()).throw(PermissionError(13, "in use"))
        try:
            steps = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        finally:
            steam._rm = orig
        self.assertIn("earlier move", steps[-1].detail)
        self.assertTrue((self.root / "steamapps/common/StarCourier/data.pak").exists())

    def test_shared_install_folder_refused(self):
        vdf.dump_file({"AppState": {"appid": "4100011", "name": "Star Courier Multiplayer", "StateFlags": "4",
                                    "installdir": "StarCourier"}}, self.root / "steamapps/appmanifest_4100011.acf")
        why = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())[-1].detail
        self.assertIn("shares its folder", why)

    def test_finished_rename_recovery_tidies(self):
        orig = steam.set_app_library
        steam.set_app_library = lambda *a: (_ for _ in ()).throw(KeyboardInterrupt("power cut"))
        try:
            with self.assertRaises(KeyboardInterrupt):
                steam.move_game(steam.find_game("4100010"), str(self.drive2 / "SteamLibrary"), self.job())
        finally:
            steam.set_app_library = orig
        self.assertIn("finished", steam.recover_moves()[0].detail)
        self.assertEqual(list((self.root / "steamapps").glob("*" + steam.MOVING)), [])
        apps = self.lib_apps()
        self.assertIn("4100010", apps[os.path.realpath(str(self.drive2 / "SteamLibrary"))])

    def test_steam_started_during_move_undoes_it(self):
        os.environ["THISPC_FORCE_COPY"] = "1"
        orig, n = steam._copy_file, [0]

        def start_steam(s, d, j, done):
            n[0] += 1
            if n[0] == 2:
                st = {"steam_running": True}
                (self.home / ".thispc-fake-state.json").write_text(json.dumps(st))
            orig(s, d, j, done)
        steam._copy_file = start_steam
        try:
            steps = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        finally:
            steam._copy_file = orig
        self.assertIn("Steam was started", steps[-1].detail)
        self.assertTrue((self.root / "steamapps/common/StarCourier/data.pak").exists())

    def test_library_list_backups_rotate_and_bad_bytes_refused(self):
        for _ in range(5):
            steam.write_libraryfolders(self.root, steam.read_libraryfolders(self.root, strict=True))
            time.sleep(1.05)
        self.assertLessEqual(len(list((self.root / "config").glob("libraryfolders.vdf" + steam.BACKUP + "-*"))), 3)
        f = self.root / "config/libraryfolders.vdf"
        f.write_bytes(f.read_bytes().replace(b'"label"\t\t""', b'"label"\t\t"\xff\xfe"', 1))
        with self.assertRaises(ValueError):
            steam.read_libraryfolders(self.root, strict=True)

    def test_cli_ctrl_c_stops_cleanly(self):
        src = str(Path(__file__).resolve().parent.parent / "src")
        env = dict(os.environ, PYTHONPATH=src, THISPC_FORCE_COPY="1")
        p = subprocess.Popen([sys.executable, "-c",
                              "import sys,time; sys.path.insert(0, %r)\n"
                              "from ddg import steam\n"
                              "orig = steam._copy_file\n"
                              "def slow(*a):\n    time.sleep(0.3); orig(*a)\n"
                              "steam._copy_file = slow\n"
                              "from ddg.__main__ import main\n"
                              "sys.exit(main(['move-game', '4100010', %r]))" % (src, str(self.drive3 / "SteamLibrary"))],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(1.5)
        p.send_signal(signal.SIGINT)
        out, _ = p.communicate(timeout=60)
        self.assertNotEqual(p.returncode, 0, out)
        self.assertIn("Cancelled", out)
        self.assertTrue((self.root / "steamapps/appmanifest_4100010.acf").exists())


class SteamRunningRules(SteamBase):
    """Game Mode: Steam is always running, so these must be safe with it on."""

    def setUp(self):
        super().setUp()
        os.environ["THISPC_FAKE_STEAM_RUNNING"] = "1"

    def test_compress_allowed_while_steam_runs_unless_game_in_use(self):
        os.environ["THISPC_FAKE_GAME_IN_USE"] = ""
        self.assertTrue(steam.compress_game(steam.find_game("4100010"), self.job())[0].ok)
        os.environ["THISPC_FAKE_GAME_IN_USE"] = "4100020"
        self.assertIn("running", steam.compress_game(steam.find_game("4100020"), self.job())[0].detail)

    def test_move_still_needs_steam_closed(self):
        self.assertIn("Close Steam", steam.move_game(steam.find_game("4100010"),
                                                      str(self.drive2 / "SteamLibrary"), self.job())[-1].detail)

    def test_junk_safe_subset(self):
        items = steam.scan_junk()
        self.assertTrue(all(d["steam_ok"] == (d["category"] in steam.STEAM_SAFE_JUNK) for d in items))
        mixed = [d["id"] for d in items if d["category"] in ("shader-orphan", "steam-cache")]
        steps = steam.clean_junk(mixed, self.job())
        self.assertFalse((self.root / "steamapps/shadercache/555").exists())
        self.assertTrue((self.root / "logs/content_log.txt").exists())   # held back while Steam runs
        self.assertTrue(all(s.ok for s in steps), steps)

    def test_real_in_use_check_sees_our_own_open_file(self):
        os.environ.pop("THISPC_FAKE_GAME_IN_USE", None)
        g = steam.find_game("4100010")
        self.assertFalse(steam.game_in_use(g))
        with open(str(g.dir / "levels.dat"), "rb"):
            self.assertTrue(steam.game_in_use(g))


class WebHeaders(SteamBase):
    def test_frame_and_odd_token(self):
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


if __name__ == "__main__":
    unittest.main()
