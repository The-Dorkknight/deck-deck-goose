# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Game Mode tile (shortcuts.vdf, artwork, launcher) and the KDE on-screen keyboard."""
import os
import shutil
import stat
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_steam import SteamBase  # noqa: E402
from ddg import core, gamemode, osk, vdf  # noqa: E402

OTHER = {"appid": -1234567, "AppName": "Emulator", "Exe": '"/usr/bin/emu"', "StartDir": '"/usr/bin"',
         "icon": "", "ShortcutPath": "", "LaunchOptions": "-f", "IsHidden": 0, "AllowDesktopConfig": 1,
         "AllowOverlay": 1, "OpenVR": 0, "Devkit": 0, "DevkitGameID": "", "DevkitOverrideAppID": 0,
         "LastPlayTime": 1700000000, "FlatpakAppID": "", "tags": {"0": "favorite"}}


class BinaryVdf(unittest.TestCase):
    def test_roundtrip_and_layout(self):
        d = {"shortcuts": {"0": dict(OTHER, big=vdf.U64(2 ** 40), f=vdf.F32(0.5))}}
        b = vdf.dumps_binary(d)
        self.assertTrue(b.startswith(b"\x00shortcuts\x00\x000\x00"))
        self.assertTrue(b.endswith(b"\x08\x08\x08"))          # entry, shortcuts, root
        self.assertEqual(vdf.loads_binary(b), d)
        plain = vdf.dumps_binary({"shortcuts": {"0": OTHER}})     # tags last, like Steam writes it
        self.assertTrue(plain.endswith(b"\x08\x08\x08\x08"))

    def test_refuses_what_it_does_not_understand(self):
        for bad in (b"\x00shortcuts\x00\x09odd\x00", b"\x00shortcuts\x00\x01name\x00abc", b"\x00short"):
            with self.assertRaises(ValueError):
                vdf.loads_binary(bad)


class Tile(SteamBase):
    def setUp(self):
        super().setUp()
        self.user = self.root / "userdata/12345678/config"
        self.user.mkdir(parents=True)
        (self.root / "userdata/0").mkdir()            # anonymous folder: must be ignored
        self.sc = self.user / "shortcuts.vdf"
        self.sc.write_bytes(vdf.dumps_binary({"shortcuts": {"0": OTHER}}))

    def entries(self):
        return list(vdf.loads_binary(self.sc.read_bytes())["shortcuts"].values())

    def test_add_update_remove(self):
        os.environ["THISPC_FAKE_STEAM_RUNNING"] = "1"
        steps = gamemode.install()
        self.assertTrue(all(s.ok for s in steps), steps)
        names = [s.name for s in steps]
        self.assertIn("Close Steam", names)
        self.assertEqual(steps[-1].detail, "started again")
        e = self.entries()
        self.assertEqual(e[0], OTHER)                          # other shortcut untouched
        names_ = [x["AppName"] for x in e]
        self.assertEqual(names_, ["Emulator", "Deck Deck Goose", "Airlock"])
        for key, nm in (("ddg", "Deck Deck Goose"), ("airlock", "Airlock")):
            ours = [x for x in e if x["AppName"] == nm][0]
            self.assertEqual(ours["Exe"], '"%s"' % gamemode.launcher_path(key))
            appid = gamemode.shortcut_appid(ours["Exe"], nm)
            self.assertEqual(ours["appid"] & 0xFFFFFFFF, appid)
            for suffix in gamemode.TILES[key][2]:
                self.assertTrue((self.user / "grid" / ("%d%s" % (appid, suffix))).is_file(), suffix)
            self.assertTrue(os.stat(gamemode.launcher_path(key)).st_mode & stat.S_IXUSR)
            self.assertEqual(subprocess.run(["sh", "-n", str(gamemode.launcher_path(key))]).returncode, 0)
        st = gamemode.status()
        self.assertTrue(st["installed"] and st["airlock_installed"])
        self.assertTrue(list(self.user.glob("shortcuts.vdf.thispc-backup-*")))
        gamemode.install()                                     # again: no duplicates
        self.assertEqual(len(self.entries()), 3)
        steps = gamemode.remove()
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertEqual(self.entries(), [OTHER])
        self.assertFalse(list((self.user / "grid").glob("%d*" % appid)))
        self.assertFalse(gamemode.launcher_path("ddg").exists() or gamemode.launcher_path("airlock").exists())

    def test_old_this_pc_games_tile_is_replaced(self):
        old_exe = '"%s"' % (gamemode.launcher_path("ddg").parent / gamemode.LEGACY_LAUNCHER)
        items = vdf.loads_binary(self.sc.read_bytes())
        items["shortcuts"]["1"] = dict(OTHER, appid=-5, AppName="This PC Games", Exe=old_exe)
        self.sc.write_bytes(vdf.dumps_binary(items))
        gamemode.install()
        self.assertEqual([x["AppName"] for x in self.entries()], ["Emulator", "Deck Deck Goose", "Airlock"])

    def test_refusals(self):
        os.environ["THISPC_GAMEMODE"] = "1"
        self.assertIn("Desktop Mode", gamemode.install()[0].detail)
        os.environ.pop("THISPC_GAMEMODE")
        orig = gamemode.find_browser
        gamemode.find_browser = lambda: None
        try:
            steps = gamemode.install()
            self.assertTrue(any("No browser" in x.detail for x in steps), steps)
            self.assertEqual([x["AppName"] for x in self.entries()], ["Emulator", "Airlock"])   # Airlock needs no browser
        finally:
            gamemode.find_browser = orig
        self.sc.write_bytes(b"\x00shortcuts\x00\x09weird\x00")
        before = self.sc.read_bytes()
        steps = gamemode.install()
        self.assertTrue(any("could not be read" in s.detail for s in steps), steps)
        self.assertEqual(self.sc.read_bytes(), before)

    def test_no_shortcuts_file_yet(self):
        self.sc.unlink()
        self.assertTrue(all(s.ok for s in gamemode.install()))
        self.assertEqual([x["AppName"] for x in self.entries()], ["Deck Deck Goose", "Airlock"])

    def test_removing_this_pc_folder_keeps_tile_working(self):
        gamemode.install()
        core.remove_timer()
        self.assertTrue((core.data_dir() / "lib/ddg/__init__.py").exists())

    def test_launcher_end_to_end_with_stand_in_browser(self):
        """Real launcher + real server; a small script stands in for the browser:
        it loads the page, checks Game Mode layout, and presses Back to Steam."""
        core.copy_program()
        fake = Path(self.tmp.name) / "fakebrowser"
        fake.write_text(textwrap.dedent('''\
            #!%s
            import re, sys, json, urllib.request
            url = [a for a in sys.argv[1:] if a.startswith("--app=")][0][6:]
            html = urllib.request.urlopen(url, timeout=10).read().decode()
            tok = re.search(r'const TOKEN="([^"]+)"', html).group(1)
            assert "const GM=true" in html, "page not in Game Mode"
            open(sys.argv[0] + ".ok", "w").write("page ok")
            req = urllib.request.Request(url + "api/quit", data=b"{}", method="POST",
                headers={"X-Token": tok, "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
            import time; time.sleep(30)     # a browser stays open until it is closed
            ''' % sys.executable))
        os.chmod(str(fake), 0o755)
        script = Path(self.tmp.name) / "launch.sh"
        script.write_text(gamemode.launcher_text(str(fake), "native-chromium"))
        env = dict(os.environ, XDG_RUNTIME_DIR=self.tmp.name)
        env.pop("THISPC_GAMEMODE", None)
        p = subprocess.run(["sh", str(script)], env=env, timeout=60, capture_output=True, text=True)
        self.assertTrue(Path(str(fake) + ".ok").exists(), p.stdout + p.stderr +
                        (core.data_dir() / "gamemode.log").read_text())
        self.assertEqual(p.returncode, 0)
        self.assertFalse(list(Path(self.tmp.name).glob("ddg-gm-*.url")))


    def test_firefox_launcher_never_goes_offline(self):
        """Real Ally, Game Mode: Firefox said the local page needed the internet. The launcher must
        (re)write the profile's prefs so Firefox never switches itself to offline mode."""
        core.copy_program()
        prof = core.data_dir() / "ddg-gamemode-profile"
        prof.mkdir(parents=True)
        (prof / "user.js").write_text('user_pref("old", 1);\n')       # a profile from 0.6.1 or older
        fake = Path(self.tmp.name) / "fakefirefox"
        fake.write_text(textwrap.dedent('''\
            #!%s
            import re, sys, urllib.request
            args = sys.argv[1:]
            prof = args[args.index("--profile") + 1]
            prefs = open(prof + "/user.js").read()
            assert 'network.manage-offline-status", false' in prefs, prefs
            assert 'network.offline-mirrors-connectivity", false' in prefs, prefs
            assert '"old"' not in prefs
            assert "--kiosk" in args
            url = args[-1]
            html = urllib.request.urlopen(url, timeout=10).read().decode()
            tok = re.search(r'const TOKEN="([^"]+)"', html).group(1)
            open(sys.argv[0] + ".ok", "w").write("ok")
            req = urllib.request.Request(url + "api/quit", data=b"{}", method="POST",
                headers={"X-Token": tok, "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
            ''' % sys.executable))
        os.chmod(str(fake), 0o755)
        script = Path(self.tmp.name) / "launch.sh"
        script.write_text(gamemode.launcher_text(str(fake), "native-firefox"))
        env = dict(os.environ, XDG_RUNTIME_DIR=self.tmp.name)
        env.pop("THISPC_GAMEMODE", None)
        p = subprocess.run(["sh", str(script)], env=env, timeout=60, capture_output=True, text=True)
        log = (core.data_dir() / "gamemode-browser.log").read_text()
        self.assertTrue(Path(str(fake) + ".ok").exists(), p.stdout + p.stderr + log)
        self.assertIn("server http://127.0.0.1:", log)

    def test_install_app_in_one_step(self):
        from ddg import appinstall
        st = appinstall.status()
        self.assertFalse(st["installed"])
        steps = appinstall.install()
        self.assertTrue(all(s.ok for s in steps), steps)
        st = appinstall.status()
        self.assertTrue(st["installed"] and st["menu"] and st["tiles"] and st["airlock_tile"], st)
        self.assertFalse(st["update"])
        menu = appinstall.menu_path().read_text()
        self.assertIn("Exec=env PYTHONPATH=%s python3 -m ddg gui" % (core.data_dir() / "lib"), menu)
        self.assertTrue((core.data_dir() / "components" / "zombie-deck" / "install.sh").exists())
        self.assertFalse((core.data_dir() / "components" / "zombie-deck" / "tests").exists())
        # an older copy counts as needing an update
        init = core.data_dir() / "lib" / "ddg" / "__init__.py"
        init.write_text(init.read_text().replace(appinstall.__version__, "0.0.1-wip"))
        self.assertTrue(appinstall.status()["update"])
        steps = appinstall.uninstall()
        self.assertTrue(all(s.ok for s in steps), steps)
        st = appinstall.status()
        self.assertFalse(st["menu"] or st["tiles"])

    def test_install_app_refused_in_game_mode(self):
        from ddg import appinstall
        os.environ["THISPC_GAMEMODE"] = "1"
        self.assertIn("Desktop Mode", appinstall.install()[0].detail)


class OnScreenKeyboard(SteamBase):
    def setUp(self):
        super().setUp()
        self.apps = Path(self.tmp.name) / "apps"
        self.apps.mkdir()
        os.environ["THISPC_APPS_DIR"] = str(self.apps)
        os.environ.update(XDG_CURRENT_DESKTOP="KDE", XDG_SESSION_TYPE="wayland")
        self.rc = self.home / ".config/kwinrc"
        self.rc.parent.mkdir(parents=True, exist_ok=True)
        self.rc.write_text("[Desktops]\nNumber=2\n\n[Wayland]\nInputMethod[$e]=/old/onboard.desktop\nOther=1\n\n"
                           "[Xwayland]\nScale=1\n")

    def tearDown(self):
        for k in ("THISPC_FAKE_PKG", "THISPC_FAKE_PKG_RESULT"):
            os.environ.pop(k, None)
        super().tearDown()

    def test_missing_keyboard(self):
        steps = osk.enable()
        self.assertFalse(steps[0].ok)
        self.assertIn("No KDE touch keyboard is installed", steps[0].detail)
        self.assertIn("InputMethod[$e]=/old/onboard.desktop", self.rc.read_text())

    def test_on_and_off_keep_other_settings(self):
        kb = self.apps / "com.github.maliit.keyboard.desktop"
        kb.write_text("[Desktop Entry]\nName=Maliit\n")
        steps = osk.enable()
        self.assertTrue(all(s.ok for s in steps), steps)
        text = self.rc.read_text()
        self.assertIn("InputMethod=%s" % kb, text)
        self.assertNotIn("onboard", text)
        self.assertIn("Number=2", text)
        self.assertIn("Other=1", text)
        self.assertIn("[Xwayland]\nScale=1", text)
        self.assertTrue(osk.status()["enabled"])
        self.assertTrue((self.home / ".local/share/applications" / osk.SHOW_LAUNCHER).exists())
        self.assertTrue(self.rc.with_name("kwinrc.thispc-backup").exists())
        steps = osk.disable()
        self.assertTrue(steps[0].ok)
        self.assertNotIn("InputMethod", self.rc.read_text())
        self.assertIn("Other=1", self.rc.read_text())
        self.assertFalse(osk.status()["enabled"])

    def test_plasma_keyboard_found_and_preferred(self):
        """Nobara/Fedora KDE ship plasma-keyboard, not Maliit (the reported bug)."""
        (self.apps / "com.github.maliit.keyboard.desktop").write_text("[Desktop Entry]\n")
        pk = self.apps / "org.kde.plasma.keyboard.desktop"
        pk.write_text("[Desktop Entry]\nName=Plasma Keyboard\n")
        self.assertEqual(osk.find_keyboard(), pk)
        self.assertEqual(osk.status()["label"], "Plasma Keyboard")
        steps = osk.enable()
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertEqual(osk.read_key(self.rc, "Wayland", "InputMethod"), str(pk))
        self.assertEqual(osk.read_key(self.rc, "Wayland", "VirtualKeyboardEnabled"), "true")
        osk.disable()
        self.assertIsNone(osk.read_key(self.rc, "Wayland", "VirtualKeyboardEnabled"))

    def test_install_on_dnf_then_enable(self):
        os.environ["THISPC_FAKE_PKG"] = "dnf"
        os.environ["THISPC_FAKE_PKG_RESULT"] = "0:org.kde.plasma.keyboard.desktop"
        st = osk.status()
        self.assertTrue(st["can_install"])
        self.assertFalse(st["needs_reboot"])
        steps = osk.install_keyboard()
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertIn("plasma-keyboard", steps[0].detail)
        self.assertTrue(osk.status()["enabled"])
        self.assertEqual(osk.install_command("dnf", "plasma-keyboard")[:2], ["pkexec", "dnf"])

    def test_install_on_bazzite_needs_restart(self):
        os.environ["THISPC_FAKE_PKG"] = "rpm-ostree"
        os.environ["THISPC_FAKE_PKG_RESULT"] = "0"
        steps = osk.install_keyboard()
        self.assertTrue(steps[0].ok)
        self.assertIn("Restart", steps[0].detail)
        self.assertNotIn("InputMethod=", self.rc.read_text().replace("InputMethod[$e]", ""))

    def test_install_cancelled_or_failing(self):
        os.environ["THISPC_FAKE_PKG"] = "dnf"
        os.environ["THISPC_FAKE_PKG_RESULT"] = "126"
        steps = osk.install_keyboard()
        self.assertFalse(steps[0].ok)
        self.assertIn("cancelled", steps[0].detail)
        os.environ["THISPC_FAKE_PKG_RESULT"] = "1"
        steps = osk.install_keyboard()
        self.assertFalse(steps[0].ok)
        self.assertIn("plasma-keyboard, maliit-keyboard", steps[0].detail)
        os.environ["THISPC_GAMEMODE"] = "1"
        self.assertIn("Desktop Mode", osk.install_keyboard()[0].detail)
        os.environ.pop("THISPC_GAMEMODE")
        os.environ["THISPC_FAKE_PKG"] = ""
        self.assertIn("software centre", osk.install_keyboard()[0].detail)
        self.assertFalse(osk.status()["can_install"])
        self.assertEqual(osk.install_command("zypper", "plasma6-keyboard")[-1], "plasma6-keyboard")

    def test_no_kwinrc_yet_and_x11_note(self):
        self.rc.unlink()
        os.environ["XDG_SESSION_TYPE"] = "x11"
        (self.apps / "com.github.maliit.keyboard.desktop").write_text("[Desktop Entry]\n")
        steps = osk.enable()
        self.assertTrue(any("X11" in s.detail for s in steps))
        self.assertEqual(osk.read_key(self.rc, "Wayland", "InputMethod"),
                         str(self.apps / "com.github.maliit.keyboard.desktop"))


class GameModePage(SteamBase):
    def test_page_flag(self):
        import http.client
        import threading
        from ddg import webui
        for on in (False, True):
            if on:
                os.environ["THISPC_GAMEMODE"] = "1"
            srv = webui._Server(("127.0.0.1", 0), webui.Handler)
            srv.token = "tok"
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
                c.request("GET", "/")
                html = c.getresponse().read().decode()
                c.close()
                self.assertIn("const GM=%s" % ("true" if on else "false"), html)
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
