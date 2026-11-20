# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""0.6.12: Airlock stays inside its window (pad mouse paused while Steam's menu is in front), keeps its touch mode
when Steam resets it, and the touch lock with gestures (four fingers down: lock/unlock; three-finger double tap:
next mode)."""
import os
import struct
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from ddg import padbridge as pb, touchgesture as tg, touchmode  # noqa: E402
from test_touchmode import Base, HAVE_X  # noqa: E402
from test_padbridge import FakeSink, ev  # noqa: E402

AIRLOCK_APP = 2700000001


# ------------------------------------------------------------------ the gesture recogniser (no screen needed)
class Fingers:
    """Builds multi-touch protocol B frames for the Recognizer."""

    def __init__(self, rec):
        self.rec, self.t, self.out = rec, 0.0, []

    def frame(self, pts, dt=0.02):
        """pts: {slot: (x, y)} for fingers down this frame; slots missing are lifted."""
        self.t += dt
        down = getattr(self, "_down", set())
        for slot in sorted(down - set(pts)):
            self.rec.feed(tg.EV_ABS, tg.ABS_MT_SLOT, slot, self.t)
            self.rec.feed(tg.EV_ABS, tg.ABS_MT_TRACKING_ID, -1, self.t)
        for slot, (x, y) in pts.items():
            self.rec.feed(tg.EV_ABS, tg.ABS_MT_SLOT, slot, self.t)
            if slot not in down:
                self.rec.feed(tg.EV_ABS, tg.ABS_MT_TRACKING_ID, 100 + slot, self.t)
            self.rec.feed(tg.EV_ABS, tg.ABS_MT_POSITION_X, int(x), self.t)
            self.rec.feed(tg.EV_ABS, tg.ABS_MT_POSITION_Y, int(y), self.t)
        self._down = set(pts)
        g = self.rec.feed(tg.EV_SYN, tg.SYN_REPORT, 0, self.t)
        if g:
            self.out.append(g)
        return g

    def drag(self, n, x0, y0, dx, dy, steps=10, dt=0.03):
        for i in range(steps + 1):
            self.frame({k: (x0 + k * 80 + dx * i / steps, y0 + dy * i / steps) for k in range(n)}, dt)
        self.frame({}, dt)

    def tap(self, n, x=800, y=500, hold=0.1):
        self.frame({k: (x + k * 80, y) for k in range(n)}, 0.01)
        self.frame({k: (x + k * 80, y) for k in range(n)}, hold)
        self.frame({}, 0.01)

    def wait(self, s):
        self.t += s


class Gestures(unittest.TestCase):
    def setUp(self):
        self.f = Fingers(tg.Recognizer((0, 1919), (0, 1079)))

    def test_four_fingers_down(self):
        self.f.drag(4, 400, 200, 0, 400)
        self.assertEqual(self.f.out, ["four_down"])

    def test_four_fingers_up_or_sideways_or_short_is_nothing(self):
        self.f.drag(4, 400, 700, 0, -400)
        self.f.drag(4, 200, 400, 600, 100)
        self.f.drag(4, 400, 200, 0, 80)                       # too short
        self.f.drag(4, 400, 200, 0, 400, steps=60, dt=0.04)   # too slow (2.4 s)
        self.f.drag(3, 400, 200, 0, 400)                      # three fingers, not four
        self.assertEqual(self.f.out, [])

    def test_fingers_landing_one_by_one(self):
        f = self.f
        f.frame({0: (400, 200)})
        f.frame({0: (400, 200), 1: (480, 200)})
        f.frame({0: (400, 200), 1: (480, 200), 2: (560, 200), 3: (640, 200)})
        for i in range(1, 9):
            f.frame({k: (400 + k * 80, 200 + i * 50) for k in range(4)}, 0.03)
        f.frame({0: (400, 600)})                              # fingers leave one by one
        f.frame({})
        self.assertEqual(f.out, ["four_down"])

    def test_three_finger_double_tap(self):
        self.f.tap(3)
        self.f.wait(0.2)
        self.f.tap(3)
        self.assertEqual(self.f.out, ["three_double_tap"])
        self.f.wait(0.2)
        self.f.tap(3)                                         # a third tap starts a new pair
        self.assertEqual(self.f.out, ["three_double_tap"])

    def test_not_a_double_tap(self):
        self.f.tap(3); self.f.wait(1.0); self.f.tap(3)        # too far apart
        self.f.wait(1.0)
        self.f.tap(2); self.f.wait(0.1); self.f.tap(2)        # two fingers
        self.f.wait(1.0)
        self.f.tap(3, hold=0.6); self.f.wait(0.1); self.f.tap(3, hold=0.6)   # long presses
        self.f.wait(1.0)
        self.f.drag(3, 400, 400, 200, 0, steps=4, dt=0.02); self.f.wait(0.1); self.f.drag(3, 400, 400, 200, 0, steps=4, dt=0.02)
        self.assertEqual(self.f.out, [])

    def test_finds_only_touchscreens(self):
        text = """I: Bus=0018 Vendor=04f3 Product=4235
N: Name="ELAN9008:00 04F3:4235"
H: Handlers=mouse2 event7
B: PROP=2
B: EV=1b
B: KEY=400 0 0 0 0 0
B: ABS=3273800000000003

I: Bus=0018
N: Name="ASUE140D:00 04F3:31B9 Touchpad"
H: Handlers=mouse1 event8
B: PROP=5
B: ABS=2e0800000000003

I: Bus=0003
N: Name="Microsoft X-Box 360 pad"
H: Handlers=event20 js0
B: PROP=0
B: KEY=7cdb000000000000 0
B: ABS=3003f
"""
        found = tg.find_touchscreens(text)
        self.assertEqual([d["name"] for d in found], ["ELAN9008:00 04F3:4235"])
        self.assertEqual(found[0]["event"], "/dev/input/event7")


class WatcherPipe(unittest.TestCase):
    def test_reads_a_device_and_reports(self):
        r, w = os.pipe()
        got = []
        wt = tg.Watcher(got.append, scan=lambda: [])
        wt.fds[r] = tg.Recognizer((0, 1919), (0, 1079))
        import threading
        th = threading.Thread(target=wt.run, daemon=True)
        wt._open = lambda: None
        th.start()

        def frame(pts, prev):
            data = b""
            for s in sorted(prev - set(pts)):
                data += ev(tg.EV_ABS, tg.ABS_MT_SLOT, s) + ev(tg.EV_ABS, tg.ABS_MT_TRACKING_ID, -1)
            for s, (x, y) in pts.items():
                data += ev(tg.EV_ABS, tg.ABS_MT_SLOT, s)
                if s not in prev:
                    data += ev(tg.EV_ABS, tg.ABS_MT_TRACKING_ID, 50 + s)
                data += ev(tg.EV_ABS, tg.ABS_MT_POSITION_X, x) + ev(tg.EV_ABS, tg.ABS_MT_POSITION_Y, y)
            return data + ev(0, 0, 0)
        prev = set()
        for i in range(9):
            pts = {k: (300 + k * 80, 150 + i * 60) for k in range(4)}
            os.write(w, frame(pts, prev)); prev = set(pts); time.sleep(0.03)
        os.write(w, frame({}, prev))
        for _ in range(40):
            if got:
                break
            time.sleep(0.05)
        wt.stop()
        os.close(w)
        self.assertEqual(got, ["four_down"])


# ------------------------------------------------------------------ pad mouse paused while Steam's menu is in front
class PadPause(unittest.TestCase):
    def test_pause_lets_go_and_drops_input(self):
        r, w = os.pipe()
        os.set_blocking(r, False)
        sink = FakeSink()
        b = pb.Bridge(sink, scan=lambda: [])
        b.add_fd(r, "Fake pad")
        os.write(w, ev(pb.EV_KEY, pb.BTN_SOUTH, 1) + ev(0, 0, 0))      # A held: left button down
        b.pump(0.2)
        self.assertIn((1, pb.BTN_LEFT, 1), sink.events)
        b.set_paused(True)                                             # Steam's menu opened
        self.assertIn((1, pb.BTN_LEFT, 0), sink.events)                # let go at once
        n_before = len(sink.events)
        os.write(w, ev(pb.EV_KEY, pb.BTN_EAST, 1) + ev(pb.EV_ABS, pb.ABS_X, 30000) + ev(0, 0, 0))
        b.pump(0.2)
        b.pump(0.05)
        self.assertEqual([e for e in sink.events[n_before:] if e[0] != 0], [])   # nothing reaches the mouse
        b.set_paused(False)                                            # back in Airlock
        os.write(w, ev(pb.EV_KEY, pb.BTN_EAST, 0) + ev(pb.EV_KEY, pb.BTN_EAST, 1) + ev(0, 0, 0))
        b.pump(0.2)
        self.assertIn((1, pb.KEY_ESC, 1), sink.events)
        os.close(w)
        os.close(r)


# ------------------------------------------------------------------ focus, keeping the mode, the lock (real X server)
@unittest.skipUnless(HAVE_X, "Xvfb not installed")
class FocusAndLock(Base):
    def focus(self, appid):
        r = touchmode.Root(self.outer)
        if appid is None:
            r.lib.XDeleteProperty(r.dpy, r.root, r.focus_atom)
        else:
            v = (__import__("ctypes").c_ulong * 1)(appid)
            r.lib.XChangeProperty(r.dpy, r.root, r.focus_atom, 6, 32, 0, v, 1)
        r.lib.XSync(r.dpy, 0)
        r.close()

    def setUp(self):
        super().setUp()
        import ctypes
        touchmode.x11().XDeleteProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        self.focus(None)

    def test_steam_menu_gets_its_own_mode_and_the_pad_pauses(self):
        self.steam_sets(4)
        calls = []
        self.focus(AIRLOCK_APP)
        s = touchmode.Session(self.outer, start_mode=1, on_focus=calls.append)
        s.poll()
        self.assertEqual(s.ours, AIRLOCK_APP)
        self.assertEqual(self.value(), 1)
        self.focus(touchmode.STEAM_UI_APPID)                  # Steam button: Steam's menu in front
        s.poll()
        self.assertEqual(calls, [False])
        self.assertEqual(self.value(), 4)                     # Steam's own touch mode while its menu is up
        self.assertTrue(touchmode.parse_current(touchmode.current_file().read_text())["front"] is False)
        self.focus(AIRLOCK_APP)                               # back to Airlock
        s.poll()
        self.assertEqual(calls, [False, True])
        self.assertEqual(self.value(), 1)
        s.end()
        self.assertEqual(self.value(), 4)

    def test_another_game_in_front_also_counts_as_away(self):
        self.focus(AIRLOCK_APP)
        calls = []
        s = touchmode.Session(self.outer, start_mode=1, on_focus=calls.append)
        s.poll()
        self.focus(1234567)                                   # the game underneath brought to the front
        s.poll()
        self.assertEqual(calls, [False])
        s.end()

    def test_steam_resetting_the_mode_is_put_back(self):
        self.steam_sets(4)
        self.focus(AIRLOCK_APP)
        logs = []
        s = touchmode.Session(self.outer, start_mode=1, log=logs.append)
        for _ in range(3):
            self.steam_sets(4)                                # Steam fiddles while Airlock is in front
            s.poll()
            self.assertEqual(self.value(), 1)
        self.assertEqual(s.put_back, 3)
        self.assertTrue(any("put back Mouse" in m for m in logs))
        s.end()

    def test_no_choice_means_hands_off(self):
        self.steam_sets(4)
        s = touchmode.Session(self.outer, start_mode=None)
        self.steam_sets(6)
        s.poll()
        self.assertEqual(self.value(), 6)                     # nothing chosen: Steam's value is left alone
        s.end()

    def test_lock_refuses_button_and_cycle_gesture(self):
        s = touchmode.Session(self.outer, start_mode=1, locked=True)
        touchmode._write(touchmode.request_file(), "6")
        self.assertIsNone(s.poll())
        self.assertEqual(self.value(), 1)
        self.assertIn("Locked", touchmode.message_file().read_text())
        self.assertIsNone(s.gesture("three_double_tap"))
        self.assertEqual(self.value(), 1)
        s.gesture("four_down")                                # unlock
        self.assertFalse(s.locked)
        self.assertIn("Unlocked", touchmode.message_file().read_text())
        self.assertTrue(touchmode.parse_current(touchmode.current_file().read_text())["locked"] is False)
        self.assertEqual(s.gesture("three_double_tap"), 4)    # Mouse -> Native touch
        self.assertEqual(self.value(), 4)
        self.assertIn("Native touch", touchmode.message_file().read_text())
        s.gesture("four_down")                                # lock again
        self.assertTrue(s.locked)
        self.assertIn("Locked: Native touch", touchmode.message_file().read_text())
        s.end()

    def test_gestures_ignored_while_steam_menu_in_front(self):
        self.focus(AIRLOCK_APP)
        s = touchmode.Session(self.outer, start_mode=1, locked=False)
        s.poll()
        self.focus(touchmode.STEAM_UI_APPID)
        s.poll()
        self.assertIsNone(s.gesture("three_double_tap"))
        s.gesture("four_down")
        self.assertFalse(s.locked)
        s.end()


@unittest.skipUnless(HAVE_X, "Xvfb not installed")
class LockedButton(Base):
    def test_locked_tap_asks_nothing(self):
        import subprocess, ctypes
        s = touchmode.Session(self.outer, start_mode=1, locked=True)
        env = dict(os.environ, DISPLAY=":%d" % self.INNER, PYTHONPATH=str(ROOT / "src"))
        p = subprocess.Popen([sys.executable, "-m", "ddg.touchbutton"], env=env, stderr=subprocess.DEVNULL)
        try:
            time.sleep(2.5)                                   # it reads the lock within 2 s
            xt = ctypes.CDLL("libXtst.so.6")
            x = touchmode.x11()
            dpy = x.XOpenDisplay(b":%d" % self.INNER)
            x.XWarpPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_int]
            xt.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            x.XWarpPointer(dpy, 0, x.XDefaultRootWindow(dpy), 0, 0, 0, 0, 20, 20)
            xt.XTestFakeButtonEvent(dpy, 1, 1, 0)
            xt.XTestFakeButtonEvent(dpy, 1, 0, 0)
            x.XFlush(dpy)
            time.sleep(0.6)
            self.assertFalse(touchmode.request_file().exists())    # nothing asked of the supervisor
            self.assertEqual(self.value(), 1)
            x.XCloseDisplay(dpy)
        finally:
            p.terminate(); p.wait(5)
            s.end()


class NoNotificationsInGameMode(unittest.TestCase):
    def test_game_mode_never_notifies(self):
        from ddg import airlock
        import tempfile
        d = tempfile.mkdtemp()
        rec = Path(d) / "called"
        ns = Path(d) / "notify-send"
        ns.write_text("#!/bin/sh\necho \"$@\" >> %s\n" % rec)
        ns.chmod(0o755)
        airlock.notify({"session": "game", "notify_send": str(ns)}, "t", "b")
        airlock.notify({"session": "desktop", "notify_send": str(ns)}, "t", "b")
        time.sleep(0.5)
        self.assertEqual(rec.read_text().count("\n"), 1)        # only the desktop one


if __name__ == "__main__":
    unittest.main()
