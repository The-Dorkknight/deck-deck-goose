# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Builds a simulated Windows PC with Steam for the tests and screenshots.
Drives are folders; all games, ids and artwork are invented."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ddg import vdf  # noqa: E402

GAMES = [
    # appid, name, installdir, state, MB of random data, extras
    ("5100010", "Star Courier", "Star Courier", 4, 3, {"redist": True}),
    ("5100020", "Moss Valley", "Moss Valley", 4, 2, {}),
    ("5100030", "Iron Lantern", "Iron Lantern", 6, 1, {}),          # update required
    ("5100040", "Steamworks Common Redistributables", "Steamworks Shared", 4, 1, {}),
    ("5100060", "Quiet Orbit", "Quiet Orbit", 4, 1, {"dstorage": True}),
]
SECOND = [("5100050", "Harbour Lights", "Harbour Lights", 4, 2, {})]
DRIVES = [("C:", "C", "Windows", "NTFS", "fixed"), ("D:", "D", "Games", "NTFS", "fixed"),
          ("E:", "E", "Spare", "NTFS", "fixed"), ("F:", "F", "USB", "FAT32", "removable"),
          ("G:", "G", "Travel", "exFAT", "removable"), ("N:", "N", "NAS", "NTFS", "network")]


def _game(sa: Path, appid, name, installdir, state, mb, extras) -> None:
    vdf.dump_file({"AppState": {"appid": appid, "universe": "1", "name": name, "StateFlags": str(state),
                                "installdir": installdir, "SizeOnDisk": str(mb * 1024 * 1024)}},
                  sa / ("appmanifest_%s.acf" % appid))
    g = sa / "common" / installdir
    (g / "bin").mkdir(parents=True)
    (g / "data.pak").write_bytes(os.urandom(mb * 1024 * 1024))
    words = [b"level", b"mesh", b"texture", b"spawn", b"door", b"light", b"enemy", b"path"]
    (g / "levels.dat").write_bytes(b" ".join(words[(i * 7 + i // 3) % 8] for i in range(120000)))
    (g / "intro.bk2").write_bytes(os.urandom(256 * 1024))
    (g / "bin" / "game.exe").write_bytes(b"MZ" + b"\0\x01\x02code" * 20000)
    ro = g / "readme.txt"
    ro.write_text("read-only file, as some games ship")
    os.chmod(str(ro), 0o444)
    if extras.get("redist"):
        (g / "_CommonRedist/DirectX").mkdir(parents=True)
        (g / "_CommonRedist/DirectX/setup.bin").write_bytes(os.urandom(64 * 1024))
    if extras.get("dstorage"):
        (g / "bin" / "dstorage.dll").write_bytes(b"MZ" + os.urandom(1000))
    sc = sa / "shadercache" / appid
    sc.mkdir(parents=True)
    (sc / "cache.bin").write_bytes(b"s" * 4096)


def build(base: Path, art=None, spec_path=None) -> dict:
    """Returns the spec; also writes it to spec_path (default base/spec.json)."""
    drives = []
    for letter, folder, label, fs, kind in DRIVES:
        (base / folder).mkdir(parents=True, exist_ok=True)
        drives.append({"letter": letter, "root": str(base / folder), "label": label, "fstype": fs, "kind": kind})
    folder = {letter: f for letter, f, *_ in DRIVES}
    root = base / folder["C:"] / "Program Files (x86)" / "Steam"
    sa = root / "steamapps"
    sa.mkdir(parents=True)
    lib2 = base / folder["D:"] / "SteamLibrary"
    (lib2 / "steamapps").mkdir(parents=True)
    for g in GAMES:
        _game(sa, *g)
    for g in SECOND:
        _game(lib2 / "steamapps", *g)
    (sa / "workshop/content/5100010/999").mkdir(parents=True)
    (sa / "workshop/content/5100010/999/mod.txt").write_text("mod")
    vdf.dump_file({"AppWorkshop": {"appid": "5100010"}}, sa / "workshop/appworkshop_5100010.acf")
    (sa / "shadercache/555").mkdir(parents=True)
    (sa / "shadercache/555/old.bin").write_bytes(b"x" * 8192)
    (sa / "common/Old Game Leftovers").mkdir(parents=True)
    (sa / "common/Old Game Leftovers/settings.ini").write_text("[video]\nfov=90\n")
    (sa / "downloading/777").mkdir(parents=True)
    (sa / "downloading/777/chunk").write_bytes(b"d" * 4096)
    (root / "logs").mkdir()
    (root / "logs/content_log.txt").write_text("log\n" * 100)
    (root / "appcache/httpcache").mkdir(parents=True)
    (root / "appcache/httpcache/abc").write_bytes(b"h" * 2048)
    data = {"libraryfolders": {
        "0": {"path": str(root), "label": "", "contentid": "111", "totalsize": "0",
              "apps": {a: str(m * 1024 * 1024) for a, _, _, _, m, _ in GAMES}},
        "1": {"path": str(lib2), "label": "", "contentid": "222", "totalsize": "0",
              "apps": {a: str(m * 1024 * 1024) for a, _, _, _, m, _ in SECOND}}}}
    (root / "config").mkdir()
    vdf.dump_file(data, root / "config/libraryfolders.vdf")
    vdf.dump_file(data, sa / "libraryfolders.vdf")
    vdf.dump_file({"libraryfolder": {"contentid": "222", "label": ""}}, lib2 / "libraryfolder.vdf")
    cache = root / "appcache/librarycache"
    for appid, *_ in GAMES + SECOND:
        p = cache / ("%s_library_600x900.jpg" % appid) if appid == "5100020" else \
            cache / appid / ("%040x" % int(appid)) / "library_600x900.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        if art:
            art(appid, p)
        else:
            p.write_bytes(b"\xff\xd8\xff" + appid.encode())
    spec = {"steam_roots": [str(root)], "drives": drives, "steam_running": False}
    Path(spec_path or base / "spec.json").write_text(json.dumps(spec))
    return spec
