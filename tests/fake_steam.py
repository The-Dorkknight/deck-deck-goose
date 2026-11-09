# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Builds a pretend Steam install for the tests and the screenshots.
All games, ids and artwork here are invented."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ddg import vdf  # noqa: E402

GAMES = [
    # appid, name, installdir, state, megabytes of data, has prefix
    ("4100010", "Star Courier", "StarCourier", 4, 3, True),
    ("4100020", "Moss Valley", "Moss Valley", 4, 2, False),
    ("4100030", "Iron Lantern", "IronLantern", 6, 1, True),     # update required
    ("4100040", "Proton 9.0", "Proton 9.0", 4, 1, False),       # a tool
]
SECOND = [("4100050", "Harbour Lights", "HarbourLights", 4, 2, True)]


def _manifest(sa: Path, appid, name, installdir, state, mb) -> None:
    vdf.dump_file({"AppState": {"appid": appid, "universe": "1", "name": name,
                                "StateFlags": str(state), "installdir": installdir,
                                "SizeOnDisk": str(mb * 1024 * 1024), "LastOwner": "76561190000000000"}},
                  sa / ("appmanifest_%s.acf" % appid))


def _game(sa: Path, appid, name, installdir, state, mb, prefix) -> None:
    _manifest(sa, appid, name, installdir, state, mb)
    g = sa / "common" / installdir
    (g / "bin").mkdir(parents=True)
    with open(g / "data.pak", "wb") as f:
        f.write(os.urandom(mb * 1024 * 1024))
    # compressible game data (like uncompressed level files), plus an intro video
    words = [b"level", b"mesh", b"texture", b"spawn", b"door", b"light", b"enemy", b"path"]
    (g / "levels.dat").write_bytes(b" ".join(words[(i * 7 + i // 3) % 8] for i in range(120000)))
    (g / "intro.bk2").write_bytes(os.urandom(256 * 1024))
    (g / "bin" / "game.sh").write_text("#!/bin/sh\necho hi\n")
    os.chmod(g / "bin" / "game.sh", 0o755)
    os.symlink("bin/game.sh", g / "start.sh")
    if prefix:
        pfx = sa / "compatdata" / appid / "pfx"
        (pfx / "drive_c/users/steamuser/Saved Games").mkdir(parents=True)
        (pfx / "drive_c/users/steamuser/Saved Games/save1.dat").write_text("level 7")
        (pfx / "dosdevices").mkdir()
        os.symlink("../drive_c", pfx / "dosdevices" / "c:")
    sc = sa / "shadercache" / appid
    sc.mkdir(parents=True)
    (sc / "cache.bin").write_bytes(b"s" * 4096)


def build(home: Path, drive2: Path, art=None) -> Path:
    """Steam in ~/.local/share/Steam with one extra library on drive2.
    art(appid, path) may write a picture; default writes tiny files."""
    root = home / ".local/share/Steam"
    sa = root / "steamapps"
    sa.mkdir(parents=True)
    lib2 = drive2 / "SteamLibrary"
    sa2 = lib2 / "steamapps"
    sa2.mkdir(parents=True)
    for g in GAMES:
        _game(sa, *g)
    for g in SECOND:
        _game(sa2, *g)
    # workshop item for the first game
    (sa / "workshop/content/4100010/999").mkdir(parents=True)
    (sa / "workshop/content/4100010/999/mod.txt").write_text("mod")
    vdf.dump_file({"AppWorkshop": {"appid": "4100010"}}, sa / "workshop/appworkshop_4100010.acf")
    # junk
    (sa / "shadercache/555").mkdir(parents=True)
    (sa / "shadercache/555/old.bin").write_bytes(b"x" * 8192)
    (sa / "compatdata/999/pfx").mkdir(parents=True)                 # orphan prefix
    (sa / "compatdata/3123456789/pfx").mkdir(parents=True)          # non-Steam shortcut: keep!
    (sa / "common/Old Game Leftovers").mkdir(parents=True)
    (sa / "common/Old Game Leftovers/mods.txt").write_text("my mods")
    (sa / "downloading/777").mkdir(parents=True)
    (sa / "downloading/777/chunk").write_bytes(b"d" * 4096)
    (root / "logs").mkdir()
    (root / "logs/content_log.txt").write_text("log\n" * 100)
    (root / "appcache/httpcache").mkdir(parents=True)
    (root / "appcache/httpcache/abc").write_bytes(b"h" * 2048)
    # libraryfolders, both copies, like a current Steam
    data = {"libraryfolders": {
        "0": {"path": str(root), "label": "", "contentid": "111", "totalsize": "0",
              "apps": {a: str(m * 1024 * 1024) for a, _, _, _, m, _ in GAMES}},
        "1": {"path": str(lib2), "label": "", "contentid": "222", "totalsize": "0",
              "apps": {a: str(m * 1024 * 1024) for a, _, _, _, m, _ in SECOND}},
    }}
    (root / "config").mkdir()
    vdf.dump_file(data, root / "config/libraryfolders.vdf")
    vdf.dump_file(data, sa / "libraryfolders.vdf")
    vdf.dump_file({"libraryfolder": {"contentid": "222", "label": ""}}, lib2 / "libraryfolder.vdf")
    # artwork: new layout for most, old flat layout for one, custom grid for one
    cache = root / "appcache/librarycache"
    for appid, *_ in GAMES + SECOND:
        if appid == "4100020":
            p = cache / ("%s_library_600x900.jpg" % appid)
        else:
            p = cache / appid / ("%040x" % int(appid)) / "library_600x900.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        if art:
            art(appid, p)
        else:
            p.write_bytes(b"\xff\xd8\xff" + appid.encode())
    return root
