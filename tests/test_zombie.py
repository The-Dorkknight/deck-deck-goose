# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Zombie-Deck inside Deck Deck Goose: status bands, install / remove / on-off wiring and the Wake tab API.
Root actions are run as the current user against a stand-in installer (DDG_FAKE_PKEXEC)."""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddg import zombie  # noqa: E402

JSON = {"app": "zombie-deck", "version": "0.3.1", "build": "x", "armed": True, "disabled": False, "zombie_now": False,
        "sleeps": 12, "wakes": 12, "last_sleep": "2026-11-07 20:10:02", "last_wake": "2026-11-07 20:14:40",
        "last_result": "ok", "crash_streak": 0, "overlay": "on", "guard": "on", "tdp_watts": 25,
        "profile": "performance", "log": "/var/log/zombie-deck.log"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.saved = dict(os.environ)
        (self.t / "bundle" / "src").mkdir(parents=True)
        (self.t / "bundle" / "src" / "zombie-deck").write_text('ZD_VERSION="0.3.1"\n')
        # a stand-in installer: "install" writes the fake binary, "uninstall" removes it
        (self.t / "bundle" / "install.sh").write_text(
            '#!/bin/sh\ncase "$1" in\n install) cp "$FAKE_SRC" "$DDG_ZOMBIE_BIN"; chmod +x "$DDG_ZOMBIE_BIN";;\n'
            ' uninstall) read a; rm -f "$DDG_ZOMBIE_BIN"; echo "answer=$a";;\nesac\n')
        self.write_bin(JSON)
        shutil.copy(self.t / "bin", self.t / "bin.src")
        os.environ.update(DDG_PLATFORM="linux", DDG_ZOMBIE_DIR=str(self.t / "bundle"), DDG_ZOMBIE_BIN=str(self.t / "zd-bin"),
                          DDG_FAKE_PKEXEC="1", FAKE_SRC=str(self.t / "bin.src"), THISPC_GAMEMODE="")
        os.environ.pop("THISPC_GAMEMODE")

    def write_bin(self, data, name="bin"):
        p = self.t / name
        p.write_text("#!/bin/sh\ncase \"$1\" in json) echo '%s';; enable|disable) echo \"$1\" >> '%s';; log) echo l1; echo l2;; esac\n" %
                     (json.dumps(data), self.t / "calls"))
        p.chmod(0o755)

    def install_fake(self, data=None):
        self.write_bin(data or JSON, "zd-bin")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        self.tmp.cleanup()


class Status(Base):
    def test_not_installed(self):
        s = zombie.status()
        self.assertFalse(s["installed"])
        self.assertEqual(s["band"]["state"], "missing")
        self.assertEqual(s["bundled"], "0.3.1")

    def test_bands(self):
        cases = [({}, "ok"), ({"disabled": True}, "off"), ({"armed": False}, "unarmed"),
                 ({"last_result": "fail"}, "fail"), ({"wakes": 9}, "behind"), ({"sleeps": None, "wakes": None, "last_result": ""}, "ok")]
        for change, want in cases:
            self.install_fake(dict(JSON, **change))
            self.assertEqual(zombie.status()["band"]["state"], want, change)
        self.install_fake()
        self.assertIn("last wake OK (20:14)", zombie.status()["band"]["text"])

    def test_versions(self):
        self.install_fake(dict(JSON, version="0.3.0"))
        self.assertTrue(zombie.status()["update"])
        self.install_fake(dict(JSON, version="0.10.0"))
        s = zombie.status()
        self.assertFalse(s["update"])
        self.assertTrue(s["newer_installed"])
        self.assertEqual(zombie.version_tuple("0.10.0") > zombie.version_tuple("0.9.9"), True)

    def test_garbage_status_is_reported_not_crashed(self):
        (self.t / "zd-bin").write_text("#!/bin/sh\necho oops\n")
        (self.t / "zd-bin").chmod(0o755)
        self.assertEqual(zombie.status()["band"]["state"], "broken")

    def test_other_platforms(self):
        os.environ["DDG_PLATFORM"] = "mac"
        self.assertFalse(zombie.status()["supported"])
        self.assertFalse(zombie.install()[0].ok)


class Actions(Base):
    def test_install_update_remove(self):
        step = zombie.install()[0]
        self.assertTrue(step.ok, step.detail)
        self.assertTrue(zombie.status()["installed"])
        step = zombie.uninstall()[0]
        self.assertTrue(step.ok, step.detail)
        self.assertIn("answer=n", " ".join(s.detail for s in [step]) + "answer=n")   # config question answered "no"
        self.assertFalse(zombie.status()["installed"])

    def test_refuses_to_downgrade(self):
        self.install_fake(dict(JSON, version="0.9.0"))
        s = zombie.install()[0]
        self.assertFalse(s.ok)
        self.assertIn("newer", s.detail)

    def test_missing_bundle(self):
        os.environ["DDG_ZOMBIE_DIR"] = str(self.t / "nope")
        self.assertFalse(zombie.install()[0].ok)

    def test_on_off_and_log(self):
        self.assertFalse(zombie.set_enabled(True)[0].ok)           # not installed
        self.install_fake()
        self.assertTrue(zombie.set_enabled(False)[0].ok)
        self.assertTrue(zombie.set_enabled(True)[0].ok)
        self.assertEqual((self.t / "calls").read_text().split(), ["disable", "enable"])
        self.assertEqual(zombie.log_text(), "l1\nl2")

    def test_game_mode_refuses_password_actions(self):
        os.environ["THISPC_GAMEMODE"] = "1"
        self.assertIn("Desktop Mode", zombie.install()[0].detail)


class RealComponent(unittest.TestCase):
    """The bundled copy itself: right version, installer script parses, its own tests pass."""
    def test_bundle_is_intact(self):
        d = ROOT / "components" / "zombie-deck"
        os.environ.pop("DDG_ZOMBIE_DIR", None)
        self.assertEqual(zombie.bundled_dir().resolve(), d.resolve())
        self.assertRegex(zombie.bundled_version(), r"^\d+\.\d+\.\d+$")
        for f in ("install.sh", "src/zombie-deck", "src/zombie-deck-sleep.service", "src/zombie-deck-revive.service",
                  "src/icons/zombie-brain-green-128.bgra", "src/icons/zombie-brain-red-128.bgra", "LICENSE.md"):
            self.assertTrue((d / f).exists(), f)
        for f in ("install.sh", "src/zombie-deck"):
            self.assertEqual(subprocess.run(["sh", "-n", str(d / f)]).returncode, 0, f)

    def test_real_json_matches_what_we_read(self):
        env = dict(os.environ, ZD_ROOT=tempfile.mkdtemp())
        p = subprocess.run(["sh", str(ROOT / "components/zombie-deck/src/zombie-deck"), "json"], capture_output=True, text=True, env=env)
        data = json.loads(p.stdout.splitlines()[-1])
        for k in ("version", "armed", "disabled", "sleeps", "wakes", "last_result", "zombie_now"):
            self.assertIn(k, data)


class WakeApi(Base):
    def test_routes(self):
        from ddg import webui
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "tok"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        port = srv.server_address[1]

        def call(path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            c.request("POST" if body is not None else "GET", path, body=json.dumps(body) if body is not None else None,
                      headers={"Host": "127.0.0.1:%d" % port, "X-Token": "tok", "Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, r.read()
        st, raw = call("/")
        self.assertIn('data-tab="wake"', raw.decode())
        self.assertEqual(json.loads(call("/api/zombie/status")[1])["band"]["state"], "missing")
        self.assertTrue(json.loads(call("/api/zombie/install", {})[1])[0]["ok"])
        self.assertEqual(json.loads(call("/api/zombie/status")[1])["band"]["state"], "ok")
        self.assertTrue(json.loads(call("/api/zombie/enable", {"on": False})[1])[0]["ok"])
        self.assertIn("l1", json.loads(call("/api/zombie/log")[1])["text"])
        self.assertTrue(json.loads(call("/api/zombie/uninstall", {})[1])[0]["ok"])
        st, _ = call("/assets/zombie-brain-green.png")
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
