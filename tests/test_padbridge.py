# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ddg import airlock, padbridge as pb   # noqa: E402

SAMPLE = """I: Bus=0003 Vendor=045e Product=028e Version=0110
N: Name="Microsoft X-Box 360 pad"
H: Handlers=event5 js0
B: PROP=0
B: EV=20000b
B: KEY=7cdb000000000000 0 0 0 0
B: ABS=3003f

I: Bus=0003 Vendor=0b05 Product=1abe Version=0110
N: Name="Asus Keyboard"
H: Handlers=sysrq kbd event3
B: EV=120013
B: KEY=1000000000007 ff9f207ac14057ff febeffdfffefffff fffffffffffffffe

I: Bus=0003 Vendor=1209 Product=0dd6 Version=0001
N: Name="Deck Deck Goose Airlock Mouse"
H: Handlers=event9
B: KEY=70000 0 0 0 0
B: ABS=3
"""


def ev(t, c, v):
    return struct.pack(pb.EVENT_FMT, 0, 0, t, c, v)


class Parse(unittest.TestCase):
    def test_bits(self):
        got = pb._bits("7cdb000000000000 0 0 0 0")        # 5 words, biggest first: only the top word has bits
        self.assertIn(4 * 64 + 63 - 1, got)                 # 0x7c.. -> bit 62 of word index 4
        self.assertEqual(min(got), 4 * 64 + 48)
        self.assertEqual(pb._bits("3003f"), {0, 1, 2, 3, 4, 5, 16, 17})

    def test_finds_only_the_pad(self):
        pads = pb.find_pads(SAMPLE)
        self.assertEqual([p["name"] for p in pads], ["Microsoft X-Box 360 pad"])
        self.assertEqual(pads[0]["event"], "/dev/input/event5")
        self.assertIn(pb.BTN_SOUTH, pads[0]["key"])

    def test_own_virtual_devices_are_not_pads(self):
        d = {"name": "Deck Deck Goose Airlock Mouse", "key": {pb.BTN_SOUTH}, "abs": {0, 1}, "event": "/dev/input/event9"}
        self.assertFalse(pb.is_pad(d))

    def test_env_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(SAMPLE)
        os.environ["DDG_INPUT_DEVICES"] = f.name
        try:
            self.assertEqual(len(pb.find_pads()), 1)
        finally:
            del os.environ["DDG_INPUT_DEVICES"]
            os.unlink(f.name)


class Translate(unittest.TestCase):
    def setUp(self):
        self.out = []
        self.t = pb.Translator(lambda a, b, c: self.out.append((a, b, c)))

    def test_a_clicks_and_releases(self):
        self.t.feed(pb.EV_KEY, pb.BTN_SOUTH, 1)
        self.t.feed(pb.EV_KEY, pb.BTN_SOUTH, 0)
        self.assertEqual(self.out, [(1, pb.BTN_LEFT, 1), (1, pb.BTN_LEFT, 0)])

    def test_repeat_press_ignored(self):
        self.t.feed(pb.EV_KEY, pb.BTN_SOUTH, 1)
        self.t.feed(pb.EV_KEY, pb.BTN_SOUTH, 1)
        self.assertEqual(len(self.out), 1)

    def test_two_buttons_one_click_releases_when_both_up(self):
        self.t.feed(pb.EV_KEY, pb.BTN_SOUTH, 1)
        self.t.feed(pb.EV_KEY, pb.BTN_TR2, 1)
        self.t.feed(pb.EV_KEY, pb.BTN_SOUTH, 0)
        self.assertNotIn((1, pb.BTN_LEFT, 0), self.out)
        self.t.feed(pb.EV_KEY, pb.BTN_TR2, 0)
        self.assertIn((1, pb.BTN_LEFT, 0), self.out)

    def test_alt_tab_chord(self):
        self.t.feed(pb.EV_KEY, pb.BTN_SELECT, 1)
        self.t.feed(pb.EV_KEY, pb.BTN_SELECT, 0)
        self.assertEqual(self.out, [(1, pb.KEY_LEFTALT, 1), (1, pb.KEY_TAB, 1), (1, pb.KEY_TAB, 0), (1, pb.KEY_LEFTALT, 0)])

    def test_dpad_hat_gives_arrows(self):
        self.t.feed(pb.EV_ABS, pb.ABS_HAT0X, -1)
        self.t.feed(pb.EV_ABS, pb.ABS_HAT0X, 0)
        self.t.feed(pb.EV_ABS, pb.ABS_HAT0Y, 1)
        self.assertEqual(self.out, [(1, pb.KEY_LEFT, 1), (1, pb.KEY_LEFT, 0), (1, pb.KEY_DOWN, 1)])

    def test_dpad_buttons_give_arrows(self):
        self.t.feed(pb.EV_KEY, pb.BTN_DPAD_UP, 1)
        self.assertEqual(self.out, [(1, pb.KEY_UP, 1)])

    def test_trigger_is_a_click_with_hysteresis(self):
        r = (0, 255)
        self.t.feed(pb.EV_ABS, pb.ABS_RZ, 200, r)
        self.t.feed(pb.EV_ABS, pb.ABS_RZ, 120, r)      # between the thresholds: still down
        self.assertEqual(self.out, [(1, pb.BTN_LEFT, 1)])
        self.t.feed(pb.EV_ABS, pb.ABS_RZ, 10, r)
        self.assertEqual(self.out[-1], (1, pb.BTN_LEFT, 0))

    def test_small_stick_wobble_does_nothing(self):
        self.t.feed(pb.EV_ABS, pb.ABS_X, 3000, (-32768, 32767))
        self.assertFalse(self.t.moving())
        self.t.tick(0.0)
        self.t.tick(0.5)
        self.assertEqual(self.out, [])

    def test_stick_moves_mouse_in_proportion_to_time(self):
        self.t.feed(pb.EV_ABS, pb.ABS_X, 32767, (-32768, 32767))
        self.assertTrue(self.t.moving())
        self.t.tick(10.0)                               # first tick only starts the clock
        for i in range(1, 126):                         # one second at 125 Hz
            self.t.tick(10.0 + i * 0.008)
        dx = sum(v for (a, c, v) in self.out if a == pb.EV_REL and c == pb.REL_X)
        self.assertAlmostEqual(dx, pb.SPEEDS["normal"], delta=20)
        self.assertEqual([1 for (a, c, v) in self.out if c == pb.REL_Y], [])

    def test_stick_up_moves_up_and_right_stick_scrolls(self):
        self.t.feed(pb.EV_ABS, pb.ABS_Y, -32768, (-32768, 32767))
        self.t.feed(pb.EV_ABS, pb.ABS_RY, -32768, (-32768, 32767))     # push up = scroll up (positive wheel)
        self.t.tick(1.0)
        self.t.tick(1.5)
        ys = [v for (a, c, v) in self.out if c == pb.REL_Y]
        wheel = [v for (a, c, v) in self.out if c == pb.REL_WHEEL]
        self.assertTrue(ys and all(v < 0 for v in ys))
        self.assertTrue(wheel and all(v > 0 for v in wheel))

    def test_idle_clock_resets_so_no_jump(self):
        self.t.feed(pb.EV_ABS, pb.ABS_X, 32767, (-32768, 32767))
        self.t.tick(1.0)
        self.t.feed(pb.EV_ABS, pb.ABS_X, 0, (-32768, 32767))
        self.t.tick(1.008)
        self.out.clear()
        self.t.feed(pb.EV_ABS, pb.ABS_X, 32767, (-32768, 32767))
        self.t.tick(60.0)                               # a minute later: must not leap
        self.assertEqual(self.out, [])

    def test_release_all(self):
        self.t.feed(pb.EV_KEY, pb.BTN_SOUTH, 1)
        self.t.release_all()
        self.assertEqual(self.out[-1], (1, pb.BTN_LEFT, 0))

    def test_speed_setting(self):
        t = pb.Translator(lambda *a: None, "fast")
        self.assertEqual(t.pps, pb.SPEEDS["fast"])


class FakeSink:
    def __init__(self):
        self.events, self.synced, self.closed = [], 0, False

    def emit(self, a, b, c):
        self.events.append((a, b, c))

    def sync(self):
        self.synced += 1

    def close(self):
        self.closed = True


class BridgeRun(unittest.TestCase):
    def test_pipe_events_reach_the_sink(self):
        r, w = os.pipe()
        os.set_blocking(r, False)
        sink = FakeSink()
        b = pb.Bridge(sink, scan=lambda: [])
        b.add_fd(r, "Fake pad")
        os.write(w, ev(pb.EV_KEY, pb.BTN_SOUTH, 1) + ev(pb.EV_KEY, pb.BTN_EAST, 1) + ev(0, 0, 0))
        n = b.pump(0.2)
        self.assertEqual(n, 3)
        self.assertEqual(sink.events, [(1, pb.BTN_LEFT, 1), (1, pb.KEY_ESC, 1)])
        os.close(w)                                     # pad unplugged: buttons let go, fd dropped
        b.pump(0.2)
        self.assertEqual(b.fds, {})
        self.assertIn((1, pb.BTN_LEFT, 0), sink.events)

    def test_threaded_start_stop_and_late_pad(self):
        sink = FakeSink()
        found = []
        b = pb.Bridge(sink, scan=lambda: found)
        b.start()
        b.stop()
        self.assertTrue(sink.closed)

    def test_uinput_state(self):
        os.environ["DDG_UINPUT"] = "/nonexistent/uinput"
        try:
            self.assertEqual(pb.uinput_state(), "missing")
            b, note = pb.start()
            self.assertIsNone(b)
            self.assertIn("uinput", note)
        finally:
            del os.environ["DDG_UINPUT"]
        with tempfile.NamedTemporaryFile() as f:
            os.environ["DDG_UINPUT"] = f.name
            try:
                self.assertEqual(pb.uinput_state(), "ok")
            finally:
                del os.environ["DDG_UINPUT"]


class AirlockOptions(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.old = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME")}
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = self.home + "/c"
        os.environ["XDG_STATE_HOME"] = self.home + "/s"

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_defaults_and_set(self):
        c = airlock.load_config()
        self.assertEqual((c["pad"], c["pad_speed"]), ("on", "normal"))
        r = airlock.set_options(pad="off", pad_speed="fast")
        self.assertEqual((r["pad"], r["pad_speed"]), ("off", "fast"))
        self.assertIn("error", airlock.set_options(pad="maybe"))
        self.assertIn("error", airlock.set_options(pad_speed="warp"))

    def test_status_carries_pad_info(self):
        st = airlock.status()
        self.assertIn("uinput", st["pad"])
        self.assertEqual(st["options"]["pad"], "on")


if __name__ == "__main__":
    unittest.main()
