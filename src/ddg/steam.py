# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Steam game manager: find games and their artwork, move a game (with its
Proton prefix, shader cache and workshop items) to another drive so Steam
finds it there, clean junk, and compress games on btrfs.

How a move works (Steam must be closed):
  1. Copy (other drive) or rename (same drive) the game's folders into the
     target library's steamapps/, into "*.thispc-partial" names first, then
     check every file arrived (count, sizes, symlink targets).
  2. Commit: put appmanifest_<id>.acf in the target library, make sure the
     target library is listed in Steam's libraryfolders.vdf (both copies),
     and update the "apps" lists there.
  3. Only then delete the old copy.
The manifest itself holds no paths (only the folder name "installdir"), so
moving it to the new library's steamapps/ is what tells Steam where the
game is. No symlink is needed. A journal file lets an interrupted move be
finished or rolled back the next time the program starts.
"""
from __future__ import annotations

import contextlib
import errno
try:
    import fcntl
except ImportError:  # Windows: this module's Linux-only parts are never used there
    fcntl = None
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

from . import core, vdf
from .core import Step
from .jobs import Cancelled, Job

FLATPAK_STEAM = "com.valvesoftware.Steam"
PARTIAL = ".thispc-partial"
MOVING = ".thispc-moving"
BACKUP = ".thispc-backup"
SHORTCUT_MIN_ID = 2 ** 31   # non-Steam shortcuts use ids with the top bit set
BLOCKED_FS = {"vfat", "msdos", "exfat", "fat", "fat32"}
NTFS_FS = {"ntfs", "ntfs3", "fuseblk"}
# Never put a library here: RAM disks, system/pseudo file systems, read-only images
UNSAFE_FS = {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs", "squashfs", "iso9660", "udf",
             "overlay", "composefs", "cifs", "smb3", "nfs", "nfs4", "sshfs", "fuse.sshfs"}
STATE_BITS = [(2, "update required"), (8, "encrypted"), (16, "locked"),
              (32, "files missing"), (64, "running"), (128, "files corrupt"),
              (256, "updating"), (512, "update paused"), (1024, "update started"),
              (2048, "uninstalling"), (4096, "backup running"), (65536, "reconfiguring"),
              (131072, "validating"), (262144, "adding files"), (524288, "preallocating"),
              (1048576, "downloading"), (2097152, "staging"), (4194304, "committing"),
              (8388608, "update stopping")]
ART_COVER = ("library_600x900", "library_capsule")
ART_WIDE = ("library_header", "header")


# ------------------------------------------------------------------ helpers
def tree_size(path: Path) -> Tuple[int, int]:
    """(bytes, files) under path, not following symlinks."""
    total = files = 0
    p = str(path)
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
                        if e.is_dir(follow_symlinks=False):
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


def human(n: int) -> str:
    return core.human_size(n) if n >= 10 ** 7 else "%d MB" % max(0, round(n / 1e6)) if n >= 10 ** 5 else "%d kB" % max(0, round(n / 1e3))


def _unescape_mount(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def fs_info(path) -> Dict[str, str]:
    """File system type, mount point and options for a path."""
    src = os.environ.get("THISPC_MOUNTS_FILE", "/proc/self/mounts")
    p = os.path.realpath(str(path))
    while not os.path.exists(p) and p != "/":
        p = os.path.dirname(p)
    best = {"fstype": "unknown", "mountpoint": "/", "options": "", "device": ""}
    try:
        lines = Path(src).read_text().splitlines()
    except OSError:
        return best
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        mp = _unescape_mount(parts[1])
        if (p == mp or p.startswith(mp.rstrip("/") + "/") or mp == "/") and len(mp) >= len(best["mountpoint"]):
            best = {"device": parts[0], "mountpoint": mp, "fstype": parts[2], "options": parts[3]}
    return best


def fs_ok_for_games(fstype: str) -> Tuple[bool, str]:
    if fstype in BLOCKED_FS:
        return False, ("%s cannot hold Linux games: it has no symlinks or Linux permissions, "
                       "which Proton prefixes need. Use a disk formatted as ext4 or btrfs." % fstype)
    if fstype in NTFS_FS:
        return False, ("This looks like an NTFS (Windows) disk. Proton games on NTFS often fail "
                       "(see the Proton FAQ). Blocked by default; the command line has --allow-ntfs.")
    return True, ""


def _free(path) -> int:
    p = str(path)
    while not os.path.exists(p) and p != "/":
        p = os.path.dirname(p)
    override = os.environ.get("THISPC_FAKE_FREE_BYTES")
    if override:
        return int(override)
    return shutil.disk_usage(p).free


def _within(child: Path, parent: Path) -> bool:
    try:
        Path(os.path.realpath(str(child))).relative_to(os.path.realpath(str(parent)))
        return True
    except ValueError:
        return False


_LOCK = threading.local()   # nesting count per thread; other threads must take the file lock


@contextlib.contextmanager
def job_lock():
    """Only one window or command line may change Steam files at a time.
    Yields True if we hold the lock, False if someone else does."""
    if getattr(_LOCK, "depth", 0) > 0:
        _LOCK.depth += 1
        try:
            yield True
        finally:
            _LOCK.depth -= 1
        return
    core.config_dir().mkdir(parents=True, exist_ok=True)
    fd = os.open(str(core.config_dir() / "steam.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        yield False
        return
    _LOCK.depth = 1
    try:
        yield True
    finally:
        _LOCK.depth = 0
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


BUSY = "Another Deck Deck Goose window (or the command line) is working on Steam files. Wait for it to finish."


def safe_name(name: str) -> bool:
    """A single folder name, no path tricks."""
    return bool(name) and name not in (".", "..") and "/" not in name and "\0" not in name


# ----------------------------------------------------------- steam & state
def steam_roots() -> List[Tuple[Path, bool]]:
    """Steam installs found in this home: [(path, is_flatpak)]."""
    h = core.home()
    cands = [(h / ".local/share/Steam", False), (h / ".steam/steam", False),
             (h / ".steam/root", False), (h / ".steam/debian-installation", False),
             (h / ".var/app" / FLATPAK_STEAM / ".local/share/Steam", True),
             (h / ".var/app" / FLATPAK_STEAM / "data/Steam", True),
             (h / "snap/steam/common/.local/share/Steam", False)]
    seen, out = set(), []
    for p, flat in cands:
        if not ((p / "steamapps").is_dir() or (p / "config/libraryfolders.vdf").is_file()):
            continue
        real = os.path.realpath(str(p))
        if real in seen:
            continue
        seen.add(real)
        out.append((Path(real), flat))
    return out


def steam_running() -> bool:
    if os.environ.get("THISPC_FAKE_TOOLS"):
        try:
            st = json.loads((core.home() / ".thispc-fake-state.json").read_text())
            return bool(st.get("steam_running"))
        except (OSError, ValueError):
            return bool(os.environ.get("THISPC_FAKE_STEAM_RUNNING"))
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            comm = Path("/proc", pid, "comm").read_text().strip()
        except OSError:
            continue
        if comm in ("steam", "steam.sh"):
            return True
    return False


def close_steam() -> Step:
    if not steam_running():
        return Step("Close Steam", True, "Steam is not running")
    roots = steam_roots()
    if roots and all(flat for _, flat in roots):
        core.run(["flatpak", "run", FLATPAK_STEAM, "-shutdown"], timeout=30)
    else:
        core.run(["steam", "-shutdown"], timeout=30)
    for _ in range(45):
        if not steam_running():
            return Step("Close Steam", True, "Steam has closed")
        time.sleep(1)
    return Step("Close Steam", False, "Steam is still running. Quit it from its menu (Steam > Exit) "
                "and try again. In Game Mode, switch to Desktop Mode first.")


def decode_state(flags: int) -> List[str]:
    return [name for bit, name in STATE_BITS if flags & bit]


# --------------------------------------------------------------- libraries
def vdf_files(root: Path) -> List[Path]:
    return [p for p in (root / "config/libraryfolders.vdf", root / "steamapps/libraryfolders.vdf") if p.is_file()]


def read_libraryfolders(root: Path, strict: bool = False) -> dict:
    """Steam's library list. strict=True raises ValueError if it is unreadable
    (used before changing it); otherwise an unreadable list counts as empty."""
    files = vdf_files(root)
    if not files:
        return {"libraryfolders": {}}
    try:
        data = vdf.load_file(files[0], strict=strict)
        if not isinstance(data.get("libraryfolders", {}), dict):
            raise ValueError("no libraryfolders block")
    except (OSError, ValueError) as e:
        if strict:
            raise ValueError("Steam's library list (%s) could not be read: %s. Start Steam once "
                             "so it rewrites it, then try again." % (files[0], e))
        return {"libraryfolders": {}, "_broken": True}
    data.setdefault("libraryfolders", {})
    return data


def write_libraryfolders(root: Path, data: dict) -> None:
    files = vdf_files(root) or [root / "steamapps/libraryfolders.vdf"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for f in files:
        if f.exists():
            shutil.copy2(str(f), str(f.with_name("%s%s-%s" % (f.name, BACKUP, stamp))))
            for extra in sorted(f.parent.glob(f.name + BACKUP + "-*"))[:-3]:   # keep the three newest
                extra.unlink()
        f.parent.mkdir(parents=True, exist_ok=True)
        vdf.dump_file(data, f)


@dataclass
class Library:
    path: Path
    root: Path
    flatpak: bool
    key: str = ""

    @property
    def steamapps(self) -> Path:
        return self.path / "steamapps"

    @property
    def available(self) -> bool:
        return self.steamapps.is_dir()


def libraries(root: Path, flatpak: bool) -> List[Library]:
    data = read_libraryfolders(root)
    out: List[Library] = []
    seen = set()
    for key, entry in data["libraryfolders"].items():
        if isinstance(entry, dict) and entry.get("path"):
            real = os.path.realpath(entry["path"])
            if real not in seen:
                seen.add(real)
                out.append(Library(Path(real), root, flatpak, key))
    if os.path.realpath(str(root)) not in seen:
        out.insert(0, Library(root, root, flatpak, ""))
    return out


def add_library(root: Path, path: Path) -> Step:
    """Register path as a Steam library (Steam must be closed)."""
    data = read_libraryfolders(root, strict=True)
    folders = data["libraryfolders"]
    if not folders:   # no list yet: Steam's own folder must be entry "0"
        folders["0"] = {"path": os.path.realpath(str(root)), "label": "", "contentid": "",
                        "totalsize": "0", "apps": {}}
    real = os.path.realpath(str(path))
    for entry in folders.values():
        if isinstance(entry, dict) and os.path.realpath(entry.get("path", "")) == real:
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
    folders[str(n)] = {"path": real, "label": label, "contentid": contentid,
                       "totalsize": "0", "mounted": "1", "apps": {}}
    write_libraryfolders(root, data)
    ok = any(isinstance(e, dict) and os.path.realpath(e.get("path", "")) == real
             for e in read_libraryfolders(root)["libraryfolders"].values())
    return Step("Steam library", ok, ("added %s to Steam's library list" % path) if ok
                else "could not read the new library back from libraryfolders.vdf")


def set_app_library(root: Path, appid: str, size: str, src: Path, dst: Path) -> Step:
    data = read_libraryfolders(root, strict=True)
    rs, rd = os.path.realpath(str(src)), os.path.realpath(str(dst))
    for entry in data["libraryfolders"].values():
        if not isinstance(entry, dict):
            continue
        apps = entry.setdefault("apps", {})
        here = os.path.realpath(entry.get("path", ""))
        if here == rs:
            apps.pop(appid, None)
        if here == rd:
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
        """Paths (relative to steamapps) that belong to this game and exist."""
        if not safe_name(self.installdir) or not self.appid.isdigit():
            return []
        rels = ["common/" + self.installdir, "compatdata/" + self.appid,
                "shadercache/" + self.appid, "workshop/content/" + self.appid,
                "workshop/appworkshop_%s.acf" % self.appid]
        return [r for r in rels if os.path.lexists(str(self.library.steamapps / r))]


def is_tool(name: str) -> bool:
    return (name.startswith("Proton") or name.startswith("Steam Linux Runtime")
            or name == "Steamworks Common Redistributables")


def list_games(include_missing_libraries: bool = False) -> List[Game]:
    games: List[Game] = []
    for root, flat in steam_roots():
        for lib in libraries(root, flat):
            if not lib.available:
                continue
            for mf in sorted(lib.steamapps.glob("appmanifest_*.acf")):
                try:
                    st = vdf.load_file(mf).get("AppState", {})
                except (OSError, ValueError):
                    continue
                appid = str(st.get("appid") or mf.stem.split("_", 1)[1])
                if not appid.isdigit() or appid != mf.stem.split("_", 1)[1]:
                    continue   # manifest name and contents disagree: leave it to Steam
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
    """Other installed apps in the same library using the same game folder."""
    out = []
    for mf in game.library.steamapps.glob("appmanifest_*.acf"):
        if mf == game.manifest:
            continue
        try:
            st = vdf.load_file(mf).get("AppState", {})
        except (OSError, ValueError):
            continue
        if (st.get("installdir") or "") == game.installdir:
            out.append(st.get("name") or mf.stem)
    return out


def game_in_use(game: Game) -> bool:
    """Is any program running from, or holding files open inside, the game folder?"""
    fake = os.environ.get("THISPC_FAKE_GAME_IN_USE")
    if fake is not None:
        return game.appid in fake.split(",")
    target = os.path.realpath(str(game.dir)) + os.sep
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        base = os.path.join("/proc", pid)
        try:
            for link in ("cwd", "exe"):
                if (os.path.realpath(os.path.join(base, link)) + os.sep).startswith(target):
                    return True
            for fd in os.listdir(os.path.join(base, "fd")):
                if os.readlink(os.path.join(base, "fd", fd)).startswith(target):
                    return True
        except OSError:
            continue   # other users' processes, or gone already
    return False


def find_game(appid: str) -> Optional[Game]:
    return next((g for g in list_games() if g.appid == str(appid)), None)


def art_path(root: Path, appid: str, wide: bool = False) -> Optional[Path]:
    """Artwork Steam has already downloaded for this game (no network use)."""
    if not re.fullmatch(r"\d{1,10}", str(appid)):
        return None
    names = ART_WIDE + ART_COVER if wide else ART_COVER + ART_WIDE
    exts = (".jpg", ".png", ".jpeg", ".webp")
    # 1. Custom artwork the user picked in Steam (or via SteamGridDB)
    for grid in sorted(root.glob("userdata/*/config/grid")):
        for stem in ([appid, appid + "p"] if wide else [appid + "p", appid]):
            for ext in exts:
                p = grid / (stem + ext)
                if p.is_file():
                    return p
    cache = root / "appcache/librarycache"
    # 2. Newer Steam: librarycache/<appid>/<hash>/library_600x900.jpg (and similar)
    d = cache / appid
    if d.is_dir():
        files = [p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in exts]
        for n in names:
            hit = [p for p in files if p.stem == n or p.stem.startswith(n + "_")]
            if hit:
                return sorted(hit, key=lambda p: (p.stem != n, str(p)))[0]
        if files:
            return max(files, key=lambda p: p.stat().st_size)
    # 3. Older Steam: librarycache/<appid>_library_600x900.jpg
    for n in names:
        for ext in exts:
            p = cache / ("%s_%s%s" % (appid, n, ext))
            if p.is_file():
                return p
    return None


# ------------------------------------------------------------ move targets
_DISK_CACHE: Dict[str, object] = {"t": 0.0, "disks": []}


def _disks() -> list:
    """lsblk result, cached for a few seconds (the window asks often)."""
    if time.time() - float(_DISK_CACHE["t"]) > 5:
        try:
            _DISK_CACHE["disks"] = core.list_disks()
        except Exception:
            _DISK_CACHE["disks"] = []
        _DISK_CACHE["t"] = time.time()
    return list(_DISK_CACHE["disks"])  # type: ignore[arg-type]


def _disk_names() -> Dict[str, str]:
    disks = _disks()
    names = core.display_names(disks)
    return {d.mountpoint: names[d.dev] for d in disks if d.mountpoint}


def drive_label(path: Path) -> str:
    p = os.path.realpath(str(path))
    best = ""
    names = _disk_names()
    for mp in names:
        if (p == mp or p.startswith(mp.rstrip("/") + "/")) and len(mp) > len(best):
            best = mp
    if best:
        return names[best]
    if _within(Path(p), core.home()):
        return "Home (system drive)"
    return fs_info(p)["mountpoint"]


def move_targets(game: Game) -> List[Dict[str, object]]:
    """Places this game can be moved to: other libraries of the same Steam,
    plus a new SteamLibrary folder on each mounted data disk."""
    out: List[Dict[str, object]] = []
    libs = libraries(game.library.root, game.library.flatpak)
    here = os.path.realpath(str(game.library.path))
    lib_paths = []
    for lib in libs:
        real = os.path.realpath(str(lib.path))
        lib_paths.append(real)
        if real == here:
            continue
        info = fs_info(real)
        ok, why = fs_ok_for_games(info["fstype"])
        if not lib.available:
            ok, why = False, "drive not mounted"
        out.append({"path": real, "label": "%s - %s" % (drive_label(lib.path), os.path.basename(real) or real),
                    "new": False, "ok": ok, "why": why, "free": _free(real), "fstype": info["fstype"]})
    disks = _disks()
    names = core.display_names(disks) if disks else {}
    for d in disks:
        if not d.mountpoint:
            continue
        if any(lp == d.mountpoint or lp.startswith(d.mountpoint.rstrip("/") + "/") for lp in lib_paths):
            continue
        target = os.path.join(d.mountpoint, "SteamLibrary")
        ok, why = fs_ok_for_games(d.fstype or fs_info(d.mountpoint)["fstype"])
        if ok and not os.access(d.mountpoint, os.W_OK):
            ok, why = False, "you cannot write to this disk"
        out.append({"path": target, "label": "%s - new SteamLibrary" % names.get(d.dev, d.dev),
                    "new": True, "ok": ok, "why": why, "free": _free(d.mountpoint),
                    "fstype": d.fstype})
    return out


def mount_warning(path: str) -> str:
    mp = fs_info(path)["mountpoint"]
    if mp.startswith(("/run/media/", "/media/")):
        return ("This disk is mounted at %s, which usually only happens when you open it. "
                "If it is not mounted when Steam starts, Steam shows the game as not installed. "
                "Set it to mount at startup (for example in the Disks app: Edit Mount Options, "
                "Mount at system startup)." % mp)
    return ""


# -------------------------------------------------------------- the journal
def _journal() -> Path:
    return core.config_dir() / "move-journal.json"


def _write_journal(d: dict) -> None:
    core.config_dir().mkdir(parents=True, exist_ok=True)
    tmp = _journal().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.replace(str(tmp), str(_journal()))


def _make_writable(path: str) -> None:
    for q in (os.path.dirname(path), path):
        try:
            if not os.path.islink(q):
                os.chmod(q, os.stat(q).st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        except OSError:
            pass


def _rm_error(func, path, _exc) -> None:
    """Games sometimes ship read-only folders; make them writable and retry."""
    _make_writable(path)
    func(path)


def _rm(path: Path) -> None:
    p = str(path)
    if os.path.islink(p) or os.path.isfile(p):
        try:
            os.unlink(p)
        except PermissionError:
            _make_writable(p)
            os.unlink(p)
    elif os.path.isdir(p):
        if sys.version_info >= (3, 12):
            shutil.rmtree(p, onexc=_rm_error)
        else:
            shutil.rmtree(p, onerror=_rm_error)


def recover_moves() -> List[Step]:
    """Finish or roll back a move that was interrupted (power cut, crash)."""
    jf = _journal()
    if not jf.exists():
        return []
    with job_lock() as mine:
        if not mine:
            return []   # another window is in the middle of that move right now
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
        j = json.loads(jf.read_text())
    except (OSError, ValueError):
        jf.unlink()
        return [Step("Interrupted move", False, "journal unreadable; removed it. Check the game in Steam.")]
    src, dst = Path(j["src"]), Path(j["dst"])
    man = "appmanifest_%s.acf" % j["appid"]
    steps = []
    if j["mode"] == "rename":
        if (dst / man).exists():
            _finish_listing(j, dst)
            _rm(src / (man + MOVING))
            steps.append(Step("Interrupted move of %s" % j["name"], True, "it had finished; tidied up"))
        else:
            for rel in j["items"]:
                if os.path.lexists(str(dst / rel)) and not os.path.lexists(str(src / rel)):
                    os.rename(str(dst / rel), str(src / rel))
            if (src / (man + MOVING)).exists() and not (src / man).exists():
                os.rename(str(src / (man + MOVING)), str(src / man))
            steps.append(Step("Interrupted move of %s" % j["name"], True, "rolled back to the old drive"))
    else:
        phase = j.get("phase")
        if phase == "copying" or (phase == "committing" and not (dst / man).exists()):
            if (src / (man + MOVING)).exists() and not (src / man).exists():
                os.rename(str(src / (man + MOVING)), str(src / man))
            for rel in j["items"]:
                _rm(dst / (rel + PARTIAL))
                if os.path.lexists(str(src / rel)):
                    _rm(dst / rel)   # only remove the new copy while the old one still exists
            steps.append(Step("Interrupted move of %s" % j["name"], True,
                              "rolled back; the game is still on the old drive"))
        else:  # committing with the new manifest in place, or cleanup
            for rel in j["items"]:
                if os.path.lexists(str(dst / rel)):
                    _rm(src / rel)
            _finish_listing(j, dst)
            _rm(src / (man + MOVING))
            steps.append(Step("Interrupted move of %s" % j["name"], True,
                              "finished; the game is on the new drive"))
    jf.unlink()
    return steps


def recover_safely() -> List[Step]:
    try:
        return recover_moves()
    except OSError as e:
        return [Step("Interrupted move", False, "could not finish tidying up yet (%s). Close anything using "
                     "those files; Deck Deck Goose tries again next time." % e)]


# ------------------------------------------------------------- copy & verify
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
    shutil.copystat(s, d, follow_symlinks=False)


def _copy_tree(s: str, d: str, job: Job, done: List[int]) -> None:
    if os.path.islink(s):
        os.symlink(os.readlink(s), d)
        return
    if os.path.isfile(s):
        _copy_file(s, d, job, done)
        return
    os.mkdir(d)
    with os.scandir(s) as it:
        entries = list(it)
    for e in entries:
        job.check()
        sp, dp = e.path, os.path.join(d, e.name)
        if e.is_symlink():
            os.symlink(os.readlink(sp), dp)
        elif e.is_dir(follow_symlinks=False):
            _copy_tree(sp, dp, job, done)
        elif e.is_file(follow_symlinks=False):
            _copy_file(sp, dp, job, done)
    shutil.copystat(s, d, follow_symlinks=False)


def _listing(root: str) -> Dict[str, Tuple[str, object]]:
    out: Dict[str, Tuple[str, object]] = {}
    if not os.path.isdir(root) or os.path.islink(root):
        st = os.lstat(root)
        return {".": ("l", os.readlink(root)) if os.path.islink(root) else ("f", st.st_size)}
    for dp, dns, fns in os.walk(root):
        rel = os.path.relpath(dp, root)
        for n in dns + fns:
            p = os.path.join(dp, n)
            r = os.path.normpath(os.path.join(rel, n))
            if os.path.islink(p):
                out[r] = ("l", os.readlink(p))
            elif os.path.isdir(p):
                out[r] = ("d", 0)
            elif os.path.isfile(p):
                out[r] = ("f", os.lstat(p).st_size)
        dns[:] = [n for n in dns if not os.path.islink(os.path.join(dp, n))]
    return out


def _verify(s: str, d: str) -> Tuple[bool, str]:
    a, b = _listing(s), _listing(d)
    if a == b:
        return True, "%d files checked" % len(a)
    missing = [k for k in a if a[k] != b.get(k)]
    return False, "%d files differ, e.g. %s" % (len(missing), (missing or list(b))[:3])


# ---------------------------------------------------------------- the move
def check_move(game: Game, target: str, allow_ntfs: bool = False) -> Tuple[bool, str, int, str]:
    """(ok, reason, bytes needed, mode) without changing anything."""
    if steam_running():
        return False, "Close Steam first (it rewrites these files while running).", 0, ""
    if game.state != 4:
        return False, ("Steam reports this game as: %s. Let Steam finish (or update it) first."
                       % (", ".join(decode_state(game.state)) or "state %d" % game.state)), 0, ""
    if not game.installdir or not game.dir.is_dir():
        return False, "The game folder %s is missing." % game.dir, 0, ""
    if game.extra.get("problem"):
        return False, "Not moved because %s." % game.extra["problem"], 0, ""
    try:
        read_libraryfolders(game.library.root, strict=True)
    except ValueError as e:
        return False, str(e), 0, ""
    sharing = shares_folder(game)
    if sharing:
        return False, ("%s shares its folder with %s. Moving one would break the other, so use Steam's own "
                       "mover (Properties > Installed Files) for these." % (game.name, ", ".join(sharing))), 0, ""
    dst_lib = Path(os.path.realpath(target))
    if dst_lib == Path(os.path.realpath(str(game.library.path))):
        return False, "The game is already in that library.", 0, ""
    if "steamapps" in dst_lib.parts:
        return False, "Pick the library folder itself (the one that contains steamapps).", 0, ""
    if not dst_lib.parent.is_dir():
        return False, ("%s does not exist. Is the drive plugged in and mounted?" % dst_lib.parent), 0, ""
    mnt = fs_info(dst_lib)
    if not os.environ.get("THISPC_ALLOW_ANY_FS"):
        if mnt["fstype"] in UNSAFE_FS:
            return False, ("%s is on %s (%s), not on a real data drive. Is the drive mounted?"
                           % (dst_lib, mnt["mountpoint"], mnt["fstype"])), 0, ""
        if "ro" in mnt["options"].split(","):
            return False, "That drive is mounted read-only.", 0, ""
    writable = str(dst_lib) if dst_lib.exists() else str(dst_lib.parent)
    if not os.access(writable, os.W_OK):
        return False, "You cannot write to %s." % writable, 0, ""
    if _within(dst_lib, game.library.steamapps):
        return False, "Pick a folder outside the current library.", 0, ""
    fstype = fs_info(dst_lib)["fstype"]
    ok, why = fs_ok_for_games(fstype)
    if not ok and not (allow_ntfs and fstype in NTFS_FS):
        return False, why, 0, ""
    dst = dst_lib / "steamapps"
    if (dst / game.manifest.name).exists():
        return False, "That library already has a copy of this game.", 0, ""
    for rel in game.items():
        if os.path.lexists(str(dst / rel)):
            return False, ("%s already exists in the target library. Remove or rename it first "
                           "(the junk scan lists leftovers)." % (dst / rel)), 0, ""
    probe = str(dst_lib)
    while not os.path.exists(probe):
        probe = os.path.dirname(probe)
    same = os.stat(str(game.library.steamapps)).st_dev == os.stat(probe).st_dev
    if same and not os.environ.get("THISPC_FORCE_COPY"):
        return True, "", 0, "rename"
    need = sum(tree_size(game.library.steamapps / rel)[0] for rel in game.items())
    free = _free(probe)
    if need + 2 ** 30 > free:
        return False, ("Not enough space: needs %s plus 1 GB spare, %s free."
                       % (core.human_size(need), core.human_size(free))), need, "copy"
    return True, "", need, "copy"


def move_game(game: Game, target: str, job: Job, allow_ntfs: bool = False) -> List[Step]:
    with job_lock() as mine:
        if not mine:
            return [Step("Move %s" % game.name, False, BUSY)]
        return _move_game(game, target, job, allow_ntfs)


def _move_game(game: Game, target: str, job: Job, allow_ntfs: bool = False) -> List[Step]:
    steps: List[Step] = list(recover_safely())
    if _journal().exists():
        return steps + [Step("Move %s" % game.name, False, "An earlier move still needs tidying up (see "
                             "above). Close anything using those files and try again.")]
    ok, why, need, mode = check_move(game, target, allow_ntfs)
    if not ok:
        return steps + [Step("Move %s" % game.name, False, why)]
    src = game.library.steamapps
    dst_lib = Path(os.path.realpath(target))
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
                os.rename(str(src / rel), str(dst / rel))
                moved.append(rel)
        except (Cancelled, OSError) as e:
            for rel in reversed(moved):
                os.rename(str(dst / rel), str(src / rel))
            _journal().unlink()
            if isinstance(e, OSError) and e.errno == errno.EXDEV:
                os.environ["THISPC_FORCE_COPY"] = "1"   # e.g. a different btrfs subvolume
                try:
                    return steps + move_game(game, target, job, allow_ntfs)
                finally:
                    os.environ.pop("THISPC_FORCE_COPY", None)
            if isinstance(e, Cancelled):
                raise
            return steps + [Step("Move %s" % game.name, False, "rename failed, undone: %s" % e)]
        steps.append(Step("Moved folders (same drive, instant)", True, ", ".join(items)))
    else:
        _write_journal(journal)
        done = [0]
        try:
            for rel in items:
                job.progress(message="Copying %s" % rel)
                part = str(dst / rel) + PARTIAL
                _rm(Path(part))
                _copy_tree(str(src / rel), part, job, done)
            job.progress(message="Checking the copy")
            for rel in items:
                good, detail = _verify(str(src / rel), str(dst / rel) + PARTIAL)
                if not good:
                    raise OSError("copy check failed for %s: %s" % (rel, detail))
            for rel in items:
                os.rename(str(dst / rel) + PARTIAL, str(dst / rel))
        except (Cancelled, OSError) as e:
            for rel in items:
                _rm(Path(str(dst / rel) + PARTIAL))
                if os.path.lexists(str(src / rel)):
                    _rm(dst / rel)
            _journal().unlink()
            if isinstance(e, Cancelled):
                raise
            return steps + [Step("Copy to %s" % dst_lib, False,
                                 "%s. The new copy was removed; the game is untouched." % e)]
        steps.append(Step("Copied and checked", True, "%s, every file compared" % core.human_size(done[0])))

    if steam_running():
        # Steam was started during the copy; it could overwrite our changes. Undo.
        if mode == "rename":
            for rel in reversed(items):
                os.rename(str(dst / rel), str(src / rel))
        else:
            for rel in items:
                if os.path.lexists(str(src / rel)):
                    _rm(dst / rel)
        _journal().unlink()
        return steps + [Step("Move %s" % game.name, False, "Steam was started while the game was being "
                             "moved, so the move was undone (the game is untouched). Close Steam and try again.")]
    # ---- commit: from here on the move finishes even if cancel is pressed
    job.progress(message="Telling Steam where the game is")
    journal["phase"] = "committing"
    _write_journal(journal)
    lib_step = add_library(root, dst_lib)
    steps.append(lib_step)
    if not lib_step.ok:
        recover_moves()
        return steps + [Step("Move %s" % game.name, False, "rolled back: " + lib_step.detail)]
    os.replace(str(src / man), str(src / (man + MOVING)))
    shutil.copy2(str(src / (man + MOVING)), str(dst / (man + ".tmp")))
    os.replace(str(dst / (man + ".tmp")), str(dst / man))
    steps.append(set_app_library(root, game.appid, str(game.size), game.library.path, dst_lib))
    journal["phase"] = "cleanup"
    _write_journal(journal)
    try:
        if mode == "copy":
            job.progress(message="Removing the old copy")
            missing = [rel for rel in items if not os.path.lexists(str(dst / rel))]
            try:
                ours = json.loads(_journal().read_text()) == journal
            except (OSError, ValueError):
                ours = False
            if missing or not ours:
                raise OSError("safety stop: the new copy is incomplete (%s) or the move record changed, so "
                              "the old copy was kept" % (", ".join(missing) or "record"))
            for rel in items:
                _rm(src / rel)
        _rm(src / (man + MOVING))
        _journal().unlink()
        steps.append(Step("Old copy removed" if mode == "copy" else "Manifest moved", True,
                          "the game now lives in %s" % dst_lib))
    except OSError as e:
        steps.append(Step("Old copy", False, "The game was moved and works from %s, but part of the old copy "
                          "could not be deleted yet (%s). Deck Deck Goose finishes the clean-up next time."
                          % (dst_lib, e)))
    if mode == "copy" and (game.appid in compressed_record() or game.appid in estimates_record()):
        core.update_config(drop_from=("compressed", game.appid))
        core.update_config(drop_from=("estimates", game.appid))
        steps.append(Step("Compression", True, "Copied files may not be compressed on the new drive. Press "
                          "Estimate there if you want to compress the game again."))
    if game.library.flatpak and not _within(dst_lib, core.home()):
        rc, out = core.run(["flatpak", "override", "--user", "--filesystem=%s" % dst_lib, FLATPAK_STEAM])
        steps.append(Step("Flatpak Steam can see the new drive", rc == 0,
                          ("granted access to %s" % dst_lib) if rc == 0 else out))
    warn = mount_warning(str(dst_lib))
    if warn:
        steps.append(Step("Note about this disk", True, warn))
    steps.append(Step("Next", True, "Start Steam. The game should show as installed. If it asks to "
                      "install, choose the new library: Steam finds the files and only checks them."))
    return steps


# -------------------------------------------------------------------- junk
def _all_steamapps() -> List[Tuple[Path, Path]]:
    out = []
    for root, flat in steam_roots():
        for lib in libraries(root, flat):
            if lib.available:
                out.append((root, lib.steamapps))
    return out


def scan_junk() -> List[Dict[str, object]]:
    games = list_games()
    installed = {g.appid for g in games}
    dirs_in_use = {os.path.realpath(str(g.dir)) for g in games}
    downloading = {g.appid for g in games if g.state != 4}
    out: List[Dict[str, object]] = []

    def add(cat: str, path: Path, label: str, default: bool, warn: str = "") -> None:
        size, _ = tree_size(path)
        out.append({"id": "%s:%s" % (cat, path), "category": cat, "path": str(path),
                    "label": label, "size": size, "human": human(size),
                    "default": default, "warning": warn, "steam_ok": cat in STEAM_SAFE_JUNK})

    names = {g.appid: g.name for g in games}
    roots_done = set()
    for root, sa in _all_steamapps():
        for p in sorted((sa / "shadercache").glob("*")):
            if not p.name.isdigit():
                continue
            if p.name in installed:
                add("shader", p, "Shader cache: %s" % names[p.name], False,
                    "Rebuilt on next launch; first minutes may stutter.")
            else:
                add("shader-orphan", p, "Shader cache of uninstalled game %s" % p.name, True)
        for sub in ("downloading", "temp"):
            for p in sorted((sa / sub).glob("*")):
                if p.name in downloading:
                    continue
                add("download", p, "Leftover from an old download/update (%s)" % p.name, True)
        for pat in ("common/*", "compatdata/*", "shadercache/*", "workshop/*", "workshop/content/*"):
            for p in sorted(sa.glob(pat + PARTIAL)):
                add("partial", p, "Unfinished copy from Deck Deck Goose", True)
        for p in sorted((sa / "common").glob("*")):
            if p.is_dir() and not p.name.endswith(PARTIAL) and os.path.realpath(str(p)) not in dirs_in_use:
                add("orphan-folder", p, "Game folder with no installed game: %s" % p.name, False,
                    "Left behind after uninstalling. May hold mods or saves.")
        for p in sorted((sa / "compatdata").glob("*")):
            if p.name.isdigit() and int(p.name) < SHORTCUT_MIN_ID and p.name not in installed:
                add("orphan-prefix", p, "Proton prefix of uninstalled game %s" % p.name, False,
                    "MAY HOLD SAVE GAMES. Only remove if the game used Steam Cloud or you are sure.")
        if root not in roots_done:
            roots_done.add(root)
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


STEAM_SAFE_JUNK = {"shader-orphan", "partial", "orphan-folder", "orphan-prefix"}


def _clean_junk(ids: List[str], job: Job) -> List[Step]:
    running = steam_running()
    found = {d["id"]: d for d in scan_junk()}
    if running:
        held = [i for i in ids if i in found and found[i]["category"] not in STEAM_SAFE_JUNK]
        if held and len(held) == len(ids):
            return [Step("Clean junk", False, "Close Steam first (or pick only items marked safe while "
                         "Steam runs).")]
        ids = [i for i in ids if i not in held]
    allowed = [sa for _, sa in _all_steamapps()] + [r for r, _ in steam_roots()]
    steps: List[Step] = []
    picked = [found[i] for i in ids if i in found]
    total = sum(int(d["size"]) for d in picked)
    freed = 0
    job.progress(0, total, "Cleaning")
    for d in picked:
        if job.cancel_event.is_set():
            steps.append(Step("Stopped", False, "Cancelled after removing %d of %d items." % (
                sum(1 for x in steps if x.ok), len(picked))))
            break
        p = Path(str(d["path"]))
        if p.is_symlink() and any(_within(p.parent, a) for a in allowed):
            p.unlink()   # only the link; never follow it out of Steam's folders
            steps.append(Step(str(d["label"]), True, "removed a shortcut (its target was left alone)"))
            continue
        if not any(_within(p, a) for a in allowed) or p in [Path(str(a)) for a in allowed]:
            steps.append(Step(str(d["label"]), False, "refused: outside Steam's folders"))
            continue
        try:
            if p.is_dir() and not p.is_symlink() and d["category"] == "steam-cache":
                for child in p.iterdir():
                    _rm(child)
            else:
                _rm(p)
            freed += int(d["size"])
            steps.append(Step(str(d["label"]), True, "removed %s" % d["human"]))
        except OSError as e:
            steps.append(Step(str(d["label"]), False, str(e)))
        job.progress(freed)
    if not picked:
        steps.append(Step("Clean junk", True, "nothing selected"))
    else:
        steps.append(Step("Total", True, "freed about %s" % core.human_size(freed)))
    return steps


# -------------------------------------------------------------- compression
def compression_info(path: Path) -> Dict[str, object]:
    info = fs_info(path)
    m = re.search(r"compress(?:-force)?=([\w:]+)", info["options"])
    if info["fstype"] != "btrfs":
        return {"available": False, "fstype": info["fstype"], "mount_compress": "",
                "why": "Compression needs a btrfs drive (this one is %s)." % info["fstype"]}
    return {"available": True, "fstype": "btrfs", "mount_compress": m.group(1) if m else "",
            "why": ""}


def compressed_record() -> Dict[str, dict]:
    return core.load_config().get("compressed", {})


def estimates_record() -> Dict[str, dict]:
    return core.load_config().get("estimates", {})


def _save_record(key: str, appid: str, value: dict) -> None:
    core.update_config(merge_key=key, merge={appid: value})


def estimate_game(game: Game, job: Job) -> List[Step]:
    """Read-only: how much would compressing this game save?"""
    from . import compress
    name = "Estimate %s" % game.name
    ci = compression_info(game.dir)
    if not ci["available"]:
        return [Step(name, False, str(ci["why"]))]
    if not game.items() or not game.dir.is_dir():
        return [Step(name, False, "game folder missing")]
    job.progress(0, 1, "Sampling files")
    est = compress.estimate(str(game.dir), progress=lambda d, t: job.progress(d, t), check=job.check)
    rec = {k: est[k] for k in ("total", "saved", "pct", "verdict", "already", "skipped_formats",
                               "worth_bytes", "algo", "encoding_known")}
    rec["date"] = time.strftime("%Y-%m-%d")
    _save_record("estimates", game.appid, rec)
    detail = ("about %s (%.1f%%) - %s. Already compressed by the drive: %s. Skipped as video/audio/"
              "images/archives: %s." % (core.human_size(int(est["saved"])), est["pct"], est["verdict"],
                                         core.human_size(int(est["already"])),
                                         core.human_size(int(est["skipped_formats"]))))
    if not est["encoding_known"]:
        detail += " (Could not check what the drive already compresses; the real saving may be smaller.)"
    return [Step(name, True, detail)]


def compress_game(game: Game, job: Job) -> List[Step]:
    with job_lock() as mine:
        if not mine:
            return [Step("Compress %s" % game.name, False, BUSY)]
        return _compress_game(game, job)


def _compress_game(game: Game, job: Job) -> List[Step]:
    from . import compress
    name = "Compress %s" % game.name
    if game.state != 4 or game.extra.get("problem") or not game.items():
        return [Step(name, False, "Only fully installed games can be compressed (%s)."
                     % (game.extra.get("problem") or ", ".join(decode_state(game.state)) or "missing"))]
    if game_in_use(game):
        return [Step(name, False, "The game is running (or something has its files open). Quit it first.")]
    ci = compression_info(game.dir)
    if not ci["available"]:
        return [Step(name, False, str(ci["why"]))]
    job.progress(0, 1, "Working out which files are worth compressing")
    est = compress.estimate(str(game.dir), check=job.check)
    worth = set(est["worth_files"])
    files = [(p, s) for p, s in compress.walk_files(str(game.dir)) if p in worth]
    total = sum(s for _, s in files)
    job.progress(0, total, "Compressing %d of %d files" % (len(files), est["files"]))
    free_before = _free(game.dir)
    done, batch, bsize, errors = 0, [], 0, []

    def flush() -> None:
        nonlocal batch, bsize, done
        if not batch:
            return
        rc, out = core.run(["btrfs", "filesystem", "defragment", "-czstd", "--"] + batch, timeout=7200)
        if rc != 0:
            errors.append(out.strip().splitlines()[-1] if out.strip() else "exit %d" % rc)
        done += bsize
        batch, bsize = [], 0
        job.progress(done)

    cancelled = False
    for p, s in files:
        if job.cancel_event.is_set():
            cancelled = True
            break
        batch.append(p)
        bsize += s
        if len(batch) >= 200 or bsize >= 2 * 1024 ** 3:
            flush()
            if errors and "not permitted" in errors[-1].lower():
                break
    flush()
    rc, out = core.run(["btrfs", "property", "set", str(game.dir), "compression", "zstd"])
    saved = max(0, _free(game.dir) - free_before)
    steps = []
    if cancelled:
        steps.append(Step(name, False, "Cancelled part-way: some files are compressed (about %s freed). "
                          "That is harmless; the game works as before." % core.human_size(saved)))
    elif errors:
        steps.append(Step(name, False, "%d batch(es) failed: %s" % (len(errors), errors[0])))
    elif not files:
        steps.append(Step(name, True, "nothing worth compressing: the files are already compressed "
                          "by their format or by the drive"))
    else:
        steps.append(Step(name, True, "compressed %d files (%s) with zstd; skipped %d that would not "
                          "shrink; about %s freed (drive free-space change; the estimate was %s)"
                          % (len(files), core.human_size(total), est["files"] - len(files),
                             core.human_size(saved), core.human_size(int(est["saved"])))))
    steps.append(Step("Future updates compressed too", rc == 0,
                      "set btrfs compression=zstd on the game folder (btrfs skips data that does "
                      "not shrink)" if rc == 0 else out))
    if not errors and (files or not cancelled):
        _save_record("compressed", game.appid, {"date": time.strftime("%Y-%m-%d"), "saved": saved,
                                                 "estimate": est["saved"]})
    return steps


# ------------------------------------------------------------------ for UI
def games_status() -> Dict[str, object]:
    recovered = [s.as_dict() for s in recover_safely()]
    roots = steam_roots()
    games = list_games()
    comp = compressed_record()
    ests = estimates_record()
    libs = []
    for root, flat in roots:
        for lib in libraries(root, flat):
            ci = compression_info(lib.path) if lib.available else {"available": False, "mount_compress": ""}
            libs.append({"path": str(lib.path), "drive": drive_label(lib.path),
                         "available": lib.available, "flatpak": flat,
                         "free": core.human_size(_free(lib.path)) if lib.available else "-",
                         "fstype": fs_info(lib.path)["fstype"] if lib.available else "-",
                         "compress": ci.get("mount_compress", ""),
                         "can_compress": bool(ci.get("available"))})
    lib_compress = {l["path"]: l["can_compress"] for l in libs}
    return {
        "roots": [{"path": str(r), "flatpak": f} for r, f in roots],
        "running": steam_running(),
        "recovered": recovered,
        "libraries": libs,
        "games": [{"appid": g.appid, "name": g.name, "size": core.human_size(g.size) if g.size else "?",
                   "bytes": g.size, "tool": g.tool, "state": g.state,
                   "state_text": g.extra.get("problem") or (", ".join(decode_state(g.state)) if g.state != 4 else ""),
                   "library": str(g.library.path), "drive": drive_label(g.library.path),
                   "art": bool(art_path(g.library.root, g.appid)),
                   "compressed": g.appid in comp,
                   "estimate": ests.get(g.appid),
                   "can_compress": lib_compress.get(str(g.library.path), False),
                   "targets": move_targets(g)} for g in games],
    }
