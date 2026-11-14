# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Deleting a duplicate copy, and handing a single copy to Steam's uninstaller (0.6.6)."""
import json
import os
import shutil
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from test_steam import SteamBase  # noqa: E402
from ddg import steam, uninstall, vdf, jobs  # noqa: E402

APPID = "4100010"     # Star Courier, first library


class Duplicate(SteamBase):
    def setUp(self):
        super().setUp()
        sa1 = self.root / "steamapps"
        self.sa2 = self.drive2 / "SteamLibrary" / "steamapps"
        shutil.copy2(sa1 / ("appmanifest_%s.acf" % APPID), self.sa2 / ("appmanifest_%s.acf" % APPID))
        shutil.copytree(sa1 / "common" / "StarCourier", self.sa2 / "common" / "StarCourier")
        (self.sa2 / "compatdata" / APPID / "pfx").mkdir(parents=True)
        (self.sa2 / "compatdata" / APPID / "pfx" / "save.dat").write_text("save")
        (self.sa2 / "shadercache" / APPID).mkdir(parents=True)
        self.sa1 = sa1

    def copy2(self):
        return uninstall.find_copy(APPID, str(self.drive2 / "SteamLibrary"))

    def run_job(self, fn):
        j = jobs.Job("t")
        return fn(j)

    def test_both_copies_are_listed_and_flagged(self):
        cs = uninstall.copies(APPID)
        self.assertEqual(len(cs), 2)
        self.assertTrue(all("installed in 2 libraries" in g.extra.get("problem", "") for g in cs))

    def test_delete_the_second_copy_only(self):
        g = self.copy2()
        self.assertIsNone(uninstall.check_delete_copy(g))
        steps = self.run_job(lambda j: uninstall.delete_copy(g, j))
        self.assertTrue(all(s.ok for s in steps), [s.as_dict() for s in steps])
        self.assertFalse((self.sa2 / ("appmanifest_%s.acf" % APPID)).exists())
        self.assertFalse((self.sa2 / "common" / "StarCourier").exists())
        self.assertFalse((self.sa2 / "shadercache" / APPID).exists())
        self.assertTrue((self.sa2 / "compatdata" / APPID / "pfx" / "save.dat").exists())       # saves kept
        self.assertTrue((self.sa1 / ("appmanifest_%s.acf" % APPID)).exists())                  # the other copy untouched
        self.assertTrue((self.sa1 / "common" / "StarCourier" / "data.pak").exists())
        self.assertEqual(len(uninstall.copies(APPID)), 1)
        self.assertFalse(uninstall.copies(APPID)[0].extra.get("problem"))                     # Move/Compress unblocked
        backups = list((self.home / ".config/deck-deck-goose/deleted-manifests").glob("*appmanifest_%s.acf" % APPID))
        self.assertEqual(len(backups), 1)

    def test_refuses_the_only_copy(self):
        g = uninstall.find_copy("4100020", str(self.root))
        self.assertIn("only copy", uninstall.check_delete_copy(g))

    def test_refuses_while_steam_runs(self):
        os.environ["THISPC_FAKE_STEAM_RUNNING"] = "1"
        self.assertIn("Close Steam", uninstall.check_delete_copy(self.copy2()))

    def test_refuses_when_both_point_at_one_folder(self):
        shutil.rmtree(self.sa2 / "common" / "StarCourier")
        os.symlink(self.sa1 / "common" / "StarCourier", self.sa2 / "common" / "StarCourier")
        self.assertIn("same folder", uninstall.check_delete_copy(self.copy2()))
        self.assertTrue((self.sa1 / "common" / "StarCourier" / "data.pak").exists())

    def test_folder_kept_when_another_app_uses_it(self):
        vdf.dump_file({"AppState": {"appid": "4100099", "name": "Star Courier Soundtrack", "installdir": "StarCourier", "StateFlags": "4"}},
                      self.sa2 / "appmanifest_4100099.acf")
        steps = self.run_job(lambda j: uninstall.delete_copy(self.copy2(), j))
        self.assertTrue((self.sa2 / "common" / "StarCourier").exists())
        self.assertTrue(any("kept" in s.detail and "Soundtrack" in s.detail for s in steps))

    def test_library_list_updated(self):
        data = vdf.load_file(self.root / "config/libraryfolders.vdf")
        for e in data["libraryfolders"].values():
            if e["path"] == str(self.drive2 / "SteamLibrary"):
                e["apps"][APPID] = "1"
        vdf.dump_file(data, self.root / "config/libraryfolders.vdf")
        self.run_job(lambda j: uninstall.delete_copy(self.copy2(), j))
        data = vdf.load_file(self.root / "config/libraryfolders.vdf")
        for e in data["libraryfolders"].values():
            if e["path"] == str(self.drive2 / "SteamLibrary"):
                self.assertNotIn(APPID, e["apps"])
            if e["path"] == str(self.root):
                self.assertIn(APPID, e["apps"])

    def test_steam_uninstaller_is_only_asked(self):
        log = Path(self.tmp.name) / "open.log"
        os.environ["DDG_FAKE_OPEN"] = str(log)
        st = uninstall.open_steam_uninstaller("4100020")
        self.assertTrue(st.ok)
        self.assertEqual(log.read_text().strip(), "steam://uninstall/4100020")
        self.assertTrue((self.root / "steamapps/appmanifest_4100020.acf").exists())            # nothing deleted by us
        self.assertFalse(uninstall.open_steam_uninstaller("../x").ok)


class DuplicateInWindow(Duplicate):
    """The real page in a browser: both cards say why, and Delete on the second card removes the second copy."""

    def test_page(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright not installed")
        from ddg import webui
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "t0k3n"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown); self.addCleanup(srv.server_close)
        url = "http://127.0.0.1:%d/" % srv.server_address[1]
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--no-proxy-server"])
            pg = b.new_page(viewport={"width": 1200, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("dialog", lambda d: d.accept())
            pg.add_init_script("try{localStorage.setItem('ddg-ok','1');localStorage.setItem('ddg-tab','games')}catch(e){}")
            pg.goto(url)
            pg.click("nav.tabs button[data-tab=games]")
            pg.wait_for_selector(".game .why", timeout=8000)
            cards = pg.query_selector_all('.game[data-id="%s"]' % APPID)
            self.assertEqual(len(cards), 2)
            for c in cards:
                self.assertIn("Installed twice", c.inner_text())
            second = pg.query_selector('.game[data-id="%s"][data-lib="%s"]' % (APPID, self.drive2 / "SteamLibrary"))
            second.query_selector("button.del").click()
            deadline = time.time() + 15
            while time.time() < deadline and (self.sa2 / ("appmanifest_%s.acf" % APPID)).exists():
                time.sleep(0.2)
            self.assertFalse((self.sa2 / ("appmanifest_%s.acf" % APPID)).exists())
            self.assertTrue((self.sa1 / ("appmanifest_%s.acf" % APPID)).exists())
            other = pg.query_selector('.game[data-id="4100020"]')
            self.assertIn("Uninstall", other.inner_text())
            b.close()
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
