"""Zombie-Deck tests - run with:  python3 -m unittest discover tests

They drive the real shell script against a fake /sys tree, so they prove the
logic (snapshot, detect, restore, double tap) without needing a handheld.
What they cannot prove: that the real kernel accepts the writes. That part
has only been reasoned about, not tested on an Ally yet - see README.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

import fake_sysfs

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ZD = os.path.join(PROJ, "src", "zombie-deck")


def rd(path):
    with open(path) as f:
        return f.read().strip()


class ZDCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="zd-")
        self.addCleanup(shutil.rmtree, self.root)

    def zd(self, *args, **env):
        e = dict(os.environ,
                 ZD_ROOT=self.root,
                 ZD_STATE_DIR=os.path.join(self.root, "run", "zombie-deck"),
                 ZD_CONF=os.path.join(self.root, "etc", "zombie-deck", "zombie-deck.conf"),
                 ZD_LOG=os.path.join(self.root, "var", "log", "zombie-deck.log"),
                 ZD_LIB=os.path.join(self.root, "var", "lib", "zombie-deck"),
                 ZD_SETTLE_SECONDS="0", ZD_DOUBLE_TAP_SECONDS="0 0", ZD_WIFI_WAIT_SECONDS="0",
                 PATH="/usr/bin:/bin")   # no ryzenadj / nmcli / logger in tests
        e.update(env)
        return subprocess.run(["sh", ZD, *args], env=e, capture_output=True, text=True)

    def sysp(self, *parts):
        return os.path.join(self.root, "sys", *parts)


class TestHealthy(ZDCase):
    def test_status_alive(self):
        fake_sysfs.build(self.root)
        r = self.zd("status")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("alive and well", r.stdout)
        self.assertNotIn("[ !! ]", r.stdout)

    def test_revive_changes_nothing_when_healthy(self):
        fake_sysfs.build(self.root)
        self.zd("snapshot")
        r = self.zd("revive")
        self.assertEqual(r.returncode, 0, r.stderr)      # healthy -> verdict ok -> exit 0
        self.assertNotIn("set ", r.stderr)
        self.assertIn("all good", r.stderr)
        self.assertIn("first pass: ok", r.stderr)
        self.assertEqual(rd(os.path.join(self.root, "run", "zombie-deck", "overlay")), "ok")


class TestZombie(ZDCase):
    def setUp(self):
        super().setUp()
        fake_sysfs.build(self.root)
        r = self.zd("snapshot")
        self.assertEqual(r.returncode, 0, r.stderr)
        # now the machine "wakes up" looking like a zombie
        shutil.rmtree(os.path.join(self.root, "sys"))
        fake_sysfs.build(self.root, zombie=True, mcu_present=False)

    def test_status_detects_zombie(self):
        r = self.zd("status")
        self.assertEqual(r.returncode, 2)
        out = r.stdout
        self.assertIn("ZOMBIE", out)
        for s in ("platform profile: balanced", "snapshot said 25W", "DPM: manual",
                  "parked cores", "power-save", "544 MHz", "MCU missing", "not connected"):
            self.assertIn(s, out, out)

    def test_dry_run_changes_nothing(self):
        r = self.zd("test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would set platform profile: 'balanced' -> 'performance'", r.stderr)
        self.assertEqual(rd(self.sysp("firmware", "acpi", "platform_profile")), "balanced")

    def test_revive_restores_everything(self):
        r = self.zd("revive")
        self.assertIn(r.returncode, (0, 1), r.stderr)   # 1 = still zombie (WiFi/MCU can't return in a fake tree)
        self.assertEqual(rd(self.sysp("firmware", "acpi", "platform_profile")), "performance")
        arm = self.sysp("class", "firmware-attributes", "asus-armoury", "attributes")
        self.assertEqual(rd(f"{arm}/ppt_pl1_spl/current_value"), "25")
        self.assertEqual(rd(f"{arm}/ppt_pl2_sppt/current_value"), "27")
        self.assertEqual(rd(f"{arm}/ppt_pl3_fppt/current_value"), "30")
        self.assertEqual(rd(self.sysp("class", "drm", "card1", "device", "power_dpm_force_performance_level")), "auto")
        cpu = self.sysp("devices", "system", "cpu")
        for i in range(1, 16):
            self.assertEqual(rd(f"{cpu}/cpu{i}/online"), "1", f"cpu{i} still parked")
        self.assertEqual(rd(f"{cpu}/cpu3/cpufreq/energy_performance_preference"), "balance_performance")
        self.assertEqual(rd(f"{cpu}/cpu3/cpufreq/scaling_max_freq"), "5100000")
        # controller missing, parent hub unknown in the fake tree -> no guessing, no USB writes
        self.assertIn("re-authorising", r.stderr)
        self.assertIn("not touching USB", r.stderr)
        self.assertEqual(rd(self.sysp("bus", "usb", "devices", "usb1", "authorized")), "1")
        self.assertNotIn("hub", [l for l in r.stderr.splitlines() if "set " in l])
        # overlay state file ends on ok/fail (fail here: WiFi stays down in the fake tree)
        self.assertEqual(rd(os.path.join(self.root, "run", "zombie-deck", "overlay")), "fail")
        # the guard learns that a revive happened this boot
        self.assertIn("revived=1", rd(os.path.join(self.root, "var", "lib", "zombie-deck", "boot-marker")))
        # three passes: settle + two double taps
        self.assertEqual(r.stderr.count("revive pass"), 3)
        # verdict right after the first pass (so the brain shows within seconds of wake)
        lines = r.stderr.splitlines()
        first = next(i for i, l in enumerate(lines) if "first pass:" in l)
        self.assertLess(first, next(i for i, l in enumerate(lines) if "revive pass 2" in l))
        lib = os.path.join(self.root, "var", "lib", "zombie-deck")
        self.assertEqual(rd(os.path.join(lib, "sleeps")), "1")
        self.assertEqual(rd(os.path.join(lib, "wakes")), "1")
        # log file written
        self.assertTrue(os.path.exists(os.path.join(self.root, "var", "log", "zombie-deck.log")))

    def test_fixed_tdp_from_config(self):
        conf = os.path.join(self.root, "etc", "zombie-deck", "zombie-deck.conf")
        with open(conf, "w") as f:
            f.write("ZD_TDP=20\nZD_PROFILE=performance\nZD_CONTROLLER=off\nZD_WIFI=off\n")
        r = self.zd("revive")
        self.assertIn(r.returncode, (0, 1), r.stderr)   # 1 = still zombie (WiFi/MCU can't return in a fake tree)
        arm = self.sysp("class", "firmware-attributes", "asus-armoury", "attributes")
        self.assertEqual(rd(f"{arm}/ppt_pl1_spl/current_value"), "20")
        self.assertEqual(rd(f"{arm}/ppt_pl2_sppt/current_value"), "22")
        self.assertEqual(rd(f"{arm}/ppt_pl3_fppt/current_value"), "25")
        self.assertNotIn("re-authorising", r.stderr)

    def test_everything_off_touches_nothing(self):
        conf = os.path.join(self.root, "etc", "zombie-deck", "zombie-deck.conf")
        with open(conf, "w") as f:
            f.write("ZD_TDP=off\nZD_PROFILE=off\nZD_GPU_DPM=off\nZD_CPU=off\nZD_CONTROLLER=off\nZD_WIFI=off\n")
        r = self.zd("revive")
        self.assertIn(r.returncode, (0, 1), r.stderr)   # 1 = still zombie (WiFi/MCU can't return in a fake tree)
        self.assertNotIn("set ", r.stderr)
        self.assertEqual(rd(self.sysp("firmware", "acpi", "platform_profile")), "balanced")


class TestNoSnapshot(ZDCase):
    def test_sanity_pass_without_snapshot(self):
        """Hook's pre step failed: revive still fixes the obvious things conservatively."""
        fake_sysfs.build(self.root, zombie=True)
        r = self.zd("revive")
        self.assertIn(r.returncode, (0, 1), r.stderr)   # 1 = still zombie (WiFi/MCU can't return in a fake tree)
        self.assertIn("no snapshot", r.stderr)
        cpu = self.sysp("devices", "system", "cpu")
        self.assertEqual(rd(f"{cpu}/cpu9/online"), "1")                       # un-parked
        self.assertEqual(rd(f"{cpu}/cpu0/cpufreq/energy_performance_preference"), "balance_performance")
        self.assertEqual(rd(f"{cpu}/cpu0/cpufreq/scaling_max_freq"), "5100000")
        self.assertEqual(rd(self.sysp("class", "drm", "card1", "device", "power_dpm_force_performance_level")), "auto")
        # without a snapshot there is nothing to restore the profile/TDP to: leave them
        self.assertEqual(rd(self.sysp("firmware", "acpi", "platform_profile")), "balanced")
        arm = self.sysp("class", "firmware-attributes", "asus-armoury", "attributes")
        self.assertEqual(rd(f"{arm}/ppt_pl1_spl/current_value"), "17")


class TestEdgeCases(ZDCase):
    def arm(self):
        return self.sysp("class", "firmware-attributes", "asus-armoury", "attributes")

    def conf(self, text):
        with open(os.path.join(self.root, "etc", "zombie-deck", "zombie-deck.conf"), "w") as f:
            f.write(text)

    def test_fixed_tdp_is_clamped_to_kernel_limits(self):
        fake_sysfs.build(self.root)
        self.conf("ZD_TDP=50\nZD_CONTROLLER=off\nZD_WIFI=off\n")   # max is 30 / 43
        r = self.zd("revive")
        self.assertEqual(rd(f"{self.arm()}/ppt_pl1_spl/current_value"), "30")
        self.assertEqual(rd(f"{self.arm()}/ppt_pl2_sppt/current_value"), "43")
        self.conf("ZD_TDP=3\nZD_CONTROLLER=off\nZD_WIFI=off\n")    # min is 7
        self.zd("revive")
        self.assertEqual(rd(f"{self.arm()}/ppt_pl1_spl/current_value"), "7")

    def test_garbage_config_falls_back_to_defaults(self):
        fake_sysfs.build(self.root)
        self.conf("ZD_TDP=lots\nZD_SETTLE_SECONDS=soon\nZD_DOUBLE_TAP_SECONDS='a b'\nZD_WIFI=off\nZD_CONTROLLER=off\n")
        r = self.zd("test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("would set TDP", r.stderr)   # 'lots' -> restore, and nothing to change

    def test_zombie_snapshot_carries_previous_tdp_forward(self):
        """Sleep twice without a fix: don't faithfully restore 17 W."""
        fake_sysfs.build(self.root)
        self.zd("snapshot")                               # 25 W
        shutil.rmtree(os.path.join(self.root, "sys"))
        fake_sysfs.build(self.root, zombie=True)          # now at the 17 W firmware default
        r = self.zd("snapshot")
        self.assertIn("keeping 25 W", r.stderr)
        state = rd(os.path.join(self.root, "run", "zombie-deck", "state"))
        self.assertIn("tdp_ppt_pl1_spl=25", state)
        self.assertIn("tdp_ppt_pl3_fppt=30", state)

    def test_stuck_saved_max_clock_is_ignored(self):
        fake_sysfs.build(self.root, zombie=True)
        self.zd("snapshot")                               # saves 544 MHz as the "max"
        self.conf("ZD_CONTROLLER=off\nZD_WIFI=off\n")
        r = self.zd("revive")
        self.assertIn("looks like a stuck value", r.stderr)
        self.assertEqual(rd(self.sysp("devices", "system", "cpu", "cpu0", "cpufreq", "scaling_max_freq")), "5100000")

    def test_newer_revive_takes_over(self):
        fake_sysfs.build(self.root, zombie=True)
        self.zd("snapshot")
        pidfile = os.path.join(self.root, "run", "zombie-deck", "revive_pid")
        # pretend another revive started after ours by planting a foreign pid before ours runs:
        # ours writes its own pid first, so simulate the race with a wrapper that overwrites it
        wrapper = os.path.join(self.root, "race.sh")
        with open(wrapper, "w") as f:
            f.write(f"#!/bin/sh\n(sleep 1; echo 999999 > {pidfile}) &\nexec sh {ZD} revive\n")
        e = dict(os.environ, ZD_ROOT=self.root, ZD_STATE_DIR=os.path.join(self.root, "run", "zombie-deck"),
                 ZD_LIB=os.path.join(self.root, "var", "lib", "zombie-deck"),
                 ZD_CONF="/nonexistent", ZD_LOG="", ZD_SETTLE_SECONDS="2", ZD_DOUBLE_TAP_SECONDS="4",
                 ZD_WIFI="off", ZD_CONTROLLER="off", PATH="/usr/bin:/bin")
        r = subprocess.run(["sh", wrapper], env=e, capture_output=True, text=True, timeout=30)
        self.assertIn("a newer revive took over", r.stderr)
        self.assertEqual(r.stderr.count("revive pass"), 0)

    def test_revive_needs_root_outside_fake_tree(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        r = subprocess.run(["sh", ZD, "revive"], env=dict(os.environ, ZD_ROOT="", ZD_CONF="/nonexistent"),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs root", r.stderr)


class TestIntegration(ZDCase):
    """Interfaces for Deck Deck Goose: json status and the sleep.d hook folder."""

    def test_json_status(self):
        import json
        fake_sysfs.build(self.root)
        self.zd("snapshot"); self.zd("revive")
        r = self.zd("json")
        self.assertEqual(r.returncode, 0, r.stderr)
        j = json.loads(r.stdout)
        self.assertEqual(j["app"], "zombie-deck")
        self.assertRegex(j["version"], r"^\d+\.\d+\.\d+$")
        self.assertIs(j["armed"], True)
        self.assertIs(j["disabled"], False)
        self.assertIs(j["zombie_now"], False)
        self.assertEqual((j["sleeps"], j["wakes"], j["last_result"]), (1, 1, "ok"))
        self.assertEqual((j["tdp_watts"], j["profile"]), (25, "performance"))
        self.zd("disable")
        j = json.loads(self.zd("json").stdout)
        self.assertIs(j["disabled"], True)
        self.assertIs(j["armed"], False)

    def test_json_before_any_sleep(self):
        import json
        fake_sysfs.build(self.root, zombie=True)
        j = json.loads(self.zd("json").stdout)
        self.assertIsNone(j["sleeps"])
        self.assertIs(j["zombie_now"], True)

    def test_sleep_hooks_run_pre_and_post(self):
        fake_sysfs.build(self.root)
        d = os.path.join(self.root, "etc", "zombie-deck", "sleep.d")
        os.makedirs(d)
        out = os.path.join(self.root, "hook.out")
        for name, body in (("40-airlock", f'echo "airlock $1" >> {out}'), ("50-broken", "exit 3"),
                           ("60-not-executable", f'echo nope >> {out}')):
            with open(os.path.join(d, name), "w") as f:
                f.write("#!/bin/sh\n" + body + "\n")
            if name != "60-not-executable":
                os.chmod(os.path.join(d, name), 0o755)
        env = {"ZD_HOOKS_DIR": d}
        self.zd("snapshot", **env)
        r = self.zd("revive", **env)
        self.assertEqual(rd(out).splitlines(), ["airlock pre", "airlock post"])
        self.assertIn("hook 50-broken post: failed (exit 3), ignored", r.stderr)
        self.assertIn("all good", r.stderr)          # a broken hook never spoils the revive
        self.assertIn("would run hook 40-airlock post", self.zd("test", **env).stderr)


class TestCrashGuard(ZDCase):
    def setUp(self):
        super().setUp()
        fake_sysfs.build(self.root)
        self.lib = os.path.join(self.root, "var", "lib", "zombie-deck")
        self.disabled = os.path.join(self.root, "etc", "zombie-deck", "disabled")

    def marker(self, revived=True):
        with open(os.path.join(self.lib, "boot-marker"), "w") as f:
            f.write("boot=x 2026-11-04\n" + ("revived=1\n" if revived else ""))

    def counter(self):
        return rd(os.path.join(self.lib, "crashes"))

    def test_clean_boot_and_shutdown(self):
        r = self.zd("guard")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.counter(), "0")
        self.assertTrue(os.path.exists(os.path.join(self.lib, "boot-marker")))
        self.zd("shutdown")
        self.assertFalse(os.path.exists(os.path.join(self.lib, "boot-marker")))

    def test_crash_without_revive_not_counted(self):
        self.marker(revived=False)
        r = self.zd("guard")
        self.assertIn("did not run in that boot", r.stderr)
        self.assertEqual(self.counter(), "0")

    def test_crash_after_revive_counted_once(self):
        self.marker()
        r = self.zd("guard")
        self.assertIn("suspect crash 1", r.stderr)
        self.assertEqual(self.counter(), "1")
        self.assertFalse(os.path.exists(self.disabled))

    def test_two_crashes_in_a_row_disable_when_nobody_answers(self):
        self.marker(); self.zd("guard")
        self.marker(); r = self.zd("guard")
        self.assertIn("two suspect crashes in a row", r.stderr)
        self.assertIn("DISABLED", r.stderr)
        self.assertTrue(os.path.exists(self.disabled))
        # while disabled, the sleep unit's snapshot/revive do nothing
        self.assertIn("disabled - not taking a snapshot", self.zd("snapshot").stderr)
        self.assertIn("disabled - not reviving", self.zd("revive").stderr)
        self.assertEqual(self.counter(), "0")
        # status says so, and enable re-arms
        self.assertIn("DISABLED", self.zd("status").stdout)
        self.zd("enable")
        self.assertFalse(os.path.exists(self.disabled))
        self.assertNotIn("disabled", self.zd("snapshot").stderr)

    def test_clean_shutdown_resets_the_streak(self):
        self.marker(); self.zd("guard")
        self.assertEqual(self.counter(), "1")
        self.zd("shutdown")                       # clean power-off
        self.zd("guard")                          # next boot: nothing pending
        self.assertEqual(self.counter(), "0")
        self.marker(); r = self.zd("guard")       # one crash again -> only 1, no prompt
        self.assertEqual(self.counter(), "1")
        self.assertNotIn("DISABLED", r.stderr)

    def test_flat_battery_crash_not_counted(self):
        self.marker()
        with open(self.sysp("class", "power_supply", "BAT0", "capacity"), "w") as f:
            f.write("2\n")
        r = self.zd("guard")
        self.assertIn("battery is flat", r.stderr)
        self.assertEqual(self.counter(), "0")

    def test_guard_off_in_config(self):
        with open(os.path.join(self.root, "etc", "zombie-deck", "zombie-deck.conf"), "w") as f:
            f.write("ZD_GUARD=off\n")
        self.marker(); self.zd("guard"); self.marker(); self.zd("guard")
        self.assertFalse(os.path.exists(self.disabled))


class TestOverlayX11(unittest.TestCase):
    """Run the real overlay against a real X server (Xvfb) and inspect its window."""

    def test_x11_window_has_gamescope_overlay_atom(self):
        import ctypes
        import ctypes.util
        from shutil import which
        if not which("Xvfb") or not ctypes.util.find_library("X11"):
            self.skipTest("Xvfb / libX11 not available")
        disp = ":93"
        xv = subprocess.Popen(["Xvfb", disp, "-screen", "0", "1280x800x24"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(xv.wait)
        self.addCleanup(xv.kill)
        time.sleep(1.5)
        sd = tempfile.mkdtemp(prefix="zd-ov-")
        self.addCleanup(shutil.rmtree, sd)
        with open(os.path.join(sd, "overlay"), "w") as f:
            f.write("init")
        env = dict(os.environ, DISPLAY=disp, ZD_STATE_DIR=sd, GAMESCOPE_WAYLAND_DISPLAY="gamescope-0")
        ov = subprocess.Popen(["python3", os.path.join(PROJ, "src", "zombie-deck-overlay"), "show"], env=env)
        self.addCleanup(lambda: ov.poll() is None and ov.kill())
        time.sleep(1.0)

        x = ctypes.CDLL(ctypes.util.find_library("X11"))
        V, UL, I = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int
        x.XOpenDisplay.restype = V; x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = UL; x.XDefaultRootWindow.argtypes = [V]
        x.XQueryTree.argtypes = [V, UL, ctypes.POINTER(UL), ctypes.POINTER(UL),
                                 ctypes.POINTER(ctypes.POINTER(UL)), ctypes.POINTER(ctypes.c_uint)]
        x.XInternAtom.restype = UL; x.XInternAtom.argtypes = [V, ctypes.c_char_p, I]
        x.XGetWindowProperty.argtypes = [V, UL, UL, ctypes.c_long, ctypes.c_long, I, UL, ctypes.POINTER(UL),
                                         ctypes.POINTER(I), ctypes.POINTER(UL), ctypes.POINTER(UL),
                                         ctypes.POINTER(ctypes.POINTER(ctypes.c_long))]
        dpy = x.XOpenDisplay(disp.encode())
        self.assertTrue(dpy)
        root = x.XDefaultRootWindow(dpy)
        atom = x.XInternAtom(dpy, b"GAMESCOPE_EXTERNAL_OVERLAY", 0)
        r, p, ch, n = UL(), UL(), ctypes.POINTER(UL)(), ctypes.c_uint()
        x.XQueryTree(dpy, root, ctypes.byref(r), ctypes.byref(p), ctypes.byref(ch), ctypes.byref(n))
        flagged = 0
        for i in range(n.value):
            t, f, ni, ba = UL(), I(), UL(), UL()
            d = ctypes.POINTER(ctypes.c_long)()
            x.XGetWindowProperty(dpy, ch[i], atom, 0, 1, 0, 0, ctypes.byref(t), ctypes.byref(f),
                                 ctypes.byref(ni), ctypes.byref(ba), ctypes.byref(d))
            if ni.value and d[0] == 1:
                flagged += 1
        self.assertEqual(flagged, 1, "overlay window with GAMESCOPE_EXTERNAL_OVERLAY=1 not found")
        with open(os.path.join(sd, "overlay"), "w") as f:
            f.write("ok")
        self.assertEqual(ov.wait(timeout=10), 0)          # green flash 3 s, then exits by itself


class TestPackaging(unittest.TestCase):
    def test_version_flag(self):
        r = subprocess.run(["sh", ZD, "--version"], capture_output=True, text=True)
        self.assertRegex(r.stdout, r"Zombie-Deck \d+\.\d+\.\d+ \(built \d{4}-\d\d-\d\d \d\d:\d\d\)")

    def test_launchers_exist_and_are_executable(self):
        for name in ("Zombie-Deck-Linux.sh", "Zombie-Deck-Mac.command", "install.sh",
                     "src/zombie-deck", "src/zombie-deck-overlay"):
            p = os.path.join(PROJ, name)
            self.assertTrue(os.path.exists(p), name)
            self.assertTrue(os.stat(p).st_mode & stat.S_IXUSR, f"{name} not executable")
        self.assertTrue(os.path.exists(os.path.join(PROJ, "Zombie-Deck-Windows.bat")))
        with open(os.path.join(PROJ, "Zombie-Deck-Windows.bat"), "rb") as f:
            self.assertIn(b"\r\n", f.read(), "Windows launcher must have CRLF line endings")

    def test_overlay_fallbacks_without_a_display(self):
        env = dict(os.environ, PATH="/usr/bin:/bin", ZD_PREVIEW="ok")
        env.pop("DISPLAY", None); env.pop("WAYLAND_DISPLAY", None)
        r = subprocess.run(["python3", os.path.join(PROJ, "src", "zombie-deck-overlay"), "show"],
                           env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run(["python3", os.path.join(PROJ, "src", "zombie-deck-overlay"), "prompt", "1", "2"],
                           env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1, "no UI must mean 'disable'")

    def test_overlay_icons_present_square_and_transparent(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        for colour in ("green", "red"):
            for size in (128, 256):
                p = os.path.join(PROJ, "src", "icons", f"zombie-brain-{colour}-{size}.png")
                self.assertTrue(os.path.exists(p), p)
                im = Image.open(p)
                self.assertEqual(im.size, (size, size))
                self.assertEqual(im.mode, "RGBA")
                self.assertEqual(im.getpixel((0, 0))[3], 0, "rounded corners must be transparent")

    def unit(self, name):
        with open(os.path.join(PROJ, "src", name)) as f:
            return "\n".join(l for l in f.read().splitlines() if not l.startswith("#"))

    def test_sleep_unit_is_wired_to_sleep_target(self):
        """The v0.1-0.2 bug: /etc/systemd/system-sleep is never run by systemd."""
        u = self.unit("zombie-deck-sleep.service")
        self.assertIn("WantedBy=sleep.target", u)
        self.assertIn("Before=sleep.target", u)
        self.assertIn("StopWhenUnneeded=yes", u)          # stops (= ExecStop) when sleep.target stops on wake
        self.assertIn("RemainAfterExit=yes", u)
        self.assertIn("ExecStart=-/usr/local/bin/zombie-deck snapshot", u)
        self.assertIn("ExecStop=-/usr/bin/systemctl --no-block restart zombie-deck-revive.service", u)
        r = self.unit("zombie-deck-revive.service")
        self.assertIn("ExecStart=-/usr/local/bin/zombie-deck revive", r)
        self.assertNotIn("[Install]", r)                   # never started at boot
        with open(os.path.join(PROJ, "install.sh")) as f:
            inst = f.read()
        self.assertIn("systemctl enable zombie-deck-sleep.service", inst)

    def test_install_removes_previous_version_first(self):
        with open(os.path.join(PROJ, "install.sh")) as f:
            inst = f.read()
        body = inst[inst.index("do_install() {"):]
        self.assertLess(body.index("remove_previous"), body.index("install -D"))
        rp = inst[inst.index("remove_previous() {"):inst.index("do_install() {")]
        for must in ('rm -f "$OLD_HOOK"', '"$SLEEP_UNIT"', '"$LIBEXEC"', "daemon-reload", "zombie-deck-revive-*"):
            self.assertIn(must, rp)
        self.assertNotIn('"$CONF"', rp.split("ok ")[0])     # config is kept

    def test_units_verify(self):
        from shutil import which
        if not which("systemd-analyze"):
            self.skipTest("systemd-analyze not installed")
        names = ["zombie-deck-sleep.service", "zombie-deck-revive.service", "zombie-deck-guard.service"]
        r = subprocess.run(["systemd-analyze", "verify", *[os.path.join(PROJ, "src", n) for n in names]],
                           capture_output=True, text=True)
        bad = [l for l in (r.stdout + r.stderr).splitlines()
               if "zombie-deck" in l and "/usr/local/bin/zombie-deck" not in l]
        self.assertEqual(bad, [], r.stdout + r.stderr)

    def test_raw_icons_match_pngs(self):
        for c in ("green", "red"):
            p = os.path.join(PROJ, "src", "icons", f"zombie-brain-{c}-128.bgra")
            self.assertEqual(os.path.getsize(p), 128 * 128 * 4)

    def test_guard_unit_cannot_block_boot(self):
        with open(os.path.join(PROJ, "src", "zombie-deck-guard.service")) as f:
            unit = "\n".join(l for l in f.read().splitlines() if not l.startswith("#"))
        self.assertIn("Type=simple", unit)
        self.assertIn("ExecStart=-", unit)
        self.assertIn("ExecStop=-", unit)
        self.assertNotIn("Requires=", unit)
        self.assertNotIn("Before=multi-user", unit)
        self.assertNotIn("Before=graphical", unit)

    def test_posix_sh_syntax(self):
        for name in ("src/zombie-deck", "install.sh", "Zombie-Deck-Linux.sh"):
            r = subprocess.run(["sh", "-n", os.path.join(PROJ, name)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
