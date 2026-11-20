# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Airlock must never lock you out of Desktop Mode (0.6.10)."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddg import airlock, desktopguard  # noqa: E402

GAME_ENV = {"WAYLAND_DISPLAY": "gamescope-0", "DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "gamescope"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.env = dict(os.environ)
        os.environ.update(HOME=str(t), XDG_CONFIG_HOME=str(t / "config"), XDG_DATA_HOME=str(t / "data"),
                          XDG_STATE_HOME=str(t / "state"), XDG_RUNTIME_DIR=str(t / "run"),
                          DDG_FAKE_USERENV=str(t / "userenv.json"), DDG_SYSTEMCTL="/bin/false")
        (t / "run").mkdir()
        self.cfg = t / "config"
        self.cfg.mkdir()
        (self.cfg / "kwinoutputconfig.json").write_text('[{"name":"eDP-1","mode":"1920x1080@120"}]')
        (self.cfg / "plasma-org.kde.plasma.desktop-appletsrc").write_text("[Containments][1]\nlastScreen=0\n")
        (t / "data" / "kscreen").mkdir(parents=True)
        (t / "data" / "kscreen" / "abc123").write_text("real screen")
        (t / "userenv.json").write_text(json.dumps(GAME_ENV))
        self.t = t

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()

    def airlock_pollutes(self):
        """What a nested Plasma can do to the real settings and the user session."""
        (self.cfg / "kwinoutputconfig.json").write_text('[{"name":"WL-1","mode":"1280x720"}]')
        (self.cfg / "plasma-org.kde.plasma.desktop-appletsrc").write_text("[Containments][9]\nlastScreen=1\n")
        (self.cfg / "ksmserverrc").write_text("[Session: saved at previous logout]\ncount=3\n")
        (self.cfg / "session").mkdir()
        (self.cfg / "session" / "plasmashell_1").write_text("x")
        (self.t / "data" / "kscreen" / "nested999").write_text("airlock screen")
        env = dict(GAME_ENV, WAYLAND_DISPLAY="ddg-nested-0", XDG_CURRENT_DESKTOP="KDE", DDG_AIRLOCK="1")
        (self.t / "userenv.json").write_text(json.dumps(env))

    def assert_clean(self):
        self.assertEqual((self.cfg / "kwinoutputconfig.json").read_text(), '[{"name":"eDP-1","mode":"1920x1080@120"}]')
        self.assertIn("[Containments][1]", (self.cfg / "plasma-org.kde.plasma.desktop-appletsrc").read_text())
        self.assertFalse((self.cfg / "ksmserverrc").exists())                 # made inside Airlock: removed
        self.assertFalse((self.cfg / "session").exists())
        self.assertEqual(sorted(p.name for p in (self.t / "data" / "kscreen").iterdir()), ["abc123"])
        self.assertEqual(json.loads((self.t / "userenv.json").read_text()), GAME_ENV)
        self.assertFalse(desktopguard.manifest().exists())


class Guard(Base):
    def test_begin_end_puts_everything_back(self):
        self.assertTrue(desktopguard.begin())
        self.airlock_pollutes()
        fixed = desktopguard.end()
        self.assert_clean()
        self.assertTrue(any("kwinoutputconfig.json" in f for f in fixed))
        self.assertTrue(any("environment" in f for f in fixed))
        self.assertIn("ADGR", airlock.log_file().read_text())

    def test_nothing_changed_reports_nothing(self):
        desktopguard.begin()
        self.assertEqual(desktopguard.end(), [])
        self.assertEqual(desktopguard.end(), [])                            # twice is harmless

    def test_crashed_airlock_repaired_at_next_start(self):
        desktopguard.begin()
        self.airlock_pollutes()
        man = json.loads(desktopguard.manifest().read_text())
        man["pid"] = 999999                                                 # that Airlock is gone
        desktopguard.manifest().write_text(json.dumps(man))
        desktopguard.repair_stale()
        self.assert_clean()

    def test_running_airlock_is_left_alone(self):
        p = subprocess.Popen(["sleep", "5"])
        try:
            desktopguard.begin()
            man = json.loads(desktopguard.manifest().read_text())
            man["pid"] = p.pid
            desktopguard.manifest().write_text(json.dumps(man))
            self.airlock_pollutes()
            self.assertEqual(desktopguard.repair_stale(), [])
            self.assertIn("WL-1", (self.cfg / "kwinoutputconfig.json").read_text())
        finally:
            p.kill(); p.wait()

    def test_half_written_backup_is_never_used(self):
        desktopguard.backup_dir().mkdir(parents=True)
        (desktopguard.backup_dir() / "files").mkdir()                        # no manifest = not a backup
        self.assertEqual(desktopguard.end(), [])
        self.assertIn("eDP-1", (self.cfg / "kwinoutputconfig.json").read_text())


class WholeAirlock(Base):
    """A real Airlock run with a stand-in KWin that pollutes the settings: closed normally, and by Steam's Exit game."""

    def kwin(self, linger: float) -> Path:
        k = self.t / "kwin_wayland"
        k.write_text("""#!/bin/sh
printf '[{"name":"WL-1"}]' > "$XDG_CONFIG_HOME/kwinoutputconfig.json"
mkdir -p "$XDG_CONFIG_HOME/session" && echo x > "$XDG_CONFIG_HOME/session/plasmashell_1"
python3 - <<'PY'
import json,os
f=os.environ["DDG_FAKE_USERENV"]; e=json.load(open(f)); e.update(WAYLAND_DISPLAY="ddg-nested-0", XDG_CURRENT_DESKTOP="KDE", DDG_AIRLOCK="1")
json.dump(e, open(f,"w"))
PY
touch "$DDG_READY"
sleep %s
""" % linger)
        k.chmod(0o755)
        return k

    def test_normal_close(self):
        det = {"kwin": str(self.kwin(0.5)), "kwin_flags": {}, "display": ":0", "session": "desktop"}
        cfg = airlock.load_config()
        cfg["pad"] = "off"
        self.assertEqual(airlock.launch_nested("lite", det, cfg), "A0OK")
        self.assertEqual((self.cfg / "kwinoutputconfig.json").read_text(), '[{"name":"eDP-1","mode":"1920x1080@120"}]')
        self.assertFalse((self.cfg / "session").exists())
        self.assertEqual(json.loads((self.t / "userenv.json").read_text()), GAME_ENV)

    def test_steam_exit_game_signal(self):
        k = self.kwin(30)
        code = (
            "import sys,json; sys.path.insert(0,%r)\n"
            "from ddg import airlock\n"
            "cfg=airlock.load_config(); cfg['pad']='off'\n"
            "airlock.launch_nested('lite', {'kwin': %r, 'kwin_flags': {}, 'display': ':0', 'session': 'desktop'}, cfg)\n"
        ) % (str(ROOT / "src"), str(k))
        p = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
        try:
            for _ in range(80):
                if airlock.ready_path().exists():
                    break
                time.sleep(0.1)
            time.sleep(0.5)
            self.assertIn("WL-1", (self.cfg / "kwinoutputconfig.json").read_text())   # polluted while open
            p.send_signal(signal.SIGTERM)                                           # Steam > Exit game
            p.wait(20)
        finally:
            if p.poll() is None:
                p.kill(); p.wait()
        self.assertEqual((self.cfg / "kwinoutputconfig.json").read_text(), '[{"name":"eDP-1","mode":"1920x1080@120"}]')
        self.assertFalse((self.cfg / "session").exists())
        self.assertEqual(json.loads((self.t / "userenv.json").read_text()), GAME_ENV)


class RepairButton(Base):
    def test_repair_clears_stale_state_and_settings(self):
        desktopguard.begin()
        self.airlock_pollutes()
        man = json.loads(desktopguard.manifest().read_text())
        man["pid"] = 999999
        desktopguard.manifest().write_text(json.dumps(man))
        airlock.write_state("game", "switching", pid=999999)
        airlock.ready_path().touch()
        r = desktopguard.repair()
        self.assertTrue(r["ok"])
        self.assert_clean()
        self.assertEqual(airlock.read_state()["status"], "closed")
        self.assertFalse(airlock.ready_path().exists())
        self.assertIn("Can't Bear It", r["message"])

    def test_refuses_while_an_airlock_is_open(self):
        airlock.write_state("game", "open", pid=os.getpid())
        orig = airlock._pid_alive
        airlock._pid_alive = lambda pid: True
        try:
            r = desktopguard.repair()
        finally:
            airlock._pid_alive = orig
        self.assertFalse(r["ok"])
        self.assertIn("Close it first", r["message"])

    def test_nothing_to_repair(self):
        r = desktopguard.repair()
        self.assertTrue(r["ok"])
        self.assertIn("Nothing to repair", r["message"])

    def test_web_and_cli(self):
        import io
        from contextlib import redirect_stdout
        from ddg import webui
        from ddg import __main__ as cli
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "tok"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown); self.addCleanup(srv.server_close)
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/api/airlock/repair" % srv.server_address[1], data=b"{}",
                                     method="POST", headers={"X-Token": "tok", "Content-Type": "application/json",
                                                             "Host": "127.0.0.1:%d" % srv.server_address[1]})
        try:
            body = json.loads(urllib.request.urlopen(req).read())
        except urllib.error.HTTPError as e:
            self.fail("HTTP %s: %s" % (e.code, e.read()[:200]))
        self.assertIn("Nothing to repair", body["message"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["airlock", "repair"]), 0)
        self.assertIn("Nothing to repair", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
