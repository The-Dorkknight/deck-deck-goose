# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The Steam library manager on a simulated Mac, plus the platform switch and the
window on Windows and macOS (Linux-only tabs hidden, no Linux modules needed)."""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import fake_win  # noqa: E402
from ddg import jobs, library, vdf  # noqa: E402
from ddg.shelf import macplat, plat, steam  # noqa: E402

MOUNT = """/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
devfs on /dev (devfs, local, nobrowse)
/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, nobrowse, protect)
/dev/disk4s1 on /Volumes/Games (apfs, local, nodev, nosuid, journaled)
/dev/disk5s1 on /Volumes/Travel (exfat, local, nodev, nosuid, noowners)
/dev/disk6s1 on /Volumes/USB (msdos, local, nodev, nosuid, noowners)
/dev/disk7s1 on /Volumes/Windows (ntfs, local, nodev, nosuid, read-only, noowners)
//matt@nas._smb._tcp.local/media on /Volumes/media (smbfs, nodev, nosuid, mounted by matt)
"""


def build_mac(base: Path) -> dict:
    """A Mac with Steam in Library/Application Support and four extra volumes."""
    drives = []
    for letter, folder, label, fs, kind, ro in (
            ("/", "Macintosh", "Macintosh HD", "APFS", "fixed", False),
            ("/Volumes/Games", "Games", "Games", "APFS", "removable", False),
            ("/Volumes/Travel", "Travel", "Travel", "exFAT", "removable", False),
            ("/Volumes/USB", "USB", "USB", "MSDOS", "removable", False),
            ("/Volumes/Windows", "Windows", "Windows", "NTFS", "removable", True),
            ("/Volumes/media", "media", "media", "SMBFS", "network", False)):
        (base / folder).mkdir(parents=True, exist_ok=True)
        drives.append({"letter": letter, "root": str(base / folder), "label": label, "fstype": fs,
                       "kind": kind, "readonly": ro})
    root = base / "Macintosh" / "Library" / "Application Support" / "Steam"
    sa = root / "steamapps"
    sa.mkdir(parents=True)
    lib2 = base / "Games" / "SteamLibrary"
    (lib2 / "steamapps").mkdir(parents=True)
    for g in fake_win.GAMES[:2]:
        fake_win._game(sa, *g)
    data = {"libraryfolders": {
        "0": {"path": str(root), "label": "", "contentid": "111", "totalsize": "0",
              "apps": {g[0]: str(g[4] * 1024 * 1024) for g in fake_win.GAMES[:2]}},
        "1": {"path": str(lib2), "label": "", "contentid": "222", "totalsize": "0", "apps": {}}}}
    (root / "config").mkdir()
    vdf.dump_file(data, root / "config/libraryfolders.vdf")
    vdf.dump_file(data, sa / "libraryfolders.vdf")
    vdf.dump_file({"libraryfolder": {"contentid": "222", "label": ""}}, lib2 / "libraryfolder.vdf")
    spec = {"steam_roots": [str(root)], "drives": drives, "steam_running": False}
    (base / "spec.json").write_text(json.dumps(spec))
    return spec


class MacBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.spec = build_mac(self.base)
        self.root = Path(self.spec["steam_roots"][0])
        self.env = dict(os.environ)
        os.environ.update(HOME=str(self.base / "home"), DDG_FAKE_MAC=str(self.base / "spec.json"), DDG_PLATFORM="mac")
        os.environ.pop("DDG_FORCE_COPY", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        for dp, dns, fns in os.walk(self.tmp.name):
            for n in dns + fns:
                try:
                    os.chmod(os.path.join(dp, n), 0o755)
                except OSError:
                    pass
        self.tmp.cleanup()


class Mac(MacBase):
    def test_switch_and_list(self):
        self.assertEqual(plat.name(), "mac")
        self.assertFalse(library.is_linux())
        self.assertEqual(library.backend().__name__, "ddg.shelf.steam")
        st = steam.games_status()
        self.assertEqual((st["platform"], st["platform_name"], st["algorithms"]), ("mac", "macOS", []))
        self.assertEqual([g["name"] for g in st["games"]], ["Moss Valley", "Star Courier"])
        self.assertTrue(st["simulated"])
        self.assertEqual(st["roots"][0]["path"], str(self.root))

    def test_volumes_and_targets(self):
        by = {t["path"]: t for t in steam.move_targets(steam.find_game("5100020"))}
        self.assertEqual(sum(1 for t in by.values() if t["ok"]), 2, by)   # Games + Travel; USB, NTFS, SMB refused
        games = [t for p, t in by.items() if "Games" in p][0]
        self.assertTrue(games["ok"], games)
        text = json.dumps(list(by.values()))
        self.assertIn("4 GB", text)                # FAT32/MSDOS
        self.assertIn("read-only", text.lower())   # NTFS volume

    def test_compression_is_off_with_a_reason(self):
        g = steam.find_game("5100010")
        steps = steam.estimate_game(g, jobs.Job("e"))
        self.assertFalse(steps[0].ok)
        self.assertIn("not available on macOS", steps[0].detail)
        self.assertFalse(steam.compression_info(self.root)["available"])
        self.assertTrue(all(not x["can_compress"] for x in steam.games_status()["games"]))

    def test_move_to_another_volume(self):
        os.environ["DDG_FORCE_COPY"] = "1"
        g = steam.find_game("5100020")
        target = str(self.base / "Games" / "SteamLibrary")
        steps = steam.move_game(g, target, jobs.Job("m"))
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertTrue((Path(target) / "steamapps" / "common" / "Moss Valley" / "data.pak").exists())
        self.assertFalse((self.root / "steamapps" / "common" / "Moss Valley").exists())
        self.assertTrue((Path(target) / "steamapps" / "appmanifest_5100020.acf").exists())
        self.assertEqual(steam.find_game("5100020").library.path, Path(target))

    def test_steam_must_be_closed(self):
        macplat._state_file().parent.mkdir(parents=True, exist_ok=True)
        macplat._state_file().write_text(json.dumps({"steam_running": True}))
        self.assertTrue(steam.steam_running())
        g = steam.find_game("5100020")
        steps = steam.move_game(g, str(self.base / "Games" / "SteamLibrary"), jobs.Job("m"))
        self.assertFalse(steps[0].ok)
        self.assertTrue(steam.close_steam().ok)
        self.assertFalse(steam.steam_running())

    def test_mount_parsing(self):
        rows = {r["mount"]: r for r in macplat.parse_mount(MOUNT)}
        self.assertEqual(rows["/Volumes/Games"]["fstype"], "APFS")
        self.assertTrue(rows["/Volumes/Windows"]["readonly"])
        self.assertFalse(rows["/Volumes/media"]["local"])
        self.assertEqual(macplat._kind(rows["/Volumes/media"]), "network")
        self.assertEqual(macplat._label("/System/Volumes/Data"), "Macintosh HD")
        f = self.base / "mounts.txt"
        f.write_text(MOUNT)
        os.environ.pop("DDG_FAKE_MAC")
        os.environ["DDG_MOUNTS_FILE"] = str(f)
        names = [d.name for d in macplat.drives()]
        self.assertIn("Macintosh HD (/)", names)
        self.assertIn("Games (/Volumes/Games)", names)
        self.assertFalse(any("/dev" in n or "/System" in n for n in names))
        self.assertEqual(macplat.volume_of("/Volumes/Games/SteamLibrary/steamapps")["fstype"], "APFS")
        self.assertEqual(macplat.volume_of("/Volumes/Games/x")["label"], "Games")

    def test_cli(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        for args, want in ((["games"], "Star Courier"), (["drives"], "Games"), (["targets", "5100020"], "ok"),
                           (["compress-game", "5100010"], "not available")):
            p = subprocess.run([sys.executable, "-m", "ddg"] + args, capture_output=True, text=True, env=env, timeout=60)
            self.assertIn(want, p.stdout + p.stderr, (args, p.stdout, p.stderr))


class Window(MacBase):
    def serve(self):
        from ddg import webui
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "tok"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        port = srv.server_address[1]

        def call(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            c.request(method, path, body=json.dumps(body) if body is not None else None,
                      headers={"Host": "127.0.0.1:%d" % port, "X-Token": "tok", "Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, r.read()
        return call

    def test_mac_page_and_api(self):
        call = self.serve()
        st, page = call("GET", "/")
        self.assertIn('const PLATFORM="mac"', page.decode())
        self.assertIn("const LINUX=PLATFORM", page.decode())
        st, raw = call("GET", "/api/games")
        data = json.loads(raw)
        self.assertEqual(data["platform"], "mac")
        self.assertNotIn("gamemode", data)
        self.assertEqual(len(data["games"]), 2)
        # estimate on a Mac is accepted by the API but the job explains why it can't
        st, raw = call("POST", "/api/games/estimate", {"appid": "5100010"})
        self.assertNotIn("error", json.loads(raw))
        from ddg.jobs import RUNNER
        RUNNER.wait()
        self.assertIn("not available on macOS", json.dumps(RUNNER.current.as_dict()))
        self.assertEqual(json.loads(call("POST", "/api/games/uncompress", {"appid": "5100010"})[1]).get("error"), None)
        RUNNER.wait()

    def test_windows_page_has_algorithms(self):
        spec = fake_win.build(self.base / "w", spec_path=self.base / "wspec.json")
        os.environ.pop("DDG_FAKE_MAC")
        os.environ.update(DDG_FAKE_WIN=str(self.base / "wspec.json"), DDG_PLATFORM="windows")
        call = self.serve()
        data = json.loads(call("GET", "/api/games")[1])
        self.assertEqual(data["platform"], "windows")
        self.assertIn("XPRESS8K", data["algorithms"])
        self.assertEqual(json.loads(call("POST", "/api/games/estimate", {"appid": "5100010", "algo": "BOGUS"})[1])["error"],
                         "unknown compression method")


class NoLinuxModules(unittest.TestCase):
    def test_imports_without_fcntl_or_pwd(self):
        """Windows has no fcntl/pwd/termios: the whole window must still import and serve."""
        code = ("import sys\nfor m in ('fcntl','pwd','termios','grp'): sys.modules[m] = None\n"
                "sys.path.insert(0, %r)\nimport ddg.webui, ddg.__main__, ddg.library\n"
                "print('imported')" % str(ROOT / "src"))
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertIn("imported", p.stdout, p.stderr)


if __name__ == "__main__":
    unittest.main()
