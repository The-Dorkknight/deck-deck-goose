# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Airlock touch-mode button (0.6.7). Run against real X servers (Xvfb): one stands in for gamescope's Xwayland,
one for the desktop inside Airlock. Skipped where Xvfb is missing."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddg import airlock, touchmode  # noqa: E402

HAVE_X = bool(shutil.which("Xvfb"))


def xvfb(num: int, size: str = "1280x800x24"):
    p = subprocess.Popen(["Xvfb", ":%d" % num, "-screen", "0", size, "-nolisten", "tcp", "-noreset"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if os.path.exists("/tmp/.X11-unix/X%d" % num):
            return p
        time.sleep(0.05)
    p.kill()
    raise RuntimeError("Xvfb :%d did not start" % num)


@unittest.skipUnless(HAVE_X, "Xvfb not installed")
class Base(unittest.TestCase):
    OUTER, INNER = 87, 88

    @classmethod
    def setUpClass(cls):
        cls.xo = xvfb(cls.OUTER)
        cls.xi = xvfb(cls.INNER)

    @classmethod
    def tearDownClass(cls):
        for p in (cls.xo, cls.xi):
            p.terminate()
            p.wait(5)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ)
        os.environ.update(HOME=self.tmp.name, XDG_STATE_HOME=os.path.join(self.tmp.name, "state"),
                          XDG_CONFIG_HOME=os.path.join(self.tmp.name, "config"),
                          XDG_RUNTIME_DIR=os.path.join(self.tmp.name, "run"))
        os.makedirs(os.environ["XDG_RUNTIME_DIR"])
        self.outer = ":%d" % self.OUTER
        r = touchmode.Root(self.outer)
        r.lib.XDeleteProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        r.lib.XDeleteProperty(r.dpy, r.root, r.atom)          # start with "Steam never set it"
        r.lib.XSync(r.dpy, 0)
        r.close()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()

    def steam_sets(self, v):
        r = touchmode.Root(self.outer)
        r.set(v)
        r.close()

    def value(self):
        r = touchmode.Root(self.outer)
        try:
            return r.get()
        finally:
            r.close()


class RootProperty(Base):
    def test_get_set_round_trip(self):
        self.assertIsNone(self.value())
        self.steam_sets(4)
        self.assertEqual(self.value(), 4)

    def test_cycle_order(self):
        self.assertEqual([touchmode.next_mode(v) for v in (1, 4, 6, 2, None, 0)], [4, 6, 2, 1, 1, 1])


class SessionLife(Base):
    def test_restores_steams_value(self):
        self.steam_sets(4)
        s = touchmode.Session(self.outer)
        self.assertTrue(s.ok)
        touchmode._write(touchmode.request_file(), "1")
        self.assertEqual(s.poll(), 1)
        self.assertEqual(self.value(), 1)
        self.assertEqual(touchmode.current_file().read_text(), "1")
        s.end()
        self.assertEqual(self.value(), 4)                     # Game Mode gets Steam's own mode back
        self.assertFalse(touchmode.restore_file().exists())
        s.end()                                               # twice is harmless

    def test_never_set_before_goes_back_to_gamescope_default(self):
        s = touchmode.Session(self.outer, start_mode=6)
        self.assertEqual(self.value(), 6)
        s.end()
        self.assertEqual(self.value(), touchmode.GAMESCOPE_DEFAULT)

    def test_crash_is_repaired_next_time(self):
        self.steam_sets(4)
        s = touchmode.Session(self.outer, start_mode=1)
        info = json.loads(touchmode.restore_file().read_text())
        info["pid"] = 999999                                  # that Airlock is gone
        touchmode.restore_file().write_text(json.dumps(info))
        s.root.close(); s.root = None                         # killed: never called end()
        self.assertEqual(self.value(), 1)
        self.assertTrue(touchmode.repair_stale())
        self.assertEqual(self.value(), 4)

    def test_repair_leaves_a_running_airlock_alone(self):
        self.steam_sets(4)
        s = touchmode.Session(self.outer, start_mode=1)
        self.assertFalse(touchmode.repair_stale())            # our own pid is alive
        self.assertEqual(self.value(), 1)
        s.end()

    def test_bad_requests_are_ignored(self):
        s = touchmode.Session(self.outer)
        for bad in ("", "x", "99", "-1", "1; rm -rf /"):
            touchmode._write(touchmode.request_file(), bad)
            self.assertIsNone(s.poll())
        s.end()

    def test_unreachable_display(self):
        s = touchmode.Session(":59")
        self.assertFalse(s.ok)
        self.assertIsNone(s.poll())
        s.end()


class ButtonWindow(Base):
    """The real button on the inner display, clicked with XTest, talking to a Session on the outer display."""

    def test_tap_cycles_and_shows_popup(self):
        self.steam_sets(4)
        s = touchmode.Session(self.outer)
        stop = threading.Event()

        def supervisor():
            while not stop.is_set():
                s.poll()
                time.sleep(0.05)
        t = threading.Thread(target=supervisor, daemon=True)
        t.start()
        env = dict(os.environ, DISPLAY=":%d" % self.INNER, PYTHONPATH=str(ROOT / "src"))
        p = subprocess.Popen([sys.executable, "-m", "ddg.touchbutton"], env=env, stderr=subprocess.PIPE)
        try:
            time.sleep(1.0)
            self.assertIsNone(p.poll(), p.stderr.read().decode() if p.poll() is not None else "")
            xt = ctypes.CDLL("libXtst.so.6")
            x = touchmode.x11()
            dpy = x.XOpenDisplay(b":%d" % self.INNER)
            x.XWarpPointer = x.XWarpPointer
            x.XWarpPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_int]
            xt.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            root = x.XDefaultRootWindow(dpy)
            x.XWarpPointer(dpy, 0, root, 0, 0, 0, 0, 20, 20)        # the button is at the top-left
            seen = []
            for _ in range(3):
                xt.XTestFakeButtonEvent(dpy, 1, 1, 0)
                xt.XTestFakeButtonEvent(dpy, 1, 0, 0)
                x.XFlush(dpy)
                for _ in range(60):
                    time.sleep(0.05)
                    if self.value() not in seen[-1:] and self.value() != (seen[-1] if seen else 4):
                        break
                seen.append(self.value())
            self.assertEqual(seen, [6, 2, 1])                     # Touch -> Trackpad -> Right-click -> Mouse
            shot = Path(self.tmp.name) / "inner.png"
            if shutil.which("import"):
                xt.XTestFakeButtonEvent(dpy, 1, 1, 0); xt.XTestFakeButtonEvent(dpy, 1, 0, 0); x.XFlush(dpy)
                time.sleep(0.6)
                subprocess.run(["import", "-display", ":%d" % self.INNER, "-window", "root", str(shot)], check=True)
                out = os.environ.get("DDG_TOUCH_SHOT")
                if out:
                    shutil.copy(str(shot), out)
            x.XCloseDisplay(dpy)
        finally:
            p.terminate()
            p.wait(5)
            p.stderr.close()
            stop.set()
            t.join(2)
            s.end()
        self.assertEqual(self.value(), 4)


@unittest.skipUnless(HAVE_X, "Xvfb not installed")
class NestedScriptGuard(unittest.TestCase):
    """The button must never be started on gamescope's own display."""

    def run_script(self, display, outer):
        tmp = tempfile.mkdtemp()
        fake = Path(tmp) / "bin"
        fake.mkdir()
        (fake / "dolphin").write_text("#!/bin/sh\nsleep 1.5\n")
        (fake / "dolphin").chmod(0o755)
        log = Path(tmp) / "started"
        py = Path(tmp) / "py"
        py.write_text("#!/bin/sh\necho \"$@\" >> %s\n" % log)
        py.chmod(0o755)
        env = dict(PATH="%s:/usr/bin:/bin" % fake, DDG_NESTED_KIND="lite", DDG_LITE_APPS="dolphin",
                   DDG_READY=str(Path(tmp) / "ready"), DDG_TOUCH_BUTTON="1", DDG_PY=str(py), DDG_SRC="/x",
                   DISPLAY=display, DDG_OUTER_DISPLAY=outer)
        subprocess.run(["bash", str(ROOT / "src/ddg/airlock_data/nested-session.sh")], env=env, timeout=20)
        return log.exists() and "ddg.touchbutton" in log.read_text()

    def test_started_on_the_nested_display(self):
        self.assertTrue(self.run_script(":5", ":0"))

    def test_not_started_on_gamescopes_display(self):
        self.assertFalse(self.run_script(":0", ":0"))

    def test_not_started_without_a_display(self):
        self.assertFalse(self.run_script("", ":0"))


@unittest.skipUnless(HAVE_X, "Xvfb not installed")
class LaunchIntegration(Base):
    """A whole Airlock run with a stand-in KWin: Steam's touch mode is restored afterwards."""

    def test_open_pick_close_restores(self):
        self.steam_sets(4)
        kwin = Path(self.tmp.name) / "kwin_wayland"
        kwin.write_text("#!/bin/sh\ntouch \"$DDG_READY\"\nsleep 2\n")
        kwin.chmod(0o755)
        det = {"kwin": str(kwin), "kwin_flags": {}, "display": self.outer, "session": "game"}
        cfg = airlock.load_config()
        cfg["pad"] = "off"
        cfg["touch"] = "1"                                    # last picked: Mouse

        def pick_trackpad():
            time.sleep(0.8)
            touchmode._write(touchmode.request_file(), "6")
        threading.Thread(target=pick_trackpad, daemon=True).start()
        seen = []
        orig_poll = touchmode.Session.poll

        def spy(sess):
            v = orig_poll(sess)
            seen.append(self.value())
            return v
        touchmode.Session.poll = spy
        try:
            code = airlock._launch_once("lite", det, cfg, "x11")
        finally:
            touchmode.Session.poll = orig_poll
        self.assertEqual(code, "A0OK")
        self.assertIn(1, seen)                                # started in the remembered mode
        self.assertIn(6, seen)                                # the button's pick was applied
        self.assertEqual(self.value(), 4)                     # and Steam's own mode is back afterwards
        self.assertEqual(airlock.load_config()["touch"], "6") # remembered for the next Airlock only
        self.assertIn("button on", airlock.read_state().get("touch", ""))


if __name__ == "__main__":
    unittest.main()
