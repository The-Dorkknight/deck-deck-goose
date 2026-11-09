# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Everything macOS-specific: where Steam is, which volumes exist, whether Steam
is running, how to ask it to quit. There is no file compression on a Mac here
(APFS compresses files only in ways Steam and games do not expect), so the
Compress buttons are off; moving games and clearing junk work.

On a machine that is not a Mac, or when DDG_FAKE_MAC points at a JSON file,
a simulated Mac is used so the rest of the program can be tested.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .common import config_dir, norm
from .winplat import Drive

PLATFORM = "mac"
PLATFORM_NAME = "macOS"
ALGORITHMS = ()
DEFAULT_ALGO = ""
GOOD_FS = {"APFS", "HFS", "HFS+"}
WARN_FS = {"EXFAT"}
BAD_FS = {"NTFS": "macOS can only read NTFS drives, so Steam cannot install there",
          "UDF": "not suitable for games", "CD9660": "not suitable for games"}
FS_ADVICE = "APFS or Mac OS Extended (exFAT also works)"
DATA_VOLUME = "/System/Volumes/Data"
SIMULATED = sys.platform != "darwin" or bool(os.environ.get("DDG_FAKE_MAC"))


def fake_spec() -> Optional[dict]:
    f = os.environ.get("DDG_FAKE_MAC")
    return json.loads(Path(f).read_text(encoding="utf-8")) if f else None


def _state_file() -> Path:
    return config_dir() / "fake-mac-state.json"


def _fake_running() -> bool:
    try:
        return bool(json.loads(_state_file().read_text(encoding="utf-8")).get("steam_running"))
    except (OSError, ValueError):
        return bool((fake_spec() or {}).get("steam_running"))


# ------------------------------------------------------------------- Steam
def steam_roots() -> List[Path]:
    if SIMULATED:
        return [Path(p) for p in (fake_spec() or {}).get("steam_roots", []) if Path(p).is_dir()]
    p = Path.home() / "Library" / "Application Support" / "Steam"
    return [p] if (p / "steamapps").is_dir() else []


def steam_running() -> bool:
    if SIMULATED:
        return _fake_running()
    try:
        return subprocess.run(["pgrep", "-x", "steam_osx"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def shutdown_steam(root: Path) -> None:
    if SIMULATED:
        _state_file().parent.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps({"steam_running": False}), encoding="utf-8")
        return
    try:
        subprocess.Popen(["osascript", "-e", 'tell application "Steam" to quit'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ------------------------------------------------------------------ volumes
def parse_mount(text: str) -> List[dict]:
    """Lines like '/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled)'."""
    out = []
    for line in text.splitlines():
        if " on " not in line or "(" not in line:
            continue
        dev, rest = line.split(" on ", 1)
        mp, _, opts = rest.rpartition(" (")
        opts = [o.strip() for o in opts.rstrip(")").split(",")]
        if not opts or not mp:
            continue
        out.append({"dev": dev, "mount": mp, "fstype": opts[0].upper(), "opts": opts[1:],
                    "local": "local" in opts or dev.startswith("/dev/"),
                    "readonly": "read-only" in opts})
    return out


def _mount_table() -> List[dict]:
    f = os.environ.get("DDG_MOUNTS_FILE")
    if f:
        return parse_mount(Path(f).read_text(encoding="utf-8"))
    try:
        return parse_mount(subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=15).stdout)
    except (OSError, subprocess.SubprocessError):
        return []


def _label(mount: str) -> str:
    return "Macintosh HD" if mount in ("/", DATA_VOLUME) else os.path.basename(mount.rstrip("/")) or mount


def _kind(m: dict) -> str:
    if m["fstype"] in ("SMBFS", "NFS", "AFPFS", "WEBDAV", "CIFS") or not m["local"]:
        return "network"
    if m["fstype"] in ("CD9660", "UDF"):
        return "cdrom"
    if m["mount"] in ("/", DATA_VOLUME):
        return "fixed"
    return "removable"


def _drive(m: dict) -> Drive:
    mp = "/" if m["mount"] == DATA_VOLUME else m["mount"]
    return Drive(mp, Path(m["mount"]), _label(m["mount"]), m["fstype"], _kind(m),
                 m["readonly"] and m["mount"] != DATA_VOLUME)


def drives() -> List[Drive]:
    if SIMULATED and fake_spec() is not None:
        return [Drive(d["letter"], Path(d["root"]), d.get("label", ""), d.get("fstype", "APFS"),
                      d.get("kind", "fixed"), bool(d.get("readonly"))) for d in fake_spec().get("drives", [])]
    out = []
    for m in _mount_table():
        if m["mount"] == DATA_VOLUME or m["mount"].startswith("/Volumes/"):
            if m["mount"].startswith("/Volumes/") and m["fstype"] in ("DEVFS", "AUTOFS"):
                continue
            out.append(_drive(m))
    if not any(str(d.root) == DATA_VOLUME for d in out):            # older macOS: one startup volume
        for m in _mount_table():
            if m["mount"] == "/":
                out.insert(0, _drive(m))
    return out


def volume_of(path) -> Dict[str, object]:
    """Volume facts for any path (also for folders that don't exist yet)."""
    p = os.path.realpath(str(path))
    if SIMULATED and fake_spec() is not None:
        best = None
        for d in drives():
            r = norm(d.root)
            if (norm(p) == r or norm(p).startswith(r.rstrip(os.sep) + os.sep)) and \
                    (best is None or len(r) > len(norm(best.root))):
                best = d
        if best is None:
            return {"root": "", "letter": "", "fstype": "unknown", "kind": "unknown", "readonly": False, "label": ""}
        return {"root": str(best.root), "letter": best.letter, "fstype": best.fstype, "kind": best.kind,
                "readonly": best.readonly, "label": best.label}
    best = None
    for m in _mount_table():
        mp = m["mount"].rstrip("/") or "/"
        if p == mp or p.startswith(mp.rstrip("/") + "/") or mp == "/":
            if m["mount"] == "/" and any(x["mount"] == DATA_VOLUME for x in _mount_table()) and \
                    not p.startswith("/System/") and not p.startswith("/usr/"):
                continue                      # user files live on the Data volume, not the sealed system one
            if best is None or len(m["mount"]) > len(best["mount"]):
                best = m
    if best is None:
        return {"root": "", "letter": "", "fstype": "unknown", "kind": "unknown", "readonly": False, "label": ""}
    d = _drive(best)
    return {"root": str(d.root), "letter": d.letter, "fstype": d.fstype, "kind": d.kind,
            "readonly": d.readonly, "label": d.label}


def is_reparse_point(path: str) -> bool:
    return os.path.islink(path)


# ------------------------------------------------------------- compression
def compressed_size(path: str) -> Optional[int]:
    return None


def compact(files: List[str], algo: Optional[str]) -> List[str]:
    return ["Compression is not available on macOS."] if files else []


# -------------------------------------------------------------------- lock
def lock_file(fd: int) -> bool:
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, ImportError):
        return False


def unlock_file(fd: int) -> None:
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except (OSError, ImportError):
        pass
