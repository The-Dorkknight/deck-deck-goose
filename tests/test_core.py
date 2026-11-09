# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddg import __version__, core  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "lsblk-demo.json"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home" / "matthew"
        for d in ("Documents", "Downloads", "Pictures"):
            (self.home / d).mkdir(parents=True)
        (self.home.parent / "guest").mkdir()
        # Re-point the demo disks at real folders under the temp dir so the
        # shortcuts have somewhere to point.
        self.mnt = str(Path(self.tmp.name) / "mnt")
        text = FIXTURE.read_text().replace("/run/media/matthew", self.mnt + "/run/media/matthew") \
                                  .replace("/var/mnt/games", self.mnt + "/var/mnt/games")
        for sub in ("run/media/matthew/Backup", "run/media/matthew/USBSTICK", "var/mnt/games"):
            Path(self.mnt, sub).mkdir(parents=True)
        self.lsblk = Path(self.tmp.name) / "lsblk.json"
        self.lsblk.write_text(text)
        self.old_prefixes = core.DATA_PREFIXES
        core.DATA_PREFIXES = (self.mnt + "/",)
        self.env = dict(os.environ)
        os.environ.update(HOME=str(self.home), THISPC_LSBLK_JSON=str(self.lsblk),
                          THISPC_FAKE_TOOLS="1")

    def tearDown(self):
        core.DATA_PREFIXES = self.old_prefixes
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()


class DiskTests(Base):
    def test_system_volumes_hidden_data_disks_shown(self):
        disks = core.list_disks()
        mounted = {d.mountpoint for d in disks if d.mounted}
        self.assertEqual(mounted, {self.mnt + "/var/mnt/games", self.mnt + "/run/media/matthew/Backup",
                                   self.mnt + "/run/media/matthew/USBSTICK"})
        self.assertFalse(any(d.mountpoint in ("/", "/var", "/boot/efi") for d in disks))
        self.assertFalse(any("loop" in d.dev for d in disks))

    def test_unmounted_data_disk_listed(self):
        un = [d for d in core.list_disks() if not d.mounted]
        self.assertEqual([d.dev for d in un], ["/dev/sdb1"])

    def test_names_unique_and_slash_free(self):
        d = core.parse_disks(json.loads(self.lsblk.read_text()))
        d.append(core.Disk("/dev/zz1", "a/b", "ext4", 10 ** 10, self.mnt + "/a", False))
        d.append(core.Disk("/dev/zz2", "a/b", "ext4", 10 ** 10, self.mnt + "/b", False))
        names = core.display_names(d)
        self.assertEqual(len(set(names.values())), len(names))
        self.assertFalse(any("/" in n for n in names.values()))
        self.assertIn("Unlabelled 1.0 TB (sdb1)", names.values())


class ViewTests(Base):
    def test_sync_creates_logical_paths(self):
        root = core.view_root()
        stats, _ = core.refresh()
        self.assertEqual(os.readlink(str(root / "Disks" / "Games")), self.mnt + "/var/mnt/games")
        self.assertEqual(os.readlink(str(root / "Users" / "matthew")), str(self.home))
        self.assertTrue((root / "Users" / "guest").is_symlink())
        self.assertTrue((root / "Documents").is_symlink())
        self.assertFalse((root / "Music").exists())
        self.assertEqual(stats["removed"], 0)

    def test_idempotent_and_prunes_stale(self):
        core.refresh()
        stats, _ = core.refresh()
        self.assertEqual((stats["created"], stats["updated"], stats["removed"]), (0, 0, 0))
        os.symlink("/nowhere", str(core.view_root() / "Disks" / "Gone"))
        stats, _ = core.refresh()
        self.assertEqual(stats["removed"], 1)

    def test_never_deletes_real_files(self):
        core.refresh()
        keep = core.view_root() / "Disks" / "my-notes.txt"
        keep.write_text("mine")
        core.refresh()
        self.assertEqual(keep.read_text(), "mine")
        step = core.remove_view(core.view_root())
        self.assertTrue(step.ok)
        self.assertEqual(keep.read_text(), "mine")

    def test_refuses_foreign_folder(self):
        root = core.view_root()
        root.mkdir()
        (root / "important.txt").write_text("x")
        with self.assertRaises(RuntimeError):
            core.refresh()
        self.assertTrue((root / "important.txt").exists())


class BookmarkTests(Base):
    def test_gtk_add_remove_idempotent(self):
        root = core.view_root()
        f = core.gtk_bookmarks_path()
        f.parent.mkdir(parents=True)
        f.write_text("file:///tmp/other Other\n")
        core.gtk_bookmark(root, True)
        core.gtk_bookmark(root, True)
        self.assertEqual(f.read_text().count("This%20PC"), 1)
        core.gtk_bookmark(root, False)
        self.assertEqual(f.read_text(), "file:///tmp/other Other\n")

    def test_kde_add_remove_keeps_other_entries(self):
        f = core.kde_places_path()
        f.parent.mkdir(parents=True)
        base = ('<?xml version="1.0" encoding="UTF-8"?>\n<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks">\n'
                ' <bookmark href="file:///home/x">\n  <title>Home</title>\n  <info>\n'
                '   <metadata owner="http://www.kde.org">\n    <ID>keep-me</ID>\n   </metadata>\n  </info>\n </bookmark>\n</xbel>\n')
        f.write_text(base)
        core.kde_bookmark(core.view_root(), True)
        core.kde_bookmark(core.view_root(), True)
        t = f.read_text()
        self.assertEqual(t.count(core.KDE_ID), 1)
        self.assertIn("keep-me", t)
        core.kde_bookmark(core.view_root(), False)
        t = f.read_text()
        self.assertNotIn(core.KDE_ID, t)
        self.assertIn("keep-me", t)
        self.assertTrue(f.with_name(f.name + ".thispc-backup").exists())

    def test_kde_missing_file_is_skipped(self):
        self.assertTrue(core.kde_bookmark(core.view_root(), True).ok)


class InstallTests(Base):
    def test_install_check_uninstall_roundtrip(self):
        steps = core.install(apps=["org.kde.dolphin"])
        self.assertTrue(all(s.ok for s in steps), [s for s in steps if not s.ok])
        self.assertTrue(all(s.ok for s in core.check()))
        self.assertTrue((core.systemd_dir() / "ddg-refresh.timer").exists())
        self.assertIn(__version__, (core.data_dir() / "lib/ddg/__init__.py").read_text())
        self.assertTrue(core.override_state("org.kde.dolphin"))
        steps = core.uninstall()
        self.assertTrue(all(s.ok for s in steps), [s for s in steps if not s.ok])
        self.assertFalse(core.view_root().exists())
        self.assertFalse((core.systemd_dir() / "ddg-refresh.timer").exists())
        self.assertFalse(core.override_state("org.kde.dolphin"))

    def test_manager_detection(self):
        self.assertIn("org.kde.dolphin", core.find_file_managers())
        self.assertNotIn("org.mozilla.firefox", core.find_file_managers())

    def test_mount_refuses_unknown_device(self):
        self.assertFalse(core.mount_disk("/dev/sda").ok)
        self.assertTrue(core.mount_disk("/dev/sdb1").ok)


class ProjectTests(unittest.TestCase):
    def test_launchers_exist_and_are_executable(self):
        for n in ("DeckDeckGoose-Linux.sh", "DeckDeckGoose-Mac.command"):
            self.assertTrue(os.stat(ROOT / n).st_mode & stat.S_IXUSR, n)
        self.assertTrue((ROOT / "DeckDeckGoose-Windows.bat").exists())
        self.assertIn(b"\r\n", (ROOT / "DeckDeckGoose-Windows.bat").read_bytes())

    def test_version_consistent(self):
        self.assertIn(__version__, (ROOT / "README.md").read_text())
        self.assertIn(__version__, (ROOT / "CHANGELOG.md").read_text())
        self.assertIn(__version__, (ROOT / "setup.py").read_text())

    def test_cli_version(self):
        out = subprocess.run([sys.executable, "-m", "ddg", "--version"], cwd=str(ROOT / "src"),
                             capture_output=True, text=True).stdout
        self.assertRegex(out, r"Deck Deck Goose .*\(Built \d{4}-\d\d-\d\d \d\d:\d\d\)")

    def test_no_placeholders(self):
        bad = re.compile(r"YOUR-GITHUB|COPYRIGHT_HOLDER|__AUTHOR__|example\.invalid|TODO")
        for p in ROOT.rglob("*"):
            if p.is_file() and ".git" not in p.parts and p.suffix in (".py", ".md", ".sh", ".yml", ".html", ".bat", ".command") and p.name != "test_core.py":
                self.assertIsNone(bad.search(p.read_text(errors="ignore")), p)


if __name__ == "__main__":
    unittest.main()
