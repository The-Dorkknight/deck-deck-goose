# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Deck Deck Goose engine: find Steam games and their artwork, move a game to
another drive so Steam finds it there, clean junk, estimate and apply
Windows' file compression.

How a move works (Steam must be closed):
  1. Rename (same drive, instant) or copy (other drive) the game's folders
     into the target library's steamapps\\, copies first going to
     "*.ddg-partial" names. Every file is then compared (names, types,
     sizes) with the original.
  2. Commit: make sure the target library is in Steam's libraryfolders.vdf,
     put appmanifest_<id>.acf in the target library's steamapps\\ and update
     the "apps" lists. The manifest stores only the folder name, not a path,
     so moving it is what tells Steam where the game is. No link is needed.
  3. Only then delete the old copy.
A journal lets an interrupted move be finished or rolled back on next start.
Windows saves usually live in Documents or AppData, which are not touched.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
import random
import re
import shutil
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import estimate, plat as winplat
from .. import vdf
from .common import Step, config_dir, human_size, load_config, lp, norm, unlp, update_config
from ..jobs import Cancelled, Job

PARTIAL = ".ddg-partial"
MOVING = ".ddg-moving"
BACKUP = ".ddg-backup"
SHORTCUT_MIN_ID = 2 ** 31
BAD_KINDS = {"network", "cdrom", "ramdisk", "none", "unknown"}
STATE_BITS = [(2, "update required"), (8, "encrypted"), (16, "locked"),
              (32, "files missing"), (64, "running"), (128, "files corrupt"),
              (256, "updating"), (512, "update paused"), (1024, "update started"),
              (2048, "uninstalling"), (4096, "backup running"), (65536, "reconfiguring"),
              (131072, "validating"), (262144, "adding files"), (524288, "preallocating"),
              (1048576, "downloading"), (2097152, "staging"), (4194304, "committing"),
              (8388608, "update stopping")]
ART_COVER = ("library_600x900", "library_capsule")
ART_WIDE = ("library_header", "header")
BUSY = "Another Deck Deck Goose window (or the command line) is working on Steam files. Wait for it to finish."


# ------------------------------------------------------------------ helpers
def tree_size(path) -> Tuple[int, int]:
    total = files = 0
    p = lp(path)
    if os.path.islink(p):
        return 0, 1
    if os.path.isfile(p):
        return os.lstat(p).st_size, 1
    stack = [p]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if winplat.is_reparse_point(e.path):
                            files += 1          # a link or junction: never counted through
                        elif e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            files += 1
                            if e.is_file(follow_symlinks=False):
                                total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total, files


def _free(path) -> int:
    p = unlp(str(path))
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    if os.environ.get("DDG_FAKE_WIN_FREE_BYTES"):
        return int(os.environ["DDG_FAKE_WIN_FREE_BYTES"])
    return shutil.disk_usage(p).free


def _within(child, parent) -> bool:
    c, p = norm(child), norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


_WRITE_CACHE: Dict[str, Tuple[float, bool]] = {}


def _can_write(folder: str, fresh: bool = False) -> bool:
    """Can we create a folder here? (os.access means little on Windows, and a
    drive root such as C:\\ lets standard users create folders but not files,
    so the test creates and removes an empty folder.) Cached for 30 s so the
    window doesn't touch every drive for every game on every refresh."""
    key = norm(folder)
    hit = _WRITE_CACHE.get(key)
    if hit and not fresh and time.time() - hit[0] < 30:
        return hit[1]
    probe = os.path.join(folder, ".ddg-write-test-%d" % os.getpid())
    try:
        os.mkdir(lp(probe))
        os.rmdir(lp(probe))
        ok = True
    except FileExistsError:
        try:
            os.rmdir(lp(probe))
        except OSError:
            pass
        ok = True
    except OSError:
        ok = False
    _WRITE_CACHE[key] = (time.time(), ok)
    return ok


_LOCK = threading.local()   # nesting count per thread; other threads must take the file lock


@contextlib.contextmanager
def job_lock():
    """One mover at a time, across threads, windows and the command line."""
    if getattr(_LOCK, "depth", 0) > 0:
        _LOCK.depth += 1
        try:
            yield True
        finally:
            _LOCK.depth -= 1
        return
    config_dir().mkdir(parents=True, exist_ok=True)
    fd = os.open(str(config_dir() / "steam.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    if not winplat.lock_file(fd):
        os.close(fd)
        yield False
        return
    _LOCK.depth = 1
    try:
        yield True
    finally:
        _LOCK.depth = 0
        winplat.unlock_file(fd)
        os.close(fd)


def safe_name(name: str) -> bool:
    """One plain folder name: no path separators, drive colons or tricks."""
    if not name or name in (".", ".."):
        return False
    if any(c in name for c in '/\\:*?"<>|\0'):
        return False
    return name == name.rstrip(" .")   # Windows silently drops trailing dots and spaces


def decode_state(flags: int) -> List[str]:
    return [n for bit, n in STATE_BITS if flags & bit]


# ------------------------------------------------------------ steam itself
def steam_running() -> bool:
    return winplat.steam_running()


def close_steam() -> Step:
    if not steam_running():
        return Step("Close Steam", True, "Steam is not running")
    for root in winplat.steam_roots()[:1]:
        winplat.shutdown_steam(root)
    for _ in range(45):
        if not steam_running():
            return Step("Close Steam", True, "Steam has closed")
        time.sleep(1)
    return Step("Close Steam", False, "Steam is still running. Exit it from the tray icon (right-click, Exit) and try again.")


# --------------------------------------------------------------- libraries
def vdf_files(root: Path) -> List[Path]:
    return [p for p in (root / "config" / "libraryfolders.vdf", root / "steamapps" / "libraryfolders.vdf")
            if p.is_file()]


def read_libraryfolders(root: Path, strict: bool = False) -> dict:
    files = vdf_files(root)
    if not files:
        return {"libraryfolders": {}}
    try:
        data = vdf.load_file(files[0], strict=strict)
        if not isinstance(data.get("libraryfolders", {}), dict):
            raise ValueError("no libraryfolders block")
    except (OSError, ValueError) as e:
        if strict:
            raise ValueError("Steam's library list (%s) could not be read: %s. Start Steam once so it "
                             "rewrites it, then try again." % (files[0], e))
        return {"libraryfolders": {}, "_broken": True}
    data.setdefault("libraryfolders", {})
    return data


def write_libraryfolders(root: Path, data: dict) -> None:
    files = vdf_files(root) or [root / "steamapps" / "libraryfolders.vdf"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for f in files:
        if f.exists():
            shutil.copy2(str(f), str(f.with_name("%s%s-%s" % (f.name, BACKUP, stamp))))
            old = sorted(f.parent.glob(f.name + BACKUP + "-*"))
            for extra in old[:-3]:        # keep the three newest
                extra.unlink()
        vdf.dump_file(data, f)


@dataclass
class Library:
    path: Path
    root: Path

    @property
    def steamapps(self) -> Path:
        return self.path / "steamapps"

    @property
    def available(self) -> bool:
        return self.steamapps.is_dir()


def libraries(root: Path) -> List[Library]:
    data = read_libraryfolders(root)
    out: List[Library] = []
    seen = set()
    for entry in data["libraryfolders"].values():
        if isinstance(entry, dict) and entry.get("path"):
            p = os.path.normpath(entry["path"])
            if norm(p) not in seen:
                seen.add(norm(p))
                out.append(Library(Path(p), root))
    if norm(root) not in seen:
        out.insert(0, Library(root, root))
    return out


def add_library(root: Path, path: Path) -> Step:
    data = read_libraryfolders(root, strict=True)
    folders = data["libraryfolders"]
    if not folders:
        folders["0"] = {"path": os.path.normpath(str(root)), "label": "", "contentid": "",
                        "totalsize": "0", "apps": {}}
    for entry in folders.values():
        if isinstance(entry, dict) and norm(entry.get("path", "")) == norm(path):
            (path / "steamapps").mkdir(parents=True, exist_ok=True)
            return Step("Steam library", True, "%s is already a Steam library" % path)
    (path / "steamapps").mkdir(parents=True, exist_ok=True)
    used = {e.get("contentid") for e in folders.values() if isinstance(e, dict)}
    marker = path / "libraryfolder.vdf"
    contentid, label = "", ""
    if marker.is_file():
        try:
            m = vdf.load_file(marker).get("libraryfolder", {})
            contentid, label = m.get("contentid", ""), m.get("label", "")
        except (OSError, ValueError):
            pass
    while not contentid or contentid in used:
        contentid = str(random.randint(10 ** 17, 9 * 10 ** 18))
    if not marker.is_file():
        vdf.dump_file({"libraryfolder": {"contentid": contentid, "label": label}}, marker)
    n = 0
    while str(n) in folders:
        n += 1
    folders[str(n)] = {"path": os.path.normpath(str(path)), "label": label, "contentid": contentid,
                       "totalsize": "0", "mounted": "1", "apps": {}}
    write_libraryfolders(root, data)
    ok = any(isinstance(e, dict) and norm(e.get("path", "")) == norm(path)
             for e in read_libraryfolders(root)["libraryfolders"].values())
    return Step("Steam library", ok, ("added %s to Steam's library list" % path) if ok
                else "could not read the new library back from libraryfolders.vdf")


def set_app_library(root: Path, appid: str, size: str, src: Path, dst: Path) -> Step:
    data = read_libraryfolders(root, strict=True)
    for entry in data["libraryfolders"].values():
        if not isinstance(entry, dict):
            continue
        apps = entry.setdefault("apps", {})
        here = norm(entry.get("path", ""))
        if here == norm(src):
            apps.pop(appid, None)
        if here == norm(dst):
            apps[appid] = size
    write_libraryfolders(root, data)
    return Step("Steam library list", True, "updated (the previous version is kept as libraryfolders.vdf%s-<date>)" % BACKUP)


# ------------------------------------------------------------------- games
@dataclass
class Game:
    appid: str
    name: str
    installdir: str
    size: int
    state: int
    library: Library
    manifest: Path
    tool: bool = False
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return self.library.steamapps / "common" / self.installdir

    def items(self) -> List[str]:
        if not safe_name(self.installdir) or not self.appid.isdigit():
            return []
        rels = ["common/" + self.installdir, "shadercache/" + self.appid, "compatdata/" + self.appid,
                "workshop/content/" + self.appid, "workshop/appworkshop_%s.acf" % self.appid]
        return [r for r in rels if os.path.lexists(lp(self.library.steamapps / r))]


def is_tool(name: str) -> bool:
    return (name.startswith("Proton") or name.startswith("Steam Linux Runtime")
            or name == "Steamworks Common Redistributables")


def list_games() -> List[Game]:
    games: List[Game] = []
    for root in winplat.steam_roots():
        for lib in libraries(root):
            if not lib.available:
                continue
            for mf in sorted(lib.steamapps.glob("appmanifest_*.acf")):
                try:
                    st = vdf.load_file(mf).get("AppState", {})
                except (OSError, ValueError):
                    continue
                file_id = mf.stem.split("_", 1)[1]
                appid = str(st.get("appid") or file_id)
                if not appid.isdigit() or appid != file_id:
                    continue
                name = st.get("name") or "App %s" % appid
                try:
                    size = int(st.get("SizeOnDisk") or 0)
                    state = int(st.get("StateFlags") or 0)
                except ValueError:
                    size, state = 0, 0
                inst = st.get("installdir") or ""
                g = Game(appid, name, inst, size, state, lib, mf, is_tool(name))
                if not safe_name(inst):
                    g.extra["problem"] = "its manifest has no usable folder name"
                games.append(g)
    count: Dict[str, int] = {}
    for g in games:
        count[g.appid] = count.get(g.appid, 0) + 1
    for g in games:
        if count[g.appid] > 1:
            g.extra["problem"] = ("it is installed in %d libraries at once; let Steam sort that out "
                                  "(or delete one copy) first" % count[g.appid])
    games.sort(key=lambda g: (g.tool, g.name.lower()))
    return games


def shares_folder(game: Game) -> List[str]:
    """Other installed apps in the same library using the same game folder
    (e.g. a single-player and a multiplayer app)."""
    out = []
    for mf in game.library.steamapps.glob("appmanifest_*.acf"):
        if mf == game.manifest:
            continue
        try:
            st = vdf.load_file(mf).get("AppState", {})
        except (OSError, ValueError):
            continue
        if os.path.normcase(st.get("installdir") or "") == os.path.normcase(game.installdir):
            out.append(st.get("name") or mf.stem)
    return out


def find_game(appid: str) -> Optional[Game]:
    return next((g for g in list_games() if g.appid == str(appid)), None)


def art_path(root: Path, appid: str) -> Optional[Path]:
    """Artwork Steam already downloaded (nothing is fetched from the internet)."""
    if not re.fullmatch(r"\d{1,10}", str(appid)):
        return None
    exts = (".jpg", ".png", ".jpeg", ".webp")
    for grid in sorted(root.glob("userdata/*/config/grid")):
        for stem in (appid + "p", appid):
            for ext in exts:
                p = grid / (stem + ext)
                if p.is_file():
                    return p
    cache = root / "appcache" / "librarycache"
    d = cache / appid
    if d.is_dir():
        files = [p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in exts]
        for n in ART_COVER + ART_WIDE:
            hit = [p for p in files if p.stem == n or p.stem.startswith(n + "_")]
            if hit:
                return sorted(hit, key=lambda p: (p.stem != n, str(p)))[0]
        if files:
            return max(files, key=lambda p: p.stat().st_size)
    for n in ART_COVER + ART_WIDE:
        for ext in exts:
            p = cache / ("%s_%s%s" % (appid, n, ext))
            if p.is_file():
                return p
    return None


# ------------------------------------------------------------ drives/targets
def fs_ok_for_games(vol: Dict[str, object]) -> Tuple[bool, str]:
    fs = str(vol.get("fstype", "")).upper()
    kind = str(vol.get("kind", ""))
    if kind in BAD_KINDS:
        return False, "%s drive: not suitable for a Steam library" % (kind if kind != "none" else "missing")
    if vol.get("readonly"):
        return False, "the drive is read-only"
    if fs in winplat.BAD_FS:
        return False, winplat.BAD_FS[fs]
    if fs.startswith("FAT") or fs == "MSDOS":
        return False, "%s cannot hold files over 4 GB, which many games have; use %s" % (fs, winplat.FS_ADVICE)
    if fs not in winplat.GOOD_FS | winplat.WARN_FS:
        return False, "unknown file system (%s)" % (fs or "?")
    return True, ""


def drive_label(path) -> str:
    vol = winplat.volume_of(path)
    if not vol["letter"]:
        return str(path)
    lab = vol["label"] or ("Local Disk" if vol["kind"] == "fixed" else "Removable Disk")
    return "%s (%s)" % (lab, vol["letter"])


def move_targets(game: Game) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    libs = libraries(game.library.root)
    here = norm(game.library.path)
    lib_vols = set()
    for lib in libs:
        vol = winplat.volume_of(lib.path)
        lib_vols.add(norm(vol["root"]) if vol["root"] else "")
        if norm(lib.path) == here:
            continue
        ok, why = fs_ok_for_games(vol)
        if not lib.available:
            ok, why = False, "drive not connected"
        out.append({"path": os.path.normpath(str(lib.path)), "label": "%s - %s" % (
            drive_label(lib.path), lib.path.name or str(lib.path)), "new": False, "ok": ok, "why": why,
            "free": _free(lib.path) if lib.available else 0, "fstype": vol["fstype"]})
    for d in winplat.drives():
        if norm(d.root) in lib_vols:
            continue
        vol = winplat.volume_of(d.root)
        ok, why = fs_ok_for_games(vol)
        target = os.path.join(str(d.root), "SteamLibrary")
        if ok and not _can_write(str(d.root)):
            ok, why = False, "you cannot write to this drive"
        out.append({"path": target, "label": "%s - new SteamLibrary" % d.name, "new": True, "ok": ok,
                    "why": why, "free": _free(d.root) if ok else 0, "fstype": d.fstype})
    return out


def drive_notes(path: str) -> List[str]:
    vol = winplat.volume_of(path)
    notes = []
    if vol["kind"] == "removable":
        notes.append("This is a removable drive. If it is unplugged or gets a different letter, Steam "
                     "shows the game as not installed until it is back.")
    if str(vol["fstype"]).upper() == "EXFAT":
        notes.append("exFAT drive: games usually run, but Windows compression is not available there.")
    return notes


# -------------------------------------------------------------- journal
def _journal() -> Path:
    return config_dir() / "move-journal.json"


def _write_journal(d: dict) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    tmp = _journal().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(_journal()))


def _writable(path: str) -> None:
    """Clear read-only flags (common in games) on a file and its folder."""
    for q in (os.path.dirname(path), path):
        try:
            if not winplat.is_reparse_point(q):
                os.chmod(q, os.stat(q).st_mode | stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        except OSError:
            pass


def _unlink_link(p: str) -> None:
    """Remove a symlink or junction itself, never what it points to."""
    try:
        os.unlink(p)
    except (IsADirectoryError, PermissionError, OSError):
        os.rmdir(p)          # directory junctions/symlinks are removed with rmdir


def _rm(path) -> None:
    """Delete a file or folder tree. Links and junctions inside are removed as
    links and never followed; read-only files are made writable first."""
    p = lp(path)
    if not os.path.lexists(p):
        return
    if winplat.is_reparse_point(p):
        _unlink_link(p)
        return
    if not os.path.isdir(p):
        try:
            os.unlink(p)
        except PermissionError:
            _writable(p)
            os.unlink(p)
        return
    with os.scandir(p) as it:
        entries = [e.path for e in it]
    for e in entries:
        _rm(e)
    try:
        os.rmdir(p)
    except PermissionError:
        _writable(p)
        os.rmdir(p)


def recover_moves() -> List[Step]:
    jf = _journal()
    if not jf.exists():
        return []
    with job_lock() as mine:
        if not mine:
            return []
        return _recover(jf)


def _finish_listing(j: dict, dst: Path) -> None:
    """Make sure Steam's library list points at the new library."""
    try:
        size = vdf.load_file(dst / ("appmanifest_%s.acf" % j["appid"])).get("AppState", {}).get("SizeOnDisk", "0")
        set_app_library(Path(j["root"]), j["appid"], str(size), Path(j["src"]).parent, dst.parent)
    except (OSError, ValueError, KeyError):
        pass   # Steam rebuilds this list itself; the manifest is what matters


def _recover(jf: Path) -> List[Step]:
    try:
        j = json.loads(jf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        jf.unlink()
        return [Step("Interrupted move", False, "journal unreadable; removed it. Check the game in Steam.")]
    src, dst = Path(j["src"]), Path(j["dst"])
    man = "appmanifest_%s.acf" % j["appid"]
    name = "Interrupted move of %s" % j["name"]
    try:
        if j["mode"] == "rename":
            if (dst / man).exists():
                _finish_listing(j, dst)
                _rm(src / (man + MOVING))
                step = Step(name, True, "it had finished; tidied up")
            else:
                for rel in j["items"]:
                    if os.path.lexists(lp(dst / rel)) and not os.path.lexists(lp(src / rel)):
                        os.rename(lp(dst / rel), lp(src / rel))
                if (src / (man + MOVING)).exists() and not (src / man).exists():
                    os.rename(str(src / (man + MOVING)), str(src / man))
                step = Step(name, True, "rolled back to the old drive")
        else:
            phase = j.get("phase")
            if phase == "copying" or (phase == "committing" and not (dst / man).exists()):
                if (src / (man + MOVING)).exists() and not (src / man).exists():
                    os.rename(str(src / (man + MOVING)), str(src / man))
                for rel in j["items"]:
                    _rm(str(dst / rel) + PARTIAL)
                    if os.path.lexists(lp(src / rel)):
                        _rm(dst / rel)
                step = Step(name, True, "rolled back; the game is still on the old drive")
            else:
                for rel in j["items"]:
                    if os.path.lexists(lp(dst / rel)):
                        _rm(src / rel)
                _finish_listing(j, dst)
                _rm(src / (man + MOVING))
                step = Step(name, True, "finished; the game is on the new drive")
    except OSError as e:
        return [Step(name, False, "could not finish tidying up yet (%s). Close the game or any "
                     "window using those files; Deck Deck Goose tries again next time." % e)]
    jf.unlink()
    return [step]


# ------------------------------------------------------------ copy & verify
def _copy_file(s: str, d: str, job: Job, done: List[int]) -> None:
    with open(s, "rb") as fi, open(d, "wb") as fo:
        buf = bytearray(8 * 1024 * 1024)
        mv = memoryview(buf)
        while True:
            n = fi.readinto(buf)
            if not n:
                break
            fo.write(mv[:n])
            done[0] += n
            job.progress(done[0])
            job.check()
    shutil.copystat(s, d)


def _copy_tree(s: str, d: str, job: Job, done: List[int]) -> None:
    if winplat.is_reparse_point(s):
        raise OSError("%s is a link or junction; Deck Deck Goose does not copy those. Use Steam's own "
                      "mover for this game" % unlp(s))
    if os.path.isfile(s):
        _copy_file(s, d, job, done)
        return
    os.mkdir(d)
    with os.scandir(s) as it:
        entries = list(it)
    for e in entries:
        job.check()
        sp, dp = e.path, os.path.join(d, e.name)
        if winplat.is_reparse_point(sp):
            raise OSError("%s is a link or junction; Deck Deck Goose does not copy those. Use Steam's own "
                          "mover for this game" % unlp(sp))
        if e.is_dir(follow_symlinks=False):
            _copy_tree(sp, dp, job, done)
        elif e.is_file(follow_symlinks=False):
            _copy_file(sp, dp, job, done)
    shutil.copystat(s, d)


def _listing(root: str) -> Dict[str, Tuple[str, int]]:
    out: Dict[str, Tuple[str, int]] = {}
    if os.path.isfile(root):
        return {".": ("f", os.lstat(root).st_size)}
    for dp, dns, fns in os.walk(root):
        rel = os.path.relpath(dp, root)
        for n in dns + fns:
            p = os.path.join(dp, n)
            r = os.path.normcase(os.path.normpath(os.path.join(rel, n)))
            if os.path.isdir(p):
                out[r] = ("d", 0)
            elif os.path.isfile(p):
                out[r] = ("f", os.lstat(p).st_size)
    return out


def _verify(s: str, d: str) -> Tuple[bool, str]:
    a, b = _listing(s), _listing(d)
    if a == b:
        return True, "%d files checked" % len(a)
    bad = [k for k in a if a[k] != b.get(k)]
    return False, "%d files differ, e.g. %s" % (len(bad), (bad or list(b))[:3])


# ---------------------------------------------------------------- the move
def check_move(game: Game, target: str) -> Tuple[bool, str, int, str]:
    if steam_running():
        return False, "Close Steam first (it rewrites these files while running).", 0, ""
    if game.extra.get("problem"):
        return False, "Not moved because %s." % game.extra["problem"], 0, ""
    if game.state != 4:
        return False, ("Steam reports this game as: %s. Let Steam finish (or update it) first."
                       % (", ".join(decode_state(game.state)) or "state %d" % game.state)), 0, ""
    if not game.items() or not game.dir.is_dir():
        return False, "The game folder %s is missing." % game.dir, 0, ""
    try:
        read_libraryfolders(game.library.root, strict=True)
    except ValueError as e:
        return False, str(e), 0, ""
    sharing = shares_folder(game)
    if sharing:
        return False, ("%s shares its folder with %s. Moving one would break the other, so use Steam's own "
                       "mover (Properties > Installed Files) for these." % (game.name, ", ".join(sharing))), 0, ""
    dst_lib = Path(os.path.normpath(os.path.abspath(target)))
    if norm(dst_lib) == norm(game.library.path):
        return False, "The game is already in that library.", 0, ""
    if any(part.lower() == "steamapps" for part in dst_lib.parts):
        return False, "Pick the library folder itself (the one that contains steamapps).", 0, ""
    if not dst_lib.parent.is_dir():
        return False, "%s does not exist. Is the drive connected?" % dst_lib.parent, 0, ""
    vol = winplat.volume_of(dst_lib)
    ok, why = fs_ok_for_games(vol)
    if not ok:
        return False, "Can't use %s: %s." % (dst_lib, why), 0, ""
    if not _can_write(str(dst_lib) if dst_lib.is_dir() else str(dst_lib.parent), fresh=True):
        return False, "You cannot write to %s." % dst_lib, 0, ""
    dst = dst_lib / "steamapps"
    if (dst / game.manifest.name).exists():
        return False, "That library already has a copy of this game.", 0, ""
    for rel in game.items():
        if os.path.lexists(lp(dst / rel)):
            return False, ("%s already exists in the target library. Remove or rename it first "
                           "(Clear junk lists leftovers)." % (dst / rel)), 0, ""
    probe = str(dst_lib)
    while not os.path.exists(probe):
        probe = os.path.dirname(probe)
    same = os.stat(lp(game.library.steamapps)).st_dev == os.stat(lp(probe)).st_dev
    if same and not os.environ.get("DDG_FORCE_COPY"):
        return True, "", 0, "rename"
    need = sum(tree_size(game.library.steamapps / rel)[0] for rel in game.items())
    free = _free(probe)
    if need + 2 ** 30 > free:
        return False, ("Not enough space: needs %s plus 1 GB spare, %s free."
                       % (human_size(need), human_size(free))), need, "copy"
    return True, "", need, "copy"


def _cross_device(e: OSError) -> bool:
    return e.errno == errno.EXDEV or getattr(e, "winerror", 0) == 17


def move_game(game: Game, target: str, job: Job) -> List[Step]:
    with job_lock() as mine:
        if not mine:
            return [Step("Move %s" % game.name, False, BUSY)]
        return _move_game(game, target, job)


def _move_game(game: Game, target: str, job: Job) -> List[Step]:
    steps: List[Step] = list(recover_moves())
    if _journal().exists():
        return steps + [Step("Move %s" % game.name, False, "An earlier move still needs tidying up (see "
                             "above). Close anything using those files and start Deck Deck Goose again first.")]
    ok, why, need, mode = check_move(game, target)
    if not ok:
        return steps + [Step("Move %s" % game.name, False, why)]
    src = game.library.steamapps
    dst_lib = Path(os.path.normpath(os.path.abspath(target)))
    dst = dst_lib / "steamapps"
    items = game.items()
    man = game.manifest.name
    root = game.library.root
    journal = {"appid": game.appid, "name": game.name, "root": str(root), "src": str(src),
               "dst": str(dst), "items": items, "mode": mode, "phase": "copying"}
    job.progress(0, need, "Preparing")
    dst.mkdir(parents=True, exist_ok=True)
    for rel in items:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)

    if mode == "rename":
        _write_journal(journal)
        moved: List[str] = []
        try:
            for rel in items:
                job.check()
                job.progress(message="Moving %s" % rel)
                os.rename(lp(src / rel), lp(dst / rel))
                moved.append(rel)
        except (Cancelled, OSError) as e:
            for rel in reversed(moved):
                os.rename(lp(dst / rel), lp(src / rel))
            _journal().unlink()
            if isinstance(e, OSError) and _cross_device(e):
                os.environ["DDG_FORCE_COPY"] = "1"
                try:
                    return steps + _move_game(game, target, job)
                finally:
                    os.environ.pop("DDG_FORCE_COPY", None)
            if isinstance(e, Cancelled):
                raise
            return steps + [Step("Move %s" % game.name, False,
                                 "could not move (%s). Nothing was changed. Is the game or a folder of it "
                                 "open somewhere?" % e)]
        steps.append(Step("Moved folders (same drive, instant)", True, ", ".join(items)))
    else:
        _write_journal(journal)
        done = [0]
        try:
            for rel in items:
                job.progress(message="Copying %s" % rel)
                part = str(dst / rel) + PARTIAL
                _rm(part)
                _copy_tree(lp(src / rel), lp(part), job, done)
            job.progress(message="Checking the copy")
            for rel in items:
                good, detail = _verify(lp(src / rel), lp(str(dst / rel) + PARTIAL))
                if not good:
                    raise OSError("copy check failed for %s: %s" % (rel, detail))
            for rel in items:
                os.rename(lp(str(dst / rel) + PARTIAL), lp(dst / rel))
        except (Cancelled, OSError) as e:
            for rel in items:
                _rm(str(dst / rel) + PARTIAL)
                if os.path.lexists(lp(src / rel)):
                    _rm(dst / rel)
            _journal().unlink()
            if isinstance(e, Cancelled):
                raise
            return steps + [Step("Copy to %s" % dst_lib, False,
                                 "%s. The new copy was removed; the game is untouched." % e)]
        steps.append(Step("Copied and checked", True, "%s, every file compared" % human_size(done[0])))

    if steam_running():
        # Steam was started during the copy; it could overwrite our changes. Undo.
        if mode == "rename":
            for rel in reversed(items):
                os.rename(lp(dst / rel), lp(src / rel))
        else:
            for rel in items:
                if os.path.lexists(lp(src / rel)):
                    _rm(dst / rel)
        _journal().unlink()
        return steps + [Step("Move %s" % game.name, False, "Steam was started while the game was being "
                             "moved, so the move was undone (the game is untouched). Close Steam and try again.")]
    job.progress(message="Telling Steam where the game is")
    journal["phase"] = "committing"
    _write_journal(journal)
    lib_step = add_library(root, dst_lib)
    steps.append(lib_step)
    if not lib_step.ok:
        recover_moves()
        return steps + [Step("Move %s" % game.name, False, "rolled back: " + lib_step.detail)]
    os.replace(str(src / man), str(src / (man + MOVING)))   # replace: Windows rename won't overwrite
    shutil.copy2(str(src / (man + MOVING)), str(dst / (man + ".tmp")))
    os.replace(str(dst / (man + ".tmp")), str(dst / man))
    steps.append(set_app_library(root, game.appid, str(game.size), game.library.path, dst_lib))
    journal["phase"] = "cleanup"
    _write_journal(journal)
    try:
        if mode == "copy":
            job.progress(message="Removing the old copy")
            missing = [rel for rel in items if not os.path.lexists(lp(dst / rel))]
            try:
                ours = json.loads(_journal().read_text(encoding="utf-8")) == journal
            except (OSError, ValueError):
                ours = False
            if missing or not ours:
                raise OSError("safety stop: the new copy is incomplete (%s) or the move record changed, so the "
                              "old copy was kept" % (", ".join(missing) or "record"))
            for rel in items:
                _rm(src / rel)
        _rm(src / (man + MOVING))
        _journal().unlink()
        steps.append(Step("Old copy removed" if mode == "copy" else "Manifest moved", True,
                          "the game now lives in %s" % dst_lib))
    except OSError as e:
        steps.append(Step("Old copy", False, "The game was moved and works from %s, but part of the old "
                          "copy could not be deleted yet (%s). Close anything using it; Deck Deck Goose "
                          "finishes the clean-up next time it starts." % (dst_lib, e)))
    if mode == "copy" and (game.appid in records("compressed") or game.appid in records("estimates")):
        _record("compressed", game.appid, None)
        _record("estimates", game.appid, None)
        steps.append(Step("Compression", True, "Copied files are stored uncompressed on the new drive. "
                          "Press Estimate there if you want to compress the game again."))
    for note in drive_notes(str(dst_lib)):
        steps.append(Step("Note about this drive", True, note))
    steps.append(Step("Next", True, "Start Steam. The game should show as installed. If it asks to "
                      "install, choose the new library: Steam finds the files and only checks them."))
    return steps


# -------------------------------------------------------------------- junk
def _all_steamapps() -> List[Tuple[Path, Path]]:
    return [(root, lib.steamapps) for root in winplat.steam_roots() for lib in libraries(root)
            if lib.available]


def scan_junk() -> List[Dict[str, object]]:
    games = list_games()
    installed = {g.appid for g in games}
    in_use = {norm(g.dir) for g in games if g.items()}
    busy = {g.appid for g in games if g.state != 4}
    names = {g.appid: g.name for g in games}
    out: List[Dict[str, object]] = []

    def add(cat: str, path: Path, label: str, default: bool, warn: str = "") -> None:
        size, _ = tree_size(path)
        out.append({"id": "%s:%s" % (cat, path), "category": cat, "path": str(path), "label": label,
                    "size": size, "human": human_size(size), "default": default, "warning": warn})

    roots_done = set()
    for root, sa in _all_steamapps():
        for p in sorted((sa / "shadercache").glob("*")):
            if not p.name.isdigit():
                continue
            if p.name in installed:
                add("shader", p, "Shader cache: %s" % names[p.name], False,
                    "Rebuilt on next launch; the first minutes may stutter.")
            else:
                add("shader-orphan", p, "Shader cache of uninstalled game %s" % p.name, True)
        for sub in ("downloading", "temp"):
            for p in sorted((sa / sub).glob("*")):
                if p.name not in busy:
                    add("download", p, "Leftover from an old download/update (%s)" % p.name, True)
        for pat in ("common/*", "shadercache/*", "compatdata/*", "workshop/*", "workshop/content/*"):
            for p in sorted(sa.glob(pat + PARTIAL)):
                add("partial", p, "Unfinished copy from Deck Deck Goose", True)
        for p in sorted((sa / "common").glob("*")):
            if p.is_dir() and not p.name.endswith(PARTIAL) and norm(p) not in in_use:
                add("orphan-folder", p, "Game folder with no installed game: %s" % p.name, False,
                    "Left behind after uninstalling. May hold mods, settings or saves.")
        for g in games:
            if g.library.steamapps == sa and g.items():
                cr = g.dir / "_CommonRedist"
                if cr.is_dir():
                    add("redist", cr, "Installers for DirectX / Visual C++ in %s" % g.name, False,
                        "Only needed once, when the game is first set up. Steam puts them back if you "
                        "verify the game's files.")
        for p in sorted((sa / "compatdata").glob("*")):
            if p.name.isdigit() and int(p.name) < SHORTCUT_MIN_ID and p.name not in installed:
                add("orphan-prefix", p, "Compatibility data of uninstalled game %s" % p.name, False,
                    "MAY HOLD SAVE GAMES. Only remove if the game used Steam Cloud or you are sure.")
        if norm(root) not in roots_done:
            roots_done.add(norm(root))
            for rel, label in (("appcache/httpcache", "Steam web download cache"),
                               ("logs", "Steam log files"), ("dumps", "Steam crash dumps")):
                p = root / rel
                if p.is_dir() and tree_size(p)[0] > 0:
                    add("steam-cache", p, label, True)
    out.sort(key=lambda d: (not d["default"], -int(d["size"])))
    return out


def clean_junk(ids: List[str], job: Job) -> List[Step]:
    with job_lock() as mine:
        if not mine:
            return [Step("Clean junk", False, BUSY)]
        return _clean_junk(ids, job)


def _clean_junk(ids: List[str], job: Job) -> List[Step]:
    if steam_running():
        return [Step("Clean junk", False, "Close Steam first.")]
    found = {d["id"]: d for d in scan_junk()}
    allowed = [sa for _, sa in _all_steamapps()] + list(winplat.steam_roots())
    picked = [found[i] for i in ids if i in found]
    total = sum(int(d["size"]) for d in picked)
    steps: List[Step] = []
    freed = 0
    job.progress(0, total, "Cleaning")
    for d in picked:
        if job.cancel_event.is_set():
            steps.append(Step("Stopped", False, "Cancelled after removing %d of %d items." % (
                sum(1 for x in steps if x.ok), len(picked))))
            break
        p = Path(str(d["path"]))
        if winplat.is_reparse_point(str(p)) and any(_within(p.parent, a) for a in allowed):
            try:
                os.unlink(lp(p))      # a file link or a symlink
            except (IsADirectoryError, PermissionError):
                os.rmdir(lp(p))       # a junction: removes only the link
            steps.append(Step(str(d["label"]), True, "removed a link (its target was left alone)"))
            continue
        if not any(_within(p, a) for a in allowed) or any(norm(p) == norm(a) for a in allowed):
            steps.append(Step(str(d["label"]), False, "refused: outside Steam's folders"))
            continue
        try:
            if d["category"] == "steam-cache":
                for child in p.iterdir():
                    _rm(child)
            else:
                _rm(p)
            freed += int(d["size"])
            steps.append(Step(str(d["label"]), True, "removed %s" % d["human"]))
        except OSError as e:
            steps.append(Step(str(d["label"]), False, "%s (in use?)" % e))
        job.progress(freed)
    if not picked:
        steps.append(Step("Clean junk", True, "nothing selected"))
    else:
        steps.append(Step("Total", True, "freed about %s" % human_size(freed)))
    return steps


# -------------------------------------------------------------- compression
def compression_info(path) -> Dict[str, object]:
    vol = winplat.volume_of(path)
    if not winplat.ALGORITHMS:
        return {"available": False, "fstype": vol["fstype"],
                "why": "Compression is not available on %s." % winplat.PLATFORM_NAME}
    if str(vol["fstype"]).upper() != "NTFS":
        return {"available": False, "fstype": vol["fstype"],
                "why": "Windows compression needs an NTFS drive (this one is %s)." % (vol["fstype"] or "?")}
    return {"available": True, "fstype": "NTFS", "why": ""}


def records(key: str) -> Dict[str, dict]:
    return load_config().get(key, {})


def _record(key: str, appid: str, value: Optional[dict]) -> None:
    if value is None:
        update_config(drop_from=(key, appid))
    else:
        update_config(merge_key=key, merge={appid: value})


def _game_ok(game: Game, name: str) -> Optional[Step]:
    if game.extra.get("problem") or not game.items() or not game.dir.is_dir():
        return Step(name, False, "Not possible: %s." % (game.extra.get("problem") or "game folder missing"))
    if game.state != 4:
        return Step(name, False, "Only fully installed games (%s)." % ", ".join(decode_state(game.state)))
    ci = compression_info(game.dir)
    if not ci["available"]:
        return Step(name, False, str(ci["why"]))
    return None


def estimate_game(game: Game, job: Job, algo: str = winplat.DEFAULT_ALGO) -> List[Step]:
    name = "Estimate %s" % game.name
    bad = _game_ok(game, name)
    if bad:
        return [bad]
    job.progress(0, 1, "Sampling files")
    est = estimate.estimate(lp(game.dir), algo, on_disk=winplat.compressed_size,
                            progress=lambda d, t: job.progress(d, t), check=job.check)
    rec = {k: est[k] for k in ("total", "saved", "pct", "verdict", "already", "skipped_formats",
                               "worth_bytes", "algo", "measured", "directstorage")}
    rec["date"] = time.strftime("%Y-%m-%d")
    _record("estimates", game.appid, rec)
    detail = ("with %s: about %s more (%.1f%%) - %s. Already saved by Windows compression: %s. Skipped as "
              "video/audio/images/archives: %s." % (algo, human_size(int(est["saved"])), est["pct"],
                                                     est["verdict"], human_size(int(est["already"])),
                                                     human_size(int(est["skipped_formats"]))))
    if est["directstorage"]:
        detail += (" This game uses DirectStorage: compressing it can make loading slower, so "
                   "Deck Deck Goose will not compress it unless you force it on the command line.")
    return [Step(name, True, detail)]


def compress_game(game: Game, job: Job, algo: str = winplat.DEFAULT_ALGO, force: bool = False) -> List[Step]:
    with job_lock() as mine:
        if not mine:
            return [Step("Compress %s" % game.name, False, BUSY)]
        return _compress_game(game, job, algo, force)


def _compress_game(game: Game, job: Job, algo: str, force: bool) -> List[Step]:
    name = "Compress %s" % game.name
    if not winplat.ALGORITHMS:
        return [Step(name, False, "Compression is not available on %s." % winplat.PLATFORM_NAME)]
    if algo not in winplat.ALGORITHMS:
        return [Step(name, False, "unknown algorithm %s" % algo)]
    bad = _game_ok(game, name)
    if bad:
        return [bad]
    if steam_running():
        return [Step(name, False, "Close Steam first (files in use cannot be compressed).")]
    job.progress(0, 1, "Working out which files are worth compressing")
    est = estimate.estimate(lp(game.dir), algo, on_disk=winplat.compressed_size, check=job.check)
    if est["directstorage"] and not force:
        return [Step(name, False, "This game uses DirectStorage; compressing it can make loading slower. "
                     "Not compressed (the command line has --force).")]
    files = list(est["worth_files"])
    sizes = dict(estimate.walk_files(lp(game.dir)))
    total = sum(sizes.get(f, 0) for f in files)
    before = estimate.on_disk_total(lp(game.dir), winplat.compressed_size)[1]
    job.progress(0, total, "Compressing %d of %d files with %s" % (len(files), est["files"], algo))
    errors: List[str] = []
    long_paths = [f for f in files if len(unlp(f)) >= 260]
    skip = set(long_paths)
    files = [f for f in files if f not in skip]   # compact.exe may not cope with them
    done = 0
    cancelled = False
    for i in range(0, len(files), 100):
        if job.cancel_event.is_set():
            cancelled = True
            break
        part = files[i:i + 100]
        errors += winplat.compact([unlp(f) for f in part], algo)
        done += sum(sizes.get(f, 0) for f in part)
        job.progress(done)
    after = estimate.on_disk_total(lp(game.dir), winplat.compressed_size)[1]
    saved = max(0, before - after)
    steps = []
    if cancelled:
        steps.append(Step(name, False, "Cancelled part-way: some files are compressed (%s saved so far). "
                          "Uncompress undoes it." % human_size(saved)))
    elif errors:
        steps.append(Step(name, False, "%d problem(s), e.g. %s. %s was saved anyway." % (
            len(errors), errors[0], human_size(saved))))
    elif not files:
        steps.append(Step(name, True, "nothing worth compressing: files are already compressed by their "
                          "format or by Windows"))
    else:
        steps.append(Step(name, True, "compressed %d files with %s; skipped %d that would not shrink. "
                          "Measured saving: %s (estimate was %s)." % (
                              len(files), algo, est["files"] - len(files), human_size(saved),
                              human_size(int(est["saved"])))))
    if long_paths:
        steps.append(Step("Very long paths", True, "%d file(s) with paths of 260+ characters were left "
                          "uncompressed." % len(long_paths)))
    steps.append(Step("After game updates", True, "Files that Steam updates are stored uncompressed again. "
                      "Press Estimate now and then; if it says worth it again, compress again."))
    if saved > 0 or (files and not cancelled):
        _record("compressed", game.appid, {"date": time.strftime("%Y-%m-%d"), "saved": saved, "algo": algo})
    _record("estimates", game.appid, None)
    return steps


def uncompress_game(game: Game, job: Job) -> List[Step]:
    with job_lock() as mine:
        if not mine:
            return [Step("Uncompress %s" % game.name, False, BUSY)]
        name = "Uncompress %s" % game.name
        bad = _game_ok(game, name)
        if bad:
            return [bad]
        if steam_running():
            return [Step(name, False, "Close Steam first.")]
        files, need = [], 0
        for p, s in estimate.walk_files(lp(game.dir)):
            disk = winplat.compressed_size(p)
            if disk is not None and disk < s * estimate.ALREADY:
                files.append(p)
                need += s - disk
        if need + 2 ** 30 > _free(game.dir):
            return [Step(name, False, "Not enough free space to uncompress (needs %s more)." % human_size(need))]
        job.progress(0, len(files), "Uncompressing %d files" % len(files))
        errors: List[str] = []
        for i in range(0, len(files), 100):
            if job.cancel_event.is_set():
                return [Step(name, False, "Cancelled part-way; some files are still compressed. Press "
                             "Uncompress again to finish.")]
            errors += winplat.compact([unlp(f) for f in files[i:i + 100]], None)
            job.progress(min(len(files), i + 100))
        if errors:
            return [Step(name, False, "%d problem(s), e.g. %s" % (len(errors), errors[0]))]
        _record("compressed", game.appid, None)
        return [Step(name, True, "%d files stored uncompressed again (%s used)" % (len(files), human_size(need)))]


# ------------------------------------------------------------------ for UI
def games_status() -> Dict[str, object]:
    recovered = [s.as_dict() for s in recover_moves()]
    roots = winplat.steam_roots()
    games = list_games()
    comp, ests = records("compressed"), records("estimates")
    libs = []
    for root in roots:
        for lib in libraries(root):
            vol = winplat.volume_of(lib.path)
            libs.append({"path": str(lib.path), "drive": drive_label(lib.path), "available": lib.available,
                         "free": human_size(_free(lib.path)) if lib.available else "-",
                         "fstype": vol["fstype"] if lib.available else "-",
                         "can_compress": lib.available and compression_info(lib.path)["available"]})
    can = {norm(l["path"]): l["can_compress"] for l in libs}
    return {
        "roots": [{"path": str(r)} for r in roots],
        "running": steam_running(),
        "simulated": winplat.SIMULATED and not os.environ.get("DDG_SCREENSHOT"),
        "recovered": recovered,
        "libraries": libs,
        "platform": winplat.PLATFORM,
        "platform_name": winplat.PLATFORM_NAME,
        "algorithms": list(winplat.ALGORITHMS),
        "default_algo": winplat.DEFAULT_ALGO,
        "games": [{"appid": g.appid, "name": g.name, "size": human_size(g.size) if g.size else "?",
                   "bytes": g.size, "tool": g.tool, "state": g.state,
                   "state_text": g.extra.get("problem") or (", ".join(decode_state(g.state)) if g.state != 4 else ""),
                   "library": str(g.library.path), "drive": drive_label(g.library.path),
                   "art": bool(art_path(g.library.root, g.appid)),
                   "compressed": comp.get(g.appid), "estimate": ests.get(g.appid),
                   "can_compress": can.get(norm(g.library.path), False),
                   "targets": move_targets(g)} for g in games],
    }
