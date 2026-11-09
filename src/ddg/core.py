# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Core logic: find disks, build the logical 'This PC' folder, bookmarks,
Flatpak access, and the background refresh timer.

Nothing here touches the immutable OS. It only:
  * creates/removes symlinks inside one folder of yours (default ~/This PC)
  * edits two bookmark files (GTK and KDE) with a backup
  * runs `flatpak override --user` for file managers you choose
  * installs one systemd *user* timer
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import __version__

DEFAULT_ROOT_NAME = "This PC"
MIGRATION: List[Step] = []        # what migrate_old() did at start-up (shown once in the window)
MARK = ".thispc-managed"
UNIT = "ddg-refresh"
KDE_ID = "thispc-linux-1"
# Only volumes mounted under these prefixes are shown as disks. Everything
# else (/, /boot, /var, /sysroot ...) is the operating system and is hidden.
DATA_PREFIXES = ("/run/media/", "/media/", "/mnt/", "/var/mnt/")
DATA_FS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "ntfs", "ntfs3", "exfat",
           "vfat", "f2fs", "hfsplus", "udf", "iso9660"}
XDG_NAMES = {"DESKTOP": "Desktop", "DOCUMENTS": "Documents",
             "DOWNLOAD": "Downloads", "MUSIC": "Music",
             "PICTURES": "Pictures", "VIDEOS": "Videos"}
# Detection candidates only; any installed Flatpak whose .desktop file says
# Categories=...FileManager is found too.
KNOWN_FILE_MANAGERS = ("org.kde.dolphin", "io.github.doublecmd.DoubleCommander",
                       "org.gnome.Nautilus")


# ----------------------------------------------------------------- helpers
@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def home() -> Path:
    return Path(os.environ.get("HOME") or str(Path.home()))


def config_dir() -> Path:
    return home() / ".config" / "deck-deck-goose"


def data_dir() -> Path:
    return home() / ".local" / "share" / "deck-deck-goose"


def systemd_dir() -> Path:
    return home() / ".config" / "systemd" / "user"


def run(cmd: List[str], timeout: int = 20) -> Tuple[int, str]:
    """Run a command. Returns (exit code, stdout on success else stdout+stderr)."""
    if os.environ.get("THISPC_FAKE_TOOLS"):
        return _fake_run(cmd)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "%s: not found" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "%s: timed out" % cmd[0]
    if p.returncode == 0:
        return 0, p.stdout.strip()
    return p.returncode, (p.stdout + p.stderr).strip()


def _fake_run(cmd: List[str]) -> Tuple[int, str]:
    """Stand-in for systemctl/flatpak/udisksctl. Used by the tests and for
    screenshots on a machine that has none of them. Never used otherwise."""
    state_file = home() / ".thispc-fake-state.json"
    try:
        st = json.loads(state_file.read_text())
    except (OSError, ValueError):
        st = {}
    st.setdefault("timer", False)
    st.setdefault("overrides", {})
    st.setdefault("steam_running", bool(os.environ.get("THISPC_FAKE_STEAM_RUNNING")))
    st.setdefault("btrfs_calls", [])

    def save() -> None:
        state_file.write_text(json.dumps(st))

    if cmd[0] == "steam" or cmd[:3] == ["flatpak", "run", "com.valvesoftware.Steam"]:
        if "-shutdown" in cmd:
            st["steam_running"] = False
            save()
        elif "-start" in cmd:
            st["steam_running"] = True
            save()
        return 0, ""
    if cmd[0] == "btrfs":
        st["btrfs_calls"].append(cmd[1:])
        save()
        if os.environ.get("THISPC_FAKE_BTRFS_EPERM"):
            return 1, "ERROR: defrag failed on %s: Operation not permitted" % cmd[-1]
        return 0, ""

    if cmd[0] == "systemctl" and len(cmd) > 3 and not cmd[-1].startswith(UNIT + "."):
        units = st.setdefault("units", {})          # other user units (the sound service)
        if cmd[2] in ("enable", "disable"):
            units[cmd[-1]] = cmd[2] == "enable"
            save()
            return 0, ""
        if cmd[2] == "is-enabled":
            return (0, "enabled") if units.get(cmd[-1]) else (1, "disabled")
        return 0, ""
    if cmd[0] == "systemctl":
        verb = cmd[2] if len(cmd) > 2 else ""
        if verb == "enable":
            st["timer"] = True
            save()
            return 0, ""
        if verb == "disable":
            st["timer"] = False
            save()
            return 0, ""
        if verb == "is-active":
            return (0, "active") if st["timer"] else (3, "inactive")
        return 0, ""
    if cmd[0] == "flatpak":
        if cmd[1] == "list":
            return 0, "org.kde.dolphin\norg.mozilla.firefox\nio.github.doublecmd.DoubleCommander"
        if cmd[1] == "override":
            app = cmd[-1]
            if "--reset" in cmd:
                st["overrides"].pop(app, None)
                save()
                return 0, ""
            if "--show" in cmd:
                fs = st["overrides"].get(app)
                return (0, "[Context]\nfilesystems=%s;" % ";".join(fs)) if fs else (0, "")
            have = st["overrides"].get(app, [])
            st["overrides"][app] = have + [a.split("=", 1)[1] for a in cmd
                                           if a.startswith("--filesystem=")
                                           and a.split("=", 1)[1] not in have]
            save()
            return 0, ""
    if cmd[0] == "udisksctl":
        return 0, "Mounted %s at /run/media/demo/Mounted." % cmd[-1]
    return 127, "%s: not found" % cmd[0]


# ------------------------------------------------------------------- disks
@dataclass
class Disk:
    dev: str
    label: str
    fstype: str
    size: int
    mountpoint: Optional[str]
    removable: bool

    @property
    def mounted(self) -> bool:
        return bool(self.mountpoint)


def human_size(n: int) -> str:
    if n >= 10 ** 12:
        return "%.1f TB" % (n / 1e12)
    if n >= 10 ** 9:
        return "%d GB" % round(n / 1e9)
    return "%d MB" % round(n / 1e6)


def _int(v: object) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _bool(v: object) -> bool:
    return v in (True, 1, "1", "true")


def read_lsblk() -> dict:
    override = os.environ.get("THISPC_LSBLK_JSON")
    if override:
        return json.loads(Path(override).read_text())
    cols = "NAME,PATH,LABEL,FSTYPE,SIZE,RM,HOTPLUG,TYPE,"
    rc, out = run(["lsblk", "-J", "-b", "-o", cols + "MOUNTPOINTS"])
    if rc != 0:  # older util-linux has MOUNTPOINT only
        rc, out = run(["lsblk", "-J", "-b", "-o", cols + "MOUNTPOINT"])
    if rc != 0:
        raise RuntimeError("lsblk failed: " + out)
    return json.loads(out)


def parse_disks(data: dict) -> List[Disk]:
    found: List[Disk] = []
    seen = set()

    def walk(nodes: list) -> None:
        for n in nodes:
            walk(n.get("children") or [])
            if n.get("type") == "loop":
                continue
            fs = n.get("fstype") or ""
            mps = [m for m in (n.get("mountpoints") or [n.get("mountpoint")]) if m]
            size = _int(n.get("size"))
            rem = _bool(n.get("rm")) or _bool(n.get("hotplug"))
            dev = n.get("path") or "/dev/" + str(n.get("name"))
            label = (n.get("label") or "").strip()
            data_mps = [m for m in mps if m.startswith(DATA_PREFIXES)]
            if data_mps:
                if data_mps[0] not in seen:
                    seen.add(data_mps[0])
                    found.append(Disk(dev, label, fs, size, data_mps[0], rem))
            elif not mps and fs in DATA_FS and (size >= 2 ** 30 or rem):
                found.append(Disk(dev, label, fs, size, None, rem))

    walk(data.get("blockdevices") or [])
    found.sort(key=lambda d: (not d.mounted, d.label.lower(), d.dev))
    return found


def list_disks() -> List[Disk]:
    return parse_disks(read_lsblk())


def _clean(name: str) -> str:
    return re.sub(r"[\x00-\x1f/]", "-", name).strip(" .") or "Disk"


def display_names(disks: List[Disk]) -> Dict[str, str]:
    """Friendly, stable folder names keyed by device path."""
    names: Dict[str, str] = {}
    used: Dict[str, int] = {}
    for d in sorted(disks, key=lambda x: (x.label.lower(), x.dev)):
        base = _clean(d.label) if d.label else "Unlabelled %s (%s)" % (
            human_size(d.size), d.dev.rsplit("/", 1)[-1])
        n = used.get(base, 0) + 1
        used[base] = n
        names[d.dev] = base if n == 1 else "%s (%d)" % (base, n)
    return names


def mount_disk(dev: str) -> Step:
    known = {d.dev for d in list_disks() if not d.mounted}
    if dev not in known:
        return Step("Mount " + dev, False, "not an unmounted data disk")
    rc, out = run(["udisksctl", "mount", "-b", dev], timeout=60)
    return Step("Mount " + dev, rc == 0, out)


# ------------------------------------------------------ users and folders
def list_users() -> List[Tuple[str, str]]:
    me = home()
    out = [(me.name, str(me))]
    parent = me.parent
    if str(parent) != "/":
        try:
            for p in sorted(parent.iterdir()):
                if (p.is_dir() and not p.name.startswith(".") and p != me
                        and p.name != "lost+found"
                        and os.access(str(p), os.R_OK | os.X_OK)):
                    out.append((p.name, str(p)))
        except OSError:
            pass
    return out


def list_folders() -> List[Tuple[str, str]]:
    cfg = home() / ".config" / "user-dirs.dirs"
    found: Dict[str, str] = {}
    try:
        for line in cfg.read_text().splitlines():
            m = re.match(r'XDG_(\w+)_DIR="?([^"]*)"?', line.strip())
            if m and m.group(1) in XDG_NAMES:
                found[m.group(1)] = m.group(2).replace("$HOME", str(home()))
    except OSError:
        pass
    out = []
    for key, nice in XDG_NAMES.items():
        target = Path(found.get(key, str(home() / nice)))
        if target != home() and target.is_dir():
            out.append((nice, str(target)))
    return out


# -------------------------------------------------------------- the folder
def view_root(root_name: str = DEFAULT_ROOT_NAME) -> Path:
    return home() / root_name


def desired_links(root: Path, disks: List[Disk]) -> Dict[Path, str]:
    want: Dict[Path, str] = {}
    names = display_names(disks)
    for d in disks:
        if d.mounted and d.mountpoint:
            want[root / "Disks" / names[d.dev]] = d.mountpoint
    for name, path in list_users():
        want[root / "Users" / _clean(name)] = path
    for name, path in list_folders():
        want[root / name] = path
    return want


def sync_view(root: Path, want: Dict[Path, str]) -> Dict[str, int]:
    """Make `root` match `want`. Only ever creates or removes symlinks."""
    stats = {"created": 0, "updated": 0, "removed": 0, "skipped": 0, "broken": 0}
    if root.exists() and not (root / MARK).exists() and any(root.iterdir()):
        raise RuntimeError("%s already exists and is not managed by This PC for "
                           "Linux. Pick another folder name." % root)
    for sub in (root, root / "Disks", root / "Users"):
        sub.mkdir(parents=True, exist_ok=True)
    (root / MARK).write_text("Managed by Deck Deck Goose %s. Safe to delete "
                             "the symlinks; real files are never touched.\n" % __version__)
    for link, target in want.items():
        if link.is_symlink():
            if os.readlink(str(link)) == target:
                pass
            else:
                link.unlink()
                os.symlink(target, str(link))
                stats["updated"] += 1
        elif link.exists():
            stats["skipped"] += 1
            continue
        else:
            os.symlink(target, str(link))
            stats["created"] += 1
        if not os.path.exists(target):
            stats["broken"] += 1
    for folder in (root, root / "Disks", root / "Users"):
        for entry in folder.iterdir():
            if entry.is_symlink() and entry not in want:
                entry.unlink()
                stats["removed"] += 1
    return stats


def remove_view(root: Path) -> Step:
    if not root.exists():
        return Step("Remove " + str(root), True, "nothing to remove")
    if not (root / MARK).exists():
        return Step("Remove " + str(root), False, "not managed by Deck Deck Goose; left alone")
    for folder in (root, root / "Disks", root / "Users"):
        if folder.is_dir():
            for entry in folder.iterdir():
                if entry.is_symlink():
                    entry.unlink()
    (root / MARK).unlink()
    for folder in (root / "Disks", root / "Users", root):
        try:
            folder.rmdir()
        except OSError:
            pass  # has real files in it: keep them
    return Step("Remove " + str(root), True,
                "symlinks removed" + ("; folder kept (it holds your own files)" if root.exists() else ""))


# --------------------------------------------------------------- bookmarks
def _uri(path: Path) -> str:
    return "file://" + urllib.parse.quote(str(path))


def gtk_bookmarks_path() -> Path:
    return home() / ".config" / "gtk-3.0" / "bookmarks"


def kde_places_path() -> Path:
    return home() / ".local" / "share" / "user-places.xbel"


def gtk_bookmark(root: Path, add: bool) -> Step:
    f = gtk_bookmarks_path()
    uri = _uri(root)
    lines = f.read_text().splitlines() if f.exists() else []
    lines = [l for l in lines if l.split(" ", 1)[0] != uri]
    if add:
        lines.append("%s %s" % (uri, root.name))
    if add or f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(lines) + ("\n" if lines else ""))
    ok = (uri in f.read_text()) if (add and f.exists()) else (not add)
    return Step("Sidebar bookmark (Nemo, Thunar, Nautilus, GTK)", ok,
                ("added " if add else "removed ") + root.name)


def kde_bookmark(root: Path, add: bool) -> Step:
    name = "Sidebar entry (Dolphin Places)"
    f = kde_places_path()
    if not f.exists():
        return Step(name, True, "Dolphin has not been run yet; skipped")
    text = f.read_text()
    if "xmlns:bookmark" not in text or "</xbel>" not in text:
        return Step(name, False, "unexpected file format; left untouched")
    bak = f.with_name(f.name + ".thispc-backup")
    if not bak.exists():
        shutil.copy2(str(f), str(bak))
    text = re.sub(r'\n?[ \t]*<bookmark href="[^"]*">(?:(?!</bookmark>).)*?%s(?:(?!</bookmark>).)*</bookmark>'
                  % KDE_ID, "", text, flags=re.S)
    if add:
        entry = ('\n <bookmark href="%s">\n  <title>%s</title>\n  <info>\n'
                 '   <metadata owner="http://freedesktop.org">\n'
                 '    <bookmark:icon name="computer"/>\n   </metadata>\n'
                 '   <metadata owner="http://www.kde.org">\n    <ID>%s</ID>\n'
                 '    <isSystemItem>false</isSystemItem>\n   </metadata>\n'
                 '  </info>\n </bookmark>' % (_uri(root), root.name, KDE_ID))
        text = text.replace("</xbel>", entry + "\n</xbel>")
    f.write_text(text)
    ok = (KDE_ID in f.read_text()) == add
    return Step(name, ok, ("added" if add else "removed") +
                " (close Dolphin first if it does not show up; backup kept)")


# ----------------------------------------------------------------- flatpak
def find_file_managers() -> List[str]:
    ids = set()
    for d in (home() / ".local/share/flatpak/exports/share/applications",
              Path("/var/lib/flatpak/exports/share/applications")):
        try:
            for f in d.glob("*.desktop"):
                if re.search(r"^Categories=.*FileManager", f.read_text(errors="ignore"), re.M):
                    ids.add(f.stem)
        except OSError:
            pass
    rc, out = run(["flatpak", "list", "--app", "--columns=application"])
    if rc == 0:
        ids |= set(out.split()) & set(KNOWN_FILE_MANAGERS)
    return sorted(ids)


def _extra_paths() -> List[str]:
    return [p for p in ("/var/mnt",) if Path(p).is_dir()]


def override_state(app: str) -> bool:
    rc, out = run(["flatpak", "override", "--user", "--show", app])
    m = re.search(r"^filesystems=(.*)$", out, re.M)
    return rc == 0 and bool(m) and "host" in m.group(1).split(";")


def apply_override(app: str) -> Step:
    cmd = ["flatpak", "override", "--user", "--filesystem=host",
           "--filesystem=home", "--filesystem=/run/media"]
    cmd += ["--filesystem=" + p for p in _extra_paths()] + [app]
    rc, out = run(cmd)
    if rc != 0:
        return Step("Flatpak access: " + app, False, out)
    ok = override_state(app)
    return Step("Flatpak access: " + app, ok,
                "disks and home are now visible to it (restart the app)" if ok
                else "set, but could not be read back")


def reset_override(app: str) -> Step:
    rc, out = run(["flatpak", "override", "--user", "--reset", app])
    return Step("Flatpak access: " + app, rc == 0,
                "your overrides for this app were reset" if rc == 0 else out)


# ---------------------------------------------------------- systemd timer
SERVICE_TEXT = """[Unit]
Description=Deck Deck Goose: refresh the logical This PC folder

[Service]
Type=oneshot
Environment=PYTHONPATH=%h/.local/share/deck-deck-goose/lib
ExecStart=/usr/bin/env python3 -m ddg refresh --quiet
"""
TIMER_TEXT = """[Unit]
Description=Refresh the This PC folder every 30 seconds

[Timer]
OnBootSec=15s
OnUnitActiveSec=30s
AccuracySec=5s

[Install]
WantedBy=timers.target
"""


def copy_program() -> Step:
    """Keep a copy of this program in ~/.local/share/deck-deck-goose/lib, used by
    the refresh timer and the Game Mode tile (so they work without this folder)."""
    src = Path(__file__).resolve().parent
    dest = data_dir() / "lib" / "ddg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src != dest.resolve():
        shutil.copytree(str(src), str(dest), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    comp = src.parent.parent / "components"           # bundled projects (Zombie-Deck) travel with it
    if comp.is_dir() and comp.resolve() != (data_dir() / "components").resolve():
        shutil.copytree(str(comp), str(data_dir() / "components"), dirs_exist_ok=True, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
    ok = f"{__version__}" in (dest / "__init__.py").read_text()
    return Step("Program copy in ~/.local/share/deck-deck-goose", ok,
                "version %s read back" % __version__ if ok else "copy did not verify")


def gamemode_installed() -> bool:
    return (data_dir() / "gamemode-installed").exists()


# ------------------------------------------------ carry over an old "This PC for Linux" install
OLD_DIR = "this-pc-linux"
OLD_UNITS = ("thispc-refresh.timer", "thispc-refresh.service", "thispc-sound.service")
SHIM_MAIN = (
    "# Old name: keeps a launcher or timer made by This PC for Linux (<= 0.4) working until it is re-added.\n"
    "import sys\n"
    "from ddg.__main__ import main\n"
    "sys.exit(main())\n")


def migrate_old() -> List[Step]:
    """Carry an installation made under the old name over to the new folders.

    Moves ~/.config/this-pc-linux and ~/.local/share/this-pc-linux (settings,
    game records, a move journal that may still be needed, the program copy)
    with one rename each, leaves a symlink where the old data folder was and a
    tiny 'thispc' shim in the program copy, so an old Game Mode tile keeps
    working until it is added again, and replaces the old systemd units. Does
    nothing when there is nothing old, and never touches a folder that
    already exists under the new name."""
    steps: List[Step] = []
    old_cfg = home() / ".config" / OLD_DIR
    old_data = home() / ".local" / "share" / OLD_DIR
    moved_data = False
    for old, new, label in ((old_cfg, config_dir(), "settings"), (old_data, data_dir(), "program data")):
        if old.is_symlink() or not old.exists():
            continue
        if new.exists():
            steps.append(Step("Old %s folder" % label, True, "%s left as it is (%s already exists)" % (old, new)))
            continue
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            os.rename(str(old), str(new))
        except OSError as e:
            steps.append(Step("Move your %s" % label, False, "%s (left where it was)" % e))
            continue
        steps.append(Step("Moved your %s" % label, True, "%s -> %s" % (old, new)))
        if label == "program data":
            moved_data = True
            try:
                os.symlink(str(new), str(old))
            except OSError:
                pass
    sd = systemd_dir()
    had_timer = (sd / "thispc-refresh.timer").exists()
    had_sound = (sd / "thispc-sound.service").exists()
    if had_timer or had_sound or (sd / "thispc-refresh.service").exists():
        run(["systemctl", "--user", "disable", "--now", "thispc-refresh.timer"])
        run(["systemctl", "--user", "disable", "thispc-sound.service"])
        for n in OLD_UNITS:
            try:
                (sd / n).unlink()
            except OSError:
                pass
        run(["systemctl", "--user", "daemon-reload"])
        if had_timer:
            steps += install_timer()
        if had_sound:
            from . import sound
            steps += sound.keep(True)
    if moved_data or (data_dir() / "lib" / "thispc").exists():
        if (data_dir() / "lib").exists():
            steps.append(copy_program())
        shim = data_dir() / "lib" / "thispc"
        shutil.rmtree(str(shim), ignore_errors=True)
        shim.mkdir(parents=True, exist_ok=True)
        (shim / "__init__.py").write_text("")
        (shim / "__main__.py").write_text(SHIM_MAIN)
    return steps


def install_timer() -> List[Step]:
    steps = [copy_program()]
    systemd_dir().mkdir(parents=True, exist_ok=True)
    (systemd_dir() / (UNIT + ".service")).write_text(SERVICE_TEXT)
    (systemd_dir() / (UNIT + ".timer")).write_text(TIMER_TEXT)
    run(["systemctl", "--user", "daemon-reload"])
    rc, out = run(["systemctl", "--user", "enable", "--now", UNIT + ".timer"])
    rc2, out2 = run(["systemctl", "--user", "is-active", UNIT + ".timer"])
    steps.append(Step("Auto-refresh timer (every 30 s)", rc == 0 and rc2 == 0,
                      "active" if rc2 == 0 else (out or out2 or "not active")))
    return steps


def remove_timer() -> List[Step]:
    run(["systemctl", "--user", "disable", "--now", UNIT + ".timer"])
    for ext in (".service", ".timer"):
        try:
            (systemd_dir() / (UNIT + ext)).unlink()
        except OSError:
            pass
    run(["systemctl", "--user", "daemon-reload"])
    if not gamemode_installed() and not (systemd_dir() / "ddg-sound.service").exists():
        # the Game Mode tile and the sound service still need the program copy
        shutil.rmtree(str(data_dir() / "lib"), ignore_errors=True)
    return [Step("Auto-refresh timer", True, "removed")]


# ------------------------------------------------------------------ config
def load_config() -> dict:
    try:
        return json.loads((config_dir() / "config.json").read_text())
    except (OSError, ValueError):
        return {"root": DEFAULT_ROOT_NAME, "apps": []}


_CONFIG_LOCK = threading.Lock()


def save_config(cfg: dict) -> None:
    """Write the whole settings file atomically (temp file, then rename)."""
    config_dir().mkdir(parents=True, exist_ok=True)
    tmp = config_dir() / "config.json.tmp"
    tmp.write_text(json.dumps(cfg, indent=1))
    os.replace(str(tmp), str(config_dir() / "config.json"))


def update_config(changes: Optional[dict] = None, remove: Tuple[str, ...] = (),
                  merge_key: str = "", merge: Optional[dict] = None,
                  drop_from: Tuple[str, str] = ("", "")) -> dict:
    """Change some settings and keep the rest (the Games tab keeps its own
    records in the same file). Safe to call from the window and a job at once."""
    with _CONFIG_LOCK:
        cfg = load_config()
        cfg.update(changes or {})
        for k in remove:
            cfg.pop(k, None)
        if merge_key:
            cfg.setdefault(merge_key, {}).update(merge or {})
        if drop_from[0]:
            cfg.get(drop_from[0], {}).pop(drop_from[1], None)
        save_config(cfg)
        return cfg


# --------------------------------------------------------------- top level
def refresh(root_name: Optional[str] = None) -> Tuple[Dict[str, int], List[Disk]]:
    cfg = load_config()
    root = view_root(root_name or cfg.get("root", DEFAULT_ROOT_NAME))
    disks = list_disks()
    return sync_view(root, desired_links(root, disks)), disks


def verify_view(root: Path) -> Step:
    if not (root / MARK).exists():
        return Step("This PC folder", False, "%s does not exist yet" % root)
    links = [e for f in (root, root / "Disks", root / "Users")
             for e in f.iterdir() if e.is_symlink()]
    bad = [e.name for e in links if not e.exists()]
    return Step("This PC folder", not bad,
                "%d shortcuts, all point somewhere real" % len(links) if not bad
                else "dangling: " + ", ".join(bad))


def install(root_name: str = DEFAULT_ROOT_NAME, apps: Optional[List[str]] = None) -> List[Step]:
    apps = apps or []
    steps: List[Step] = []
    try:
        stats, _ = refresh(root_name)
        steps.append(Step("Created the folder", True,
                          "%s (%d new shortcuts)" % (view_root(root_name), stats["created"])))
    except (RuntimeError, OSError) as e:
        return [Step("Created the folder", False, str(e))]
    update_config({"root": root_name, "apps": apps})
    steps.append(verify_view(view_root(root_name)))
    steps += install_timer()
    steps.append(gtk_bookmark(view_root(root_name), True))
    steps.append(kde_bookmark(view_root(root_name), True))
    for a in apps:
        steps.append(apply_override(a))
    return steps


def check() -> List[Step]:
    cfg = load_config()
    root = view_root(cfg.get("root", DEFAULT_ROOT_NAME))
    steps = [verify_view(root)]
    lib = data_dir() / "lib" / "ddg" / "__init__.py"
    steps.append(Step("Program copy", lib.exists() and __version__ in lib.read_text(),
                      str(lib.parent) if lib.exists() else "not installed"))
    rc, out = run(["systemctl", "--user", "is-active", UNIT + ".timer"])
    steps.append(Step("Auto-refresh timer", rc == 0, out or "not active"))
    g = gtk_bookmarks_path()
    steps.append(Step("GTK sidebar bookmark", g.exists() and _uri(root) in g.read_text(),
                      "present" if g.exists() and _uri(root) in g.read_text() else "missing"))
    for a in cfg.get("apps", []):
        on = override_state(a)
        steps.append(Step("Flatpak access: " + a, on, "granted" if on else "not granted"))
    return steps


def uninstall() -> List[Step]:
    cfg = load_config()
    root = view_root(cfg.get("root", DEFAULT_ROOT_NAME))
    steps = [remove_view(root), gtk_bookmark(root, False), kde_bookmark(root, False)]
    steps += remove_timer()
    steps += [reset_override(a) for a in cfg.get("apps", [])]
    update_config(remove=("root", "apps"))   # game estimates and records stay
    return steps


def status() -> dict:
    cfg = load_config()
    root = view_root(cfg.get("root", DEFAULT_ROOT_NAME))
    disks = list_disks()
    names = display_names(disks)
    want = desired_links(root, disks)
    managers = find_file_managers()
    return {
        "version": __version__,
        "root": str(root),
        "disks": [{"dev": d.dev, "name": names.get(d.dev, d.label or d.dev),
                   "size": human_size(d.size), "fstype": d.fstype,
                   "mounted": d.mounted, "mountpoint": d.mountpoint,
                   "removable": d.removable} for d in disks],
        "preview": [{"path": "%s / %s" % (root.name, " / ".join(k.relative_to(root).parts)),
                     "target": v} for k, v in sorted(want.items(), key=lambda kv: str(kv[0]))],
        "managers": [{"id": m, "granted": override_state(m)} for m in managers],
        "installed": (root / MARK).exists(),
    }
