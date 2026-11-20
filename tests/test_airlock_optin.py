# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Airlock is opt-in behind two sanity checks (0.6.11), because it may block Desktop Mode."""
import io
import json
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from test_steam import SteamBase  # noqa: E402
from ddg import airlock, appinstall, gamemode, vdf  # noqa: E402
from ddg import __main__ as cli  # noqa: E402


class OptIn(SteamBase):
    def setUp(self):
        super().setUp()
        os.environ["XDG_STATE_HOME"] = str(Path(self.tmp.name) / "state")
        os.environ["XDG_RUNTIME_DIR"] = str(Path(self.tmp.name) / "run")
        (Path(self.tmp.name) / "run").mkdir()
        os.environ.pop("THISPC_GAMEMODE", None)
        os.environ["XDG_CURRENT_DESKTOP"] = "KDE"
        self.user = self.root / "userdata/12345678/config"
        self.user.mkdir(parents=True)
        os.environ["THISPC_FAKE_STEAM_RUNNING"] = "1"

    def tiles(self):
        f = self.user / "shortcuts.vdf"
        if not f.exists():
            return []
        return [e["AppName"] for e in vdf.loads_binary(f.read_bytes())["shortcuts"].values()]

    def test_off_by_default_and_refuses_to_open(self):
        self.assertFalse(airlock.enabled())
        r = airlock.start(force=True, det={"session": "desktop"})
        self.assertEqual(r["code"], "AENA")
        self.assertIn("Enable Airlock", r["message"])

    def test_installing_ddg_adds_no_airlock_tile(self):
        gamemode.install()
        self.assertEqual(self.tiles(), ["Deck Deck Goose"])
        self.assertFalse(gamemode.launcher_path("airlock").exists())

    def test_both_checks_needed(self):
        self.assertIn("check 1", airlock.checks_passed(False, "AIRLOCK", "desktop"))
        self.assertIn("check 2", airlock.checks_passed(True, "airlok", "desktop"))
        self.assertIn("check 2", airlock.checks_passed(True, "", "desktop"))
        self.assertIn("Desktop Mode", airlock.checks_passed(True, "AIRLOCK", "game"))
        self.assertIsNone(airlock.checks_passed(True, " airlock ", "desktop"))

    def test_enable_adds_the_tile_and_disable_removes_it(self):
        gamemode.install()
        steps = airlock.enable(True, "AIRLOCK")
        self.assertTrue(all(s.ok for s in steps), [s.as_dict() for s in steps])
        self.assertTrue(airlock.enabled())
        self.assertEqual(self.tiles(), ["Deck Deck Goose", "Airlock"])
        self.assertIn("enabled_version", airlock.conf_path().read_text())
        steps = airlock.disable()
        self.assertTrue(all(s.ok for s in steps), [s.as_dict() for s in steps])
        self.assertFalse(airlock.enabled())
        self.assertEqual(self.tiles(), ["Deck Deck Goose"])

    def test_enable_refused_without_the_checks(self):
        steps = airlock.enable(True, "nope")
        self.assertFalse(steps[0].ok)
        self.assertFalse(airlock.enabled())
        os.environ["THISPC_GAMEMODE"] = "1"
        os.environ["XDG_CURRENT_DESKTOP"] = "gamescope"
        self.assertFalse(airlock.enable(True, "AIRLOCK")[0].ok)
        self.assertFalse(airlock.enabled())

    def test_disable_from_game_mode_keeps_tile_but_it_refuses(self):
        airlock.enable(True, "AIRLOCK")
        os.environ["THISPC_GAMEMODE"] = "1"
        os.environ["XDG_CURRENT_DESKTOP"] = "gamescope"
        steps = airlock.disable()
        self.assertTrue(any("refuses to open" in s.detail for s in steps))
        self.assertEqual(airlock.start(det={"session": "game", "kdialog": None})["code"], "AENA")

    def test_other_settings_survive_enable(self):
        airlock.set_options(pad_speed="fast")
        airlock.enable(True, "AIRLOCK", add_tile=False)
        self.assertEqual(airlock.load_config()["pad_speed"], "fast")

    def test_cli_questions(self):
        answers = iter(["y", "AIRLOCK"])
        out = []
        self.assertIsNone(airlock.ask_checks(inp=lambda _p: next(answers), out=out.append))
        self.assertTrue(any("MAY BLOCK DESKTOP MODE" in o for o in out))
        answers = iter(["n", "AIRLOCK"])
        self.assertIn("check 1", airlock.ask_checks(inp=lambda _p: next(answers), out=lambda *_: None))
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["airlock", "enable", "--checked"]), 0)
        self.assertTrue(airlock.enabled())

    def test_page(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright not installed")
        from ddg import webui
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "t"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown); self.addCleanup(srv.server_close)
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--no-proxy-server"])
            pg = b.new_page(viewport={"width": 1200, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.add_init_script("try{localStorage.setItem('ddg-ok','1')}catch(e){}")
            pg.goto("http://127.0.0.1:%d/" % srv.server_address[1])
            pg.click("nav.tabs button[data-tab=airlock]")
            pg.wait_for_function("document.getElementById('alrisk').textContent.length>50", timeout=8000)
            self.assertIn("MAY BLOCK DESKTOP MODE", pg.inner_text("#alrisk"))
            self.assertTrue(pg.is_disabled("#alopen"))
            # wrong word at check 2: not enabled
            seen = []
            replies = iter([True, True, "nope"])

            def dialog(d):
                seen.append(d.type)
                if d.type == "alert":
                    d.accept()
                    return
                v = next(replies)
                d.accept(v) if isinstance(v, str) else d.accept()
            pg.on("dialog", dialog)
            pg.click("#alenable")
            pg.wait_for_timeout(800)
            self.assertFalse(airlock.enabled())
            self.assertEqual(seen, ["confirm", "confirm", "prompt", "alert"])
            # right word: enabled, tile added, Open Airlock usable
            replies = iter([True, True, "AIRLOCK"])
            pg.click("#alenable")
            pg.wait_for_function("!document.getElementById('aldisable').hidden", timeout=15000)
            self.assertTrue(airlock.enabled())
            self.assertIn("Airlock", self.tiles())
            self.assertFalse(pg.is_disabled("#alopen"))
            b.close()
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
