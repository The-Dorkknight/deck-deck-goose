# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Airlock: planning, the fallback ladder with a stand-in KWin, locking, hooks, tile and API."""
import json
import os
import stat
import sys
import threading
import time
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_steam import SteamBase  # noqa: E402
from ddg import airlock, core  # noqa: E402

CFG = airlock.DEFAULTS
FULL = {"kwin": "/usr/bin/kwin_wayland", "plasmashell": "/usr/bin/plasmashell", "display": ":0",
        "session_select": "/usr/bin/steamos-session-select", "dbus_run_session": None, "session": "game",
        "kwin_flags": {}}


def script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class Pure(unittest.TestCase):
    def test_detect_session(self):
        self.assertEqual(airlock.detect_session({"XDG_CURRENT_DESKTOP": "gamescope"}), "game")
        self.assertEqual(airlock.detect_session({"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"}), "game")
        self.assertEqual(airlock.detect_session({"THISPC_GAMEMODE": "1"}), "game")
        self.assertEqual(airlock.detect_session({"XDG_CURRENT_DESKTOP": "KDE"}), "desktop")
        self.assertEqual(airlock.detect_session({}), "unknown")

    def test_plan_ladder_and_ram(self):
        modes, notes = airlock.plan_modes(FULL, 4000, (80, True), CFG)
        self.assertEqual(modes, ["full", "lite", "switch", "advise"])
        modes, notes = airlock.plan_modes(FULL, 1000, (80, True), CFG)
        self.assertEqual(modes, ["lite", "switch", "advise"])
        self.assertTrue(any(n.startswith("A5RM") for n in notes))
        modes, notes = airlock.plan_modes(FULL, 300, (80, True), CFG)
        self.assertEqual(modes, ["switch", "advise"])
        modes, notes = airlock.plan_modes(dict(FULL, kwin=None), 4000, (-1, False), CFG)
        self.assertEqual(modes, ["switch", "advise"])
        self.assertTrue(notes[0].startswith("A1KW"))
        modes, _ = airlock.plan_modes(dict(FULL, session_select=None), 4000, (-1, False), CFG)
        self.assertEqual(modes[-1], "advise")
        self.assertNotIn("switch", modes)
        no_switch = dict(CFG, allow_session_switch="no")
        self.assertNotIn("switch", airlock.plan_modes(FULL, 4000, (-1, False), no_switch)[0])
        _, notes = airlock.plan_modes(FULL, 4000, (5, True), CFG)
        self.assertTrue(any(n.startswith("A9BT") for n in notes))
        self.assertEqual(airlock.plan_modes(dict(FULL, display=""), 4000, (-1, False), CFG)[0][0], "switch")

    def test_advise_is_always_last_and_never_reboots(self):
        for mem in (-1, 0, 400, 9000):
            self.assertEqual(airlock.plan_modes({}, mem, (-1, False), CFG)[0], ["advise"])
        self.assertNotIn("reboot", (Path(airlock.__file__).read_text().split("def start")[1]).lower().replace("never reboots", ""))

    def test_kwin_command(self):
        flags = airlock.parse_kwin_help("--x11-display --socket --no-lockscreen --width --exit-with-session --xwayland")
        cmd = airlock.build_kwin_cmd(dict(FULL, kwin_flags=flags, dbus_run_session="/usr/bin/dbus-run-session"),
                                     1920, 1080, "/x/nested.sh")
        self.assertEqual(cmd[:3], ["/usr/bin/dbus-run-session", "--", "/usr/bin/kwin_wayland"])
        for tok in ("--x11-display", ":0", "--socket", "wayland-ddg", "--xwayland", "--no-lockscreen", "1920", "1080"):
            self.assertIn(tok, cmd)
        self.assertEqual(cmd[-2:], ["--exit-with-session", "/x/nested.sh"])
        old = airlock.build_kwin_cmd(dict(FULL, kwin_flags={"exit_with_session": False}), 800, 600, "/x/n.sh")
        self.assertEqual(old[-1], "/x/n.sh")
        self.assertNotIn("--exit-with-session", old)

    def test_backends_and_wayland_command(self):
        det = dict(FULL, gamescope_wayland="gamescope-0")
        self.assertEqual(airlock.backends(det, {"backend": "auto"}), ["wayland", "x11"])
        self.assertEqual(airlock.backends(det, {"backend": "x11"}), ["x11", "wayland"])
        self.assertEqual(airlock.backends(FULL, {"backend": "wayland"}), ["x11"])      # no gamescope socket
        cmd = airlock.build_kwin_cmd(det, 1280, 720, "/x/n.sh", "wayland")
        self.assertIn("--wayland-display", cmd)
        self.assertNotIn("--x11-display", cmd)
        cmd = airlock.build_kwin_cmd(det, 1280, 720, "/x/n.sh", "x11")
        self.assertEqual(cmd[cmd.index("--x11-display") + 1], ":0")

    def test_screen_size_from_panel_not_xwayland(self):
        """Real Ally: the desktop opened at a big virtual size and the icons were tiny."""
        with tempfile.TemporaryDirectory() as d:
            drm = Path(d)
            for name, st, modes in (("card1-DP-1", "disconnected", ""), ("card1-HDMI-A-1", "connected", "2560x1440\n"),
                                    ("card1-eDP-1", "connected", "1920x1080\n1280x720\n")):
                (drm / name).mkdir()
                (drm / name / "status").write_text(st + "\n")
                (drm / name / "modes").write_text(modes)
            with mock.patch.dict(os.environ, {"DDG_DRM_DIR": d}):
                self.assertEqual(airlock.panel_size(), (1920, 1080))          # the built-in screen wins
                self.assertEqual(airlock.screen_size({"resolution": "auto"}), (1280, 720))
                self.assertEqual(airlock.screen_size({"resolution": "native"}), (1920, 1080))
                self.assertEqual(airlock.screen_size({"resolution": "1600x900"}), (1600, 900))
                self.assertEqual(airlock.screen_size({"width": "800", "height": "600"}), (800, 600))
                (drm / "card1-eDP-1" / "modes").write_text("1920x1200\n")    # 16:10 screen keeps its shape
                self.assertEqual(airlock.screen_size({"resolution": "auto"}), (1280, 800))
                (drm / "card1-eDP-1" / "modes").write_text("1280x800\n")     # Steam Deck: already small
                self.assertEqual(airlock.screen_size({"resolution": "auto"}), (1280, 800))

    def test_drawing_detection(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "n.log"
            f.write_text("kwin_scene_opengl: OpenGL vendor string: AMD\n")
            self.assertEqual(airlock._drawing(f), "gpu")
            f.write_text("kwin_core: Initializing QPainter compositing\n")
            self.assertEqual(airlock._drawing(f), "software")
            f.write_text("OpenGL renderer string: llvmpipe (LLVM 17)\n")
            self.assertEqual(airlock._drawing(f), "software")

    def test_errcodes_documented(self):
        self.assertEqual(len(airlock.ERRCODES), 15)
        self.assertTrue(all(len(k) == 4 for k in airlock.ERRCODES))

    def test_data_files_ship(self):
        for n in ("nested-session.sh", "probe.sh", "ddg-airlock-sleep.sh"):
            p = airlock.DATA / n
            self.assertTrue(p.exists() and os.access(str(p), os.X_OK), n)
            self.assertIn("Required Notice: Copyright Matthew Armstrong", p.read_text())
        self.assertTrue((airlock.DATA / "hooks/mode-change.d/50-crateless-rgb.example").exists())


class Ladder(SteamBase):
    def setUp(self):
        super().setUp()
        self.t = Path(self.tmp.name)
        os.environ["XDG_RUNTIME_DIR"] = str(self.t / "run")
        os.environ["XDG_STATE_HOME"] = str(self.t / "state")
        os.environ["THISPC_GAMEMODE"] = "1"
        (self.t / "run").mkdir()
        # stand-in KWin: ignores its flags and runs the last argument (the nested session script)
        self.kwin = script(self.t / "kwin_wayland", 'for last; do :; done\nexec "$last"\n')
        self.app = script(self.t / "fakeapp", "sleep %s\n")
        self.old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.t) + os.pathsep + self.old_path
        core.config_dir().mkdir(parents=True, exist_ok=True)
        airlock.conf_path().write_text("[portal]\nlite_apps = fakeapp\nwatchdog_seconds = 4\n"
                                       "allow_session_switch = no\nmin_ram_full_mb = 999999\n")
        self.det = dict(FULL, kwin=self.kwin, plasmashell=None, session_select=None)
        # hook that records every mode it is called with
        hd = core.config_dir() / "hooks" / "mode-change.d"
        hd.mkdir(parents=True)
        self.rec = self.t / "hook.log"
        script(hd / "10-record", 'echo "$1" >> %s\n' % self.rec)
        # pretend process table and systemctl, so tests never touch real processes
        self.procs = self.t / "procs.json"
        self.procs.write_text("{}")
        self.units = self.t / "units.txt"
        self.units.write_text("")
        self.sc = script(self.t / "systemctl", 'if [ "$2" = is-active ]; then shift 2; for u; do '
                         'grep -qx "$u" %s && echo active || echo inactive; done; else shift 2; '
                         'for u; do echo "$u" >> %s.stopped; sed -i "/^$u$/d" %s; done; fi\n' % ((self.units,) * 3))
        os.environ["DDG_FAKE_PROCS"] = str(self.procs)
        os.environ["DDG_SYSTEMCTL"] = self.sc

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        for k in ("XDG_RUNTIME_DIR", "XDG_STATE_HOME", "THISPC_GAMEMODE", "DDG_FAKE_PROCS", "DDG_SYSTEMCTL"):
            os.environ.pop(k, None)
        super().tearDown()

    def app_sleeping(self, secs):
        script(self.t / "fakeapp", "sleep %s\n" % secs)

    def test_lite_opens_and_closes_cleanly(self):
        self.app_sleeping(2)
        r = airlock.start(det=self.det)
        self.assertEqual((r["code"], r["mode"]), ("A0OK", "lite"))
        self.assertEqual(self.rec.read_text().split(), ["portal", "game"])
        self.assertEqual(airlock.read_state()["status"], "closed")
        self.assertFalse(airlock.ready_path().exists())

    def table(self):
        return json.loads(self.procs.read_text())

    def test_failed_full_rung_leftovers_are_closed_before_lite(self):
        """The real-Ally bug: full counted as failed but left a Plasma desktop running on Game Mode's display."""
        conf = airlock.conf_path().read_text().replace("min_ram_full_mb = 999999", "min_ram_full_mb = 1")
        airlock.conf_path().write_text(conf)
        script(self.t / "plasmashell", "exit 0\n")
        # stand-in KWin: in full mode a stray shell + window manager escape, then the rung fails
        script(self.t / "kwin_wayland", 'for last; do :; done\nif [ "$DDG_NESTED_KIND" = full ]; then '
               'echo \'{"501": "plasmashell", "502": "kwin_x11"}\' > %s; echo plasma-plasmashell.service > %s; exit 1; fi\n'
               'exec "$last"\n' % (self.procs, self.units))
        self.app_sleeping(1.5)
        r = airlock.start(det=dict(self.det, plasmashell=str(self.t / "plasmashell")))
        self.assertEqual(r["tried"], ["full", "lite"])
        self.assertEqual(r["code"], "A0OK")
        self.assertEqual(self.table(), {})
        self.assertIn("plasma-plasmashell.service", (self.t / "units.txt.stopped").read_text())
        self.assertIn("ADST", airlock.log_file().read_text())
        self.assertIn("plasmashell 501", airlock.log_file().read_text())

    def test_game_mode_sweeps_old_leftovers_first(self):
        self.procs.write_text(json.dumps({"77": "plasmashell", "78": "dolphin"}))
        self.app_sleeping(1.2)
        airlock.start(det=self.det)
        self.assertEqual(self.table(), {"78": "dolphin"})    # a shell never belongs in Game Mode; dolphin was there before

    def test_desktop_mode_only_closes_what_airlock_started(self):
        self.procs.write_text(json.dumps({"10": "plasmashell", "11": "kwin_wayland", "12": "dolphin"}))
        self.units.write_text("plasma-plasmashell.service\n")
        script(self.t / "fakeapp", 'python3 - <<"E"\nimport json\np=%r\nd=json.load(open(p)); d["99"]="konsole"\n'
               'json.dump(d,open(p,"w"))\nE\nsleep 1.2\n' % str(self.procs))
        r = airlock.start(force=True, det=dict(self.det, session="desktop"))
        self.assertEqual(r["code"], "A0OK")
        self.assertEqual(self.table(), {"10": "plasmashell", "11": "kwin_wayland", "12": "dolphin"})
        self.assertFalse((self.t / "units.txt.stopped").exists())       # your real desktop's shell is left alone

    def test_cleanup_command(self):
        self.procs.write_text(json.dumps({"5": "plasmashell"}))
        with mock.patch.object(airlock, "detect_session", return_value="desktop"):
            self.assertIn("Nothing", airlock.cleanup()["message"])       # Desktop Mode, no record: hands off
        with mock.patch.object(airlock, "detect_session", return_value="game"):
            r = airlock.cleanup()
        self.assertEqual(r["closed"], ["plasmashell 5"])
        self.assertEqual(self.table(), {})

    def test_wayland_failure_falls_back_to_x11_in_the_same_mode(self):
        script(self.t / "kwin_wayland", 'for last; do :; done\nfor a; do [ "$a" = --wayland-display ] && '
               '{ echo "no xdg_wm_base"; exit 1; }; done\nexec "$last"\n')
        self.app_sleeping(1.2)
        r = airlock.start(det=dict(self.det, gamescope_wayland="gamescope-0"))
        self.assertEqual((r["code"], r["mode"], r["tried"]), ("A0OK", "lite", ["lite"]))
        self.assertEqual(airlock.read_state()["backend"], "x11")
        self.assertEqual((airlock.state_dir() / "backend").read_text(), "x11")
        text = airlock.log_file().read_text()
        self.assertIn("lite via wayland failed, trying x11", text)
        self.assertIn("no xdg_wm_base", (airlock.state_dir() / "nested-lite.log").read_text())   # both tries kept
        # next time X11 (the one that worked) goes first
        self.assertEqual(airlock.backends(dict(self.det, gamescope_wayland="gamescope-0"), {"backend": "auto"})[0], "x11")

    def test_options_saved(self):
        self.assertEqual(airlock.set_options(resolution="native", backend="x11"), {"resolution": "native", "backend": "x11",
                                                                                "pad": "on", "pad_speed": "normal",
                                                                            "touch_button": "on", "touch": "steam"})
        self.assertEqual(airlock.set_options(touch_button="off")["touch_button"], "off")
        self.assertIn("error", airlock.set_options(touch_button="maybe"))
        self.assertIn("error", airlock.set_options(touch="99"))
        self.assertEqual(airlock.set_options(touch="6")["touch"], "6")
        self.assertIn("min_ram", airlock.conf_path().read_text())        # the rest of portal.ini is kept
        self.assertIn("error", airlock.set_options(resolution="huge"))
        self.assertIn("error", airlock.set_options(backend="vnc"))
        self.assertEqual(airlock.status()["options"]["resolution"], "native")

    def test_quick_close_is_still_success(self):
        self.app_sleeping(1.2)          # exits right after it appeared: not a failure
        self.assertEqual(airlock.start(det=self.det)["code"], "A0OK")

    def test_compositor_dies_at_once(self):
        script(self.t / "kwin_wayland", "echo boom >&2\nexit 1\n")
        r = airlock.start(det=self.det)
        self.assertEqual(r["code"], "A3TO")                       # ladder ends at 'advise'
        self.assertEqual(r["tried"], ["lite", "advise"])
        self.assertIn("A4EX", airlock.log_file().read_text())
        self.assertIn("boom", airlock.log_file().read_text())
        self.assertEqual(airlock.read_state()["status"], "failed")

    def test_watchdog_kills_a_hung_start(self):
        script(self.t / "kwin_wayland", "sleep 60\n")
        airlock.conf_path().write_text(airlock.conf_path().read_text().replace("= 4", "= 1"))
        t0 = time.time()
        r = airlock.start(det=self.det)
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(r["tried"], ["lite", "advise"])
        self.assertIn("A3TO", airlock.log_file().read_text())

    def test_second_airlock_is_refused_and_lock_survives(self):
        with airlock.PortalLock():
            r = airlock.start(det=self.det)
            self.assertEqual(r["code"], "A6LK")
            with self.assertRaises(RuntimeError):
                airlock.PortalLock().__enter__()
            self.assertEqual(airlock.lock_path().read_text(), str(os.getpid()))   # first pid not wiped
        with airlock.PortalLock():
            pass                                                                 # released afterwards

    def test_desktop_mode_needs_force(self):
        d = dict(self.det, session="desktop")
        r = airlock.start(det=d)
        self.assertEqual(r["code"], "A8NG")
        self.assertIn("--force", r["message"])
        self.app_sleeping(1.5)
        self.assertEqual(airlock.start(force=True, det=d)["code"], "A0OK")

    def test_session_switch_asks_first_and_no_means_no(self):
        script(self.t / "kwin_wayland", "exit 1\n")
        sw = script(self.t / "steamos-session-select", "echo switched >> %s/sw.log\n" % self.t)
        airlock.conf_path().write_text(airlock.conf_path().read_text().replace("= no", "= yes"))
        det = dict(self.det, session_select=sw)               # no kdialog/zenity: counts as "no"
        r = airlock.start(det=det)
        self.assertEqual(r["tried"], ["lite", "switch", "advise"])
        self.assertFalse((self.t / "sw.log").exists())
        # a yes answer switches
        yes = script(self.t / "kdialog", "exit 0\n")
        r = airlock.start(det=dict(det, kdialog=yes))
        self.assertEqual(r["mode"], "switch")
        for _ in range(30):
            if (self.t / "sw.log").exists():
                break
            time.sleep(0.1)
        self.assertEqual((self.t / "sw.log").read_text().strip(), "switched")

    def test_stop_closes_an_open_portal(self):
        self.app_sleeping(30)
        out = {}
        th = threading.Thread(target=lambda: out.update(r=airlock.start(det=self.det)))
        th.start()
        for _ in range(60):
            if airlock.status()["portal_open"]:
                break
            time.sleep(0.2)
        st = airlock.status()
        self.assertTrue(st["portal_open"])
        self.assertEqual(st["indicator"]["colour"], "#f5a623")
        airlock.stop()
        th.join(15)
        self.assertFalse(th.is_alive())
        self.assertEqual(airlock.stop()["code"], "A0OK")      # safe when nothing is open
        self.assertFalse(airlock.status()["portal_open"])
        self.assertEqual(airlock.status()["indicator"]["colour"], "#22c55e")

    def test_broken_config_falls_back_to_defaults(self):
        airlock.conf_path().write_text("[portal\nnonsense")
        self.assertEqual(airlock.load_config()["watchdog_seconds"], "15")

    def test_log_stays_small(self):
        for _ in range(3):
            airlock.state_dir().mkdir(parents=True, exist_ok=True)
        airlock.log_file().write_text("line of old log text\n" * 20000)
        airlock.log("A0OK", "again")
        self.assertLess(airlock.log_file().stat().st_size, 300000)

    def test_back_entry_and_sleep_hook(self):
        os.environ["THISPC_GAMEMODE"] = ""                       # desktop side
        os.environ.pop("THISPC_GAMEMODE")
        step = airlock.write_back_launcher()
        self.assertTrue(step.ok)
        txt = airlock.back_launcher_path().read_text()
        self.assertIn("-m ddg airlock stop", txt)
        self.assertIn("Back to Game", txt)
        env = {"DDG_ZOMBIE_SLEEPD": str(self.t / "zd" / "sleep.d"), "DDG_UNIT_DIR": str(self.t / "units"),
               "DDG_LIBEXEC": str(self.t / "libexec"), "DDG_FAKE_PKEXEC": "1",
               "DDG_ZOMBIE_BIN": str(self.t / "zombie-deck")}
        os.environ.update(env)
        try:
            # 1) no Zombie-Deck: its own unit on sleep.target (never /etc/systemd/system-sleep)
            self.assertTrue(airlock.sleep_hook(True)[0].ok)
            self.assertEqual(airlock.sleep_hook_how(), "unit")
            unit = (self.t / "units" / airlock.SLEEP_UNIT_NAME).read_text()
            self.assertIn("WantedBy=sleep.target", unit)
            self.assertIn("Before=sleep.target", unit)
            self.assertIn("StopWhenUnneeded=yes", unit)
            self.assertIn("ExecStart=-%s pre" % (self.t / "libexec" / "ddg-airlock-sleep.sh"), unit)
            self.assertTrue(os.access(str(self.t / "libexec" / "ddg-airlock-sleep.sh"), os.X_OK))
            self.assertTrue(airlock.sleep_hook(False)[0].ok)
            self.assertFalse(airlock.sleep_hook_installed())
            self.assertFalse((self.t / "libexec" / "ddg-airlock-sleep.sh").exists())
            # 2) with Zombie-Deck: a drop-in in its sleep.d, no unit of our own
            (self.t / "zd" / "sleep.d").mkdir(parents=True)
            (self.t / "zombie-deck").write_text("#!/bin/sh\n")
            os.chmod(str(self.t / "zombie-deck"), 0o755)
            self.assertTrue(airlock.sleep_hook(True)[0].ok)
            self.assertEqual(airlock.sleep_hook_how(), "zombie-deck")
            self.assertTrue(os.access(str(self.t / "zd" / "sleep.d" / airlock.SLEEP_HOOK_NAME), os.X_OK))
            self.assertFalse((self.t / "units" / airlock.SLEEP_UNIT_NAME).exists())
            self.assertTrue(airlock.sleep_hook(False)[0].ok)
            self.assertFalse(airlock.sleep_hook_installed())
        finally:
            for k in env:
                os.environ.pop(k, None)

    def test_sleep_hook_script_closes_open_portal(self):
        home = self.t / "h" / "u" / ".local/state/deck-deck-goose"
        home.mkdir(parents=True)
        (home / "portal.json").write_text(json.dumps({"status": "closed", "pid": 1}, indent=1))
        import subprocess
        r = subprocess.run(["bash", "-n", str(airlock.DATA / "ddg-airlock-sleep.sh")])
        self.assertEqual(r.returncode, 0)

    def test_probe_runs_and_is_read_only(self):
        ok, text = airlock.probe()
        self.assertTrue(ok, text)
        self.assertIn("Airlock probe", text)
        self.assertTrue((self.home / "deck-deck-goose-probe.txt").exists())

    def test_cli_plan_and_status(self):
        import subprocess
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent / "src"))
        for sub in ("plan", "status"):
            p = subprocess.run([sys.executable, "-m", "ddg", "airlock", sub], capture_output=True, text=True,
                               env=env, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr)
            json.loads(p.stdout)

    def test_web_api(self):
        import http.client
        from ddg import webui
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "tok"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        try:
            def call(method, path, body=None, token=True):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                h = {"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json"}
                if token:
                    h["X-Token"] = "tok"
                c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
                r = c.getresponse()
                return r.status, r.read()
            st, raw = call("GET", "/api/airlock/status")
            self.assertEqual(st, 200)
            self.assertEqual(json.loads(raw)["indicator"]["colour"], "#22c55e")
            self.assertEqual(call("GET", "/api/airlock/status", token=False)[0], 403)
            self.assertIn("modes", json.loads(call("GET", "/api/airlock/plan")[1]))
            self.assertEqual(json.loads(call("POST", "/api/airlock/stop", {})[1])["code"], "A0OK")
            # pictures: served without a token, plain file names only
            st, raw = call("GET", "/assets/hero.webp", token=False)
            self.assertEqual((st, raw[:4]), (200, b"RIFF"))
            for bad in ("/assets/../core.py", "/assets/%2e%2e/core.py", "/assets/nothing.png", "/assets/.hidden"):
                self.assertEqual(call("GET", bad, token=False)[0], 404, bad)
            self.assertEqual(call("GET", "/assets/hero.webp", token=False)[0], 200)
            page = call("GET", "/", token=False)[1].decode()
            self.assertIn('id="band"', page)
            self.assertIn('data-tab="airlock"', page)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
