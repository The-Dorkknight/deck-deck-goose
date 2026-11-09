# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Edge cases: odd manifests, unsafe targets, read-only game folders, a full
disk, broken library lists, duplicates, symlinks, two windows at once."""
import errno
import fcntl
import json
import os
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_steam import SteamBase  # noqa: E402
from ddg import steam, vdf  # noqa: E402


class ManifestEdges(SteamBase):
    def _manifest(self, appid, installdir, name="Odd"):
        vdf.dump_file({"AppState": {"appid": appid, "name": name, "StateFlags": "4",
                                    "installdir": installdir, "SizeOnDisk": "1"}},
                      self.root / ("steamapps/appmanifest_%s.acf" % appid))

    def test_path_traversal_installdir_is_harmless(self):
        victim = self.home / "precious"
        victim.mkdir()
        (victim / "keep.txt").write_text("mine")
        self._manifest("4200001", "../../../../precious")
        g = steam.find_game("4200001")
        self.assertEqual(g.items(), [])
        self.assertIn("folder name", g.extra["problem"])
        steps = steam.move_game(g, str(self.drive3 / "SteamLibrary"), self.job())
        self.assertFalse(steps[-1].ok)
        self.assertFalse(steam.compress_game(g, self.job())[0].ok)
        ids = [d["id"] for d in steam.scan_junk()]
        steam.clean_junk(ids, self.job())
        self.assertEqual((victim / "keep.txt").read_text(), "mine")

    def test_empty_installdir_never_means_whole_common_folder(self):
        self._manifest("4200002", "")
        g = steam.find_game("4200002")
        self.assertEqual(g.items(), [])
        self.assertFalse(steam.move_game(g, str(self.drive3 / "SteamLibrary"), self.job())[-1].ok)
        self.assertTrue((self.root / "steamapps/common/StarCourier").is_dir())

    def test_manifest_id_mismatch_ignored(self):
        self._manifest("4200003", "X")
        os.rename(str(self.root / "steamapps/appmanifest_4200003.acf"),
                  str(self.root / "steamapps/appmanifest_4299999.acf"))
        self.assertIsNone(steam.find_game("4200003"))
        self.assertIsNone(steam.find_game("4299999"))

    def test_corrupt_manifest_skipped(self):
        (self.root / "steamapps/appmanifest_4200004.acf").write_text('"AppState"\n{\n"name" "broken')
        self.assertEqual(len(steam.list_games()), 5)

    def test_duplicate_install_refused(self):
        src = self.root / "steamapps/appmanifest_4100010.acf"
        (self.drive2 / "SteamLibrary/steamapps/appmanifest_4100010.acf").write_bytes(src.read_bytes())
        g = steam.find_game("4100010")
        self.assertIn("2 libraries", g.extra["problem"])
        self.assertIn("2 libraries", steam.move_game(g, str(self.drive3 / "L"), self.job())[-1].detail)

    def test_unicode_and_html_names_move(self):
        name = "Ünïcode ゲーム <b>&\"'"
        g0 = self.root / "steamapps/common" / name
        g0.mkdir(parents=True)
        (g0 / "セーブ.dat").write_text("data")
        self._manifest("4200005", name, name)
        os.environ["THISPC_FORCE_COPY"] = "1"
        steps = steam.move_game(steam.find_game("4200005"), str(self.drive3 / "SteamLibrary"), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertEqual((self.drive3 / "SteamLibrary/steamapps/common" / name / "セーブ.dat").read_text(), "data")
        json.dumps(steam.games_status())   # the window can show it


class TargetEdges(SteamBase):
    def setUp(self):
        super().setUp()
        self.g = steam.find_game("4100010")

    def _why(self, target):
        return steam.move_game(self.g, str(target), self.job())[-1].detail

    def test_unmounted_drive_not_filled_with_ram(self):
        ram = Path(self.tmp.name) / "run/media/matthew"
        (ram / "Games").mkdir(parents=True)      # empty folder left where the drive used to be
        with open(os.environ["THISPC_MOUNTS_FILE"], "a") as f:
            f.write("tmpfs %s tmpfs rw 0 0\n" % ram.parent.parent)
        self.assertIn("not on a real data drive", self._why(ram / "Games/SteamLibrary"))
        self.assertIn("does not exist", self._why(ram / "Missing/Deeper/SteamLibrary"))

    def test_read_only_mount(self):
        with open(os.environ["THISPC_MOUNTS_FILE"], "a") as f:
            f.write("/dev/sde1 %s ext4 ro,relatime 0 0\n" % self.drive3)
        self.assertIn("read-only", self._why(self.drive3 / "SteamLibrary"))

    def test_steamapps_folder_given(self):
        self.assertIn("library folder itself", self._why(self.drive2 / "SteamLibrary/steamapps"))

    def test_not_writable(self):
        if os.geteuid() == 0:
            self.skipTest("root can write anywhere")
        os.chmod(str(self.drive3), 0o555)
        try:
            self.assertIn("cannot write", self._why(self.drive3 / "SteamLibrary"))
        finally:
            os.chmod(str(self.drive3), 0o755)


class FileEdges(SteamBase):
    def test_read_only_folders_move_and_old_copy_removed(self):
        os.environ["THISPC_FORCE_COPY"] = "1"
        ro = self.root / "steamapps/common/StarCourier/bin"
        os.chmod(str(ro / "game.sh"), 0o555)
        os.chmod(str(ro), 0o555)
        steps = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertFalse((self.root / "steamapps/common/StarCourier").exists())
        new = self.drive3 / "SteamLibrary/steamapps/common/StarCourier/bin"
        self.assertEqual(stat.S_IMODE(new.stat().st_mode), 0o555)

    def test_disk_full_rolls_back(self):
        os.environ["THISPC_FORCE_COPY"] = "1"
        orig, n = steam._copy_file, [0]

        def full(s, d, j, done):
            n[0] += 1
            if n[0] == 3:
                raise OSError(errno.ENOSPC, "No space left on device")
            orig(s, d, j, done)
        steam._copy_file = full
        try:
            steps = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        finally:
            steam._copy_file = orig
        self.assertIn("No space left", steps[-1].detail)
        self.assertTrue((self.root / "steamapps/appmanifest_4100010.acf").exists())
        self.assertEqual([p for p in self.drive3.rglob("*") if p.is_file()], [])

    def test_unreadable_file_rolls_back(self):
        if os.geteuid() == 0:
            self.skipTest("root can read anything")
        os.environ["THISPC_FORCE_COPY"] = "1"
        os.chmod(str(self.root / "steamapps/common/StarCourier/levels.dat"), 0)
        steps = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        self.assertFalse(steps[-1].ok)
        self.assertTrue((self.root / "steamapps/appmanifest_4100010.acf").exists())

    def test_rename_exdev_falls_back_to_copy(self):
        real, hits = os.rename, [0]

        def rename(a, b):
            if str(a).endswith("common/StarCourier") and hits[0] == 0:
                hits[0] += 1
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real(a, b)
        steam.os.rename = rename
        try:
            steps = steam.move_game(steam.find_game("4100010"), str(self.drive2 / "SteamLibrary"), self.job())
        finally:
            steam.os.rename = real
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertTrue(any(s.name == "Copied and checked" for s in steps))
        self.assertTrue((self.drive2 / "SteamLibrary/steamapps/common/StarCourier/levels.dat").exists())

    def test_symlinked_junk_outside_is_not_followed(self):
        outside = self.home / "Documents"
        outside.mkdir()
        (outside / "cv.txt").write_text("cv")
        os.symlink(str(outside), str(self.root / "steamapps/shadercache/777777"))
        ids = [d["id"] for d in steam.scan_junk() if d["path"].endswith("777777")]
        self.assertEqual(len(ids), 1)
        steps = steam.clean_junk(ids, self.job())
        self.assertTrue(steps[0].ok, steps)
        self.assertFalse(os.path.lexists(str(self.root / "steamapps/shadercache/777777")))
        self.assertEqual((outside / "cv.txt").read_text(), "cv")


class LibraryListEdges(SteamBase):
    def test_corrupt_library_list(self):
        for f in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
            (self.root / f).write_text('"libraryfolders"\n{\n "0"\n {\n')
        self.assertTrue(any(g.appid == "4100010" for g in steam.list_games()))
        why = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())[-1].detail
        self.assertIn("could not be read", why)
        self.assertTrue((self.root / "steamapps/appmanifest_4100010.acf").exists())

    def test_no_library_list_yet(self):
        for f in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
            (self.root / f).unlink()
        os.environ["THISPC_FORCE_COPY"] = "1"
        steps = steam.move_game(steam.find_game("4100010"), str(self.drive3 / "SteamLibrary"), self.job())
        self.assertTrue(all(s.ok for s in steps), steps)
        d = vdf.load_file(self.root / "steamapps/libraryfolders.vdf")["libraryfolders"]
        self.assertEqual(os.path.realpath(d["0"]["path"]), os.path.realpath(str(self.root)))
        self.assertIn("4100010", d["1"]["apps"])


class LockEdges(SteamBase):
    def test_second_window_is_refused_and_recovery_waits(self):
        steam.core.config_dir().mkdir(parents=True, exist_ok=True)
        fd = os.open(str(steam.core.config_dir() / "steam.lock"), os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            g = steam.find_game("4100010")
            self.assertIn("Another", steam.move_game(g, str(self.drive3 / "L"), self.job())[0].detail)
            self.assertIn("Another", steam.clean_junk([], self.job())[0].detail)
            self.assertIn("Another", steam.compress_game(g, self.job())[0].detail)
            steam._write_journal({"appid": "1", "name": "x", "src": "/nope", "dst": "/nope2",
                                  "items": [], "mode": "copy", "phase": "copying"})
            self.assertEqual(steam.recover_moves(), [])      # someone else's live move: hands off
            self.assertTrue(steam._journal().exists())
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(len(steam.recover_moves()), 1)
        self.assertFalse(steam._journal().exists())


if __name__ == "__main__":
    unittest.main()


class ConfigEdges(SteamBase):
    def test_this_pc_install_and_remove_keep_game_records(self):
        from ddg import core
        steam._save_record("estimates", "4100010", {"saved": 5})
        core.install(apps=[])
        self.assertIn("4100010", steam.estimates_record())
        core.uninstall()
        self.assertIn("4100010", steam.estimates_record())
        self.assertNotIn("apps", core.load_config())

    def test_parallel_record_writes_lose_nothing(self):
        import threading as th
        ts = [th.Thread(target=steam._save_record, args=("estimates", str(i), {"saved": i})) for i in range(40)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(steam.estimates_record()), 40)
