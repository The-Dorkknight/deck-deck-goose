# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Game Mode taps selected text instead of pressing buttons (0.6.8)."""
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from test_steam import SteamBase  # noqa: E402
from ddg import gamemode, webui  # noqa: E402


class Launcher(SteamBase):
    def test_firefox_gets_real_touch(self):
        for kind in ("firefox", "native-firefox"):
            t = gamemode.launcher_text("org.mozilla.firefox" if kind == "firefox" else "firefox", kind)
            self.assertIn("MOZ_USE_XINPUT2=1", t)
            self.assertIn('dom.w3c_touch_events.enabled", 1', t)
        self.assertIn("flatpak run --env=MOZ_USE_XINPUT2=1", gamemode.launcher_text("org.mozilla.firefox", "firefox"))

    def test_existing_launchers_are_refreshed_atomically(self):
        lp = gamemode.launcher_path("ddg")
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text("#!/bin/sh\n# old launcher from 0.6.7\n")
        old_inode = lp.stat().st_ino
        with mock.patch.object(gamemode, "find_browser", return_value=("org.mozilla.firefox", "firefox")):
            self.assertEqual(gamemode.refresh_launchers(), ["ddg"])
            self.assertIn("MOZ_USE_XINPUT2=1", lp.read_text())
            self.assertNotEqual(lp.stat().st_ino, old_inode)        # a running launcher keeps its old copy
            self.assertTrue(os.access(str(lp), os.X_OK))
            self.assertEqual(gamemode.refresh_launchers(), [])      # already current: untouched

    def test_missing_launchers_are_not_created(self):
        with mock.patch.object(gamemode, "find_browser", return_value=("org.mozilla.firefox", "firefox")):
            self.assertEqual(gamemode.refresh_launchers(), [])
        self.assertFalse(gamemode.launcher_path("ddg").exists())


class PageTaps(SteamBase):
    """In a real browser: a wobbly tap still presses, a real click presses once, a long drag does not press."""

    def test_taps(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright not installed")
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "t"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown); self.addCleanup(srv.server_close)
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--no-proxy-server"])
            pg = b.new_page(viewport={"width": 1000, "height": 700})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.add_init_script("try{localStorage.setItem('ddg-ok','1')}catch(e){}")
            pg.goto("http://127.0.0.1:%d/" % srv.server_address[1])
            pg.evaluate("""()=>{window.hits=0;const b=document.createElement('button');b.id='probe';b.textContent='Probe button';
                b.style='position:fixed;left:100px;top:300px;width:200px;height:60px;z-index:99';b.onclick=()=>window.hits++;document.body.appendChild(b);}""")
            # 1. CSS: the page does not select text; logs and inputs still do
            self.assertEqual(pg.evaluate("getComputedStyle(document.body).userSelect"), "none")
            self.assertEqual(pg.evaluate("(()=>{const p=document.createElement('pre');document.body.appendChild(p);return getComputedStyle(p).userSelect})()"), "text")
            # 2. a normal click presses exactly once
            pg.click("#probe")
            pg.wait_for_timeout(600)
            self.assertEqual(pg.evaluate("window.hits"), 1)
            # 3. a press and release with no click event (what a wobbly emulated tap can produce) still presses, once
            pg.evaluate("""()=>{const b=document.getElementById('probe');const o={bubbles:true,clientX:150,clientY:320,pointerId:1};
                b.dispatchEvent(new PointerEvent('pointerdown',o));b.dispatchEvent(new PointerEvent('pointerup',{...o,clientX:162,clientY:326}));}""")
            pg.wait_for_timeout(700)
            self.assertEqual(pg.evaluate("window.hits"), 2)
            # 4. a long drag is not a press
            pg.evaluate("""()=>{const b=document.getElementById('probe');b.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,clientX:110,clientY:310}));
                b.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,clientX:290,clientY:350}));}""")
            pg.wait_for_timeout(700)
            self.assertEqual(pg.evaluate("window.hits"), 2)
            # 5. a disabled button stays dead
            pg.evaluate("""()=>{const b=document.getElementById('probe');b.disabled=true;const o={bubbles:true,clientX:150,clientY:320};
                b.dispatchEvent(new PointerEvent('pointerdown',o));b.dispatchEvent(new PointerEvent('pointerup',o));}""")
            pg.wait_for_timeout(700)
            self.assertEqual(pg.evaluate("window.hits"), 2)
            # 6. a real mouse press with a small slide inside the button: one press, not two
            pg.evaluate("document.getElementById('probe').disabled=false")
            pg.mouse.move(130, 320); pg.mouse.down(); pg.mouse.move(145, 325, steps=4); pg.mouse.up()
            pg.wait_for_timeout(700)
            self.assertEqual(pg.evaluate("window.hits"), 3)
            b.close()
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()


class DropDowns(SteamBase):
    """In Game Mode a <select> opens an in-page picker (Firefox's own pop-up list can be untappable in gamescope)."""

    def run_page(self, game_mode: bool, touch: bool):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright not installed")
        if game_mode:
            os.environ["THISPC_GAMEMODE"] = "1"
        else:
            os.environ.pop("THISPC_GAMEMODE", None)
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "t"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown); self.addCleanup(srv.server_close)
        out = {}
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--no-proxy-server"])
            pg = b.new_page(viewport={"width": 1000, "height": 800}, has_touch=touch)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.add_init_script("try{localStorage.setItem('ddg-ok','1')}catch(e){}")
            pg.goto("http://127.0.0.1:%d/" % srv.server_address[1])
            pg.evaluate("""()=>{window.changed=[];const l=document.createElement('label');l.innerHTML='Pointer speed <select id="probe"><option value="slow">Slow</option><option value="normal" selected>Normal</option><option value="fast">Fast</option><option value="x" disabled>Nope</option></select>';
               l.style='position:fixed;left:40px;top:400px;z-index:40;background:#000;padding:10px';document.body.appendChild(l);
               document.getElementById('probe').addEventListener('change',e=>window.changed.push(e.target.value));}""")
            if touch:
                pg.tap("#probe")
            else:
                pg.click("#probe")
            pg.wait_for_timeout(300)
            out["shown"] = pg.is_visible("#picker")
            if out["shown"]:
                out["title"] = pg.inner_text("#picker h3")
                out["choices"] = pg.eval_on_selector_all("#picker .pk button", "bs=>bs.map(b=>b.textContent)")
                out["current"] = pg.inner_text("#picker .pk button.on")
                (pg.tap if touch else pg.click)("#picker .pk button:text-is('Fast')")
                pg.wait_for_timeout(300)
                out["after"] = pg.input_value("#probe")
                out["changed"] = pg.evaluate("window.changed")
                out["closed"] = not pg.is_visible("#picker")
                (pg.tap if touch else pg.click)("#probe")
                pg.wait_for_timeout(300)
                (pg.tap if touch else pg.click)("#picker .pk button:text-is('Cancel')")
                pg.wait_for_timeout(300)
                out["cancel_kept"] = pg.input_value("#probe")
                out["changed2"] = pg.evaluate("window.changed")
            b.close()
        out["errs"] = errs
        return out

    def check(self, o):
        self.assertTrue(o["shown"])
        self.assertEqual(o["title"], "Pointer speed")
        self.assertEqual(o["choices"], ["Slow", "Normal", "Fast", "Cancel"])     # disabled option left out
        self.assertEqual(o["current"], "Normal")
        self.assertEqual(o["after"], "fast")
        self.assertEqual(o["changed"], ["fast"])                                # change fired once
        self.assertTrue(o["closed"])
        self.assertEqual(o["cancel_kept"], "fast")
        self.assertEqual(o["changed2"], ["fast"])                               # Cancel changes nothing
        self.assertEqual(o["errs"], [])

    def test_game_mode_mouse_emulation(self):
        self.check(self.run_page(game_mode=True, touch=False))

    def test_touch_screen(self):
        self.check(self.run_page(game_mode=True, touch=True))

    def test_desktop_keeps_the_normal_list(self):
        o = self.run_page(game_mode=False, touch=False)
        self.assertFalse(o["shown"])
        self.assertEqual(o["errs"], [])
