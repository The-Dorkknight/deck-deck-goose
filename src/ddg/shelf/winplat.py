# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Everything Windows-specific: where Steam is, which drives exist, whether
Steam is running, Windows' file compression (compact.exe /EXE), real on-disk
sizes, and file locking.

On a machine that is not Windows (the test machine), or when DDG_FAKE_WIN
points at a JSON description, a simulated Windows is used instead, so the
rest of the program can be tested. The simulation is only for tests and
screenshots.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import IS_WINDOWS, config_dir, lp, norm, unlp

ALGORITHMS = ("XPRESS4K", "XPRESS8K", "XPRESS16K", "LZX")
DEFAULT_ALGO = "XPRESS8K"
PLATFORM = "windows"
PLATFORM_NAME = "Windows"
GOOD_FS = {"NTFS", "REFS"}
WARN_FS = {"EXFAT"}
BAD_FS: Dict[str, str] = {}
FS_ADVICE = "NTFS"
KINDS = {0: "unknown", 1: "none", 2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}
FILE_READ_ONLY_VOLUME = 0x00080000
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
CREATE_NO_WINDOW = 0x08000000


@dataclass
class Drive:
    letter: str          # "D:" (or a folder for a mounted volume)
    root: Path           # "D:\\"
    label: str
    fstype: str          # "NTFS", "exFAT", "FAT32", "ReFS"
    kind: str            # fixed / removable / network / cdrom / ramdisk
    readonly: bool

    @property
    def name(self) -> str:
        lab = self.label or ("Local Disk" if self.kind == "fixed" else "Removable Disk")
        return "%s (%s)" % (lab, self.letter)


def fake_spec() -> Optional[dict]:
    f = os.environ.get("DDG_FAKE_WIN")
    if not f:
        return None
    return json.loads(Path(f).read_text(encoding="utf-8"))


def _state_file() -> Path:
    return config_dir() / "fake-state.json"


def fake_state() -> dict:
    try:
        st = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    spec = fake_spec() or {}
    st.setdefault("steam_running", bool(spec.get("steam_running")))
    st.setdefault("compact_calls", [])
    st.setdefault("compressed", {})
    return st


def save_fake_state(st: dict) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(st), encoding="utf-8")


SIMULATED = not IS_WINDOWS or bool(os.environ.get("DDG_FAKE_WIN"))

_K32 = None


def _k32():
    """kernel32 with reliable error codes (use_last_error) and declared types."""
    global _K32
    if _K32 is None:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetLogicalDrives.restype = wintypes.DWORD
        k.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        k.GetDriveTypeW.restype = wintypes.UINT
        k.SetThreadErrorMode.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        k.SetThreadErrorMode.restype = wintypes.BOOL
        k.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                            ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
        k.GetVolumeInformationW.restype = wintypes.BOOL
        k.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        k.GetVolumePathNameW.restype = wintypes.BOOL
        k.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.GetCompressedFileSizeW.restype = wintypes.DWORD
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        _K32 = k
    return _K32


# ------------------------------------------------------------------- Steam
def steam_roots() -> List[Path]:
    if SIMULATED:
        spec = fake_spec() or {}
        return [Path(p) for p in spec.get("steam_roots", []) if Path(p).is_dir()]
    import winreg
    cands: List[str] = []
    for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as k:
                cands.append(str(winreg.QueryValueEx(k, value)[0]))
        except OSError:
            pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            cands.append(os.path.join(os.environ[env], "Steam"))
    seen, out = set(), []
    for c in cands:
        p = os.path.normpath(c)
        if os.path.isdir(os.path.join(p, "steamapps")) and norm(p) not in seen:
            seen.add(norm(p))
            out.append(Path(p))
    return out


def steam_running() -> bool:
    if SIMULATED:
        return bool(fake_state()["steam_running"])
    names = _process_names()
    if names is not None:
        return "steam.exe" in names
    try:   # fallback if the process list could not be read directly
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return '"steam.exe"' in out.lower()


def _process_names() -> Optional[set]:
    """Lower-case names of running programs, read directly from Windows."""
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
        k = _k32()
        k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k.Process32FirstW.restype = k.Process32NextW.restype = wintypes.BOOL
        snap = k.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
        if not snap or snap == wintypes.HANDLE(-1).value:
            return None
        try:
            e = PROCESSENTRY32W()
            e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            names = set()
            ok = k.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                names.add(e.szExeFile.lower())
                ok = k.Process32NextW(snap, ctypes.byref(e))
            return names
        finally:
            k.CloseHandle(snap)
    except Exception:
        return None


def shutdown_steam(root: Path) -> None:
    if SIMULATED:
        st = fake_state()
        st["steam_running"] = False
        save_fake_state(st)
        return
    exe = root / "steam.exe"
    try:
        subprocess.Popen([str(exe), "-shutdown"], creationflags=CREATE_NO_WINDOW)
    except OSError:
        pass


# ------------------------------------------------------------------ drives
def drives() -> List[Drive]:
    if SIMULATED:
        spec = fake_spec() or {}
        return [Drive(d["letter"], Path(d["root"]), d.get("label", ""), d.get("fstype", "NTFS"),
                      d.get("kind", "fixed"), bool(d.get("readonly"))) for d in spec.get("drives", [])]
    k32 = _k32()
    mask = k32.GetLogicalDrives()
    out = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = "%s:" % chr(65 + i)
        info = _volume_info(letter + "\\")
        if info:
            out.append(Drive(letter, Path(letter + "\\"), info["label"], info["fstype"],
                             info["kind"], info["readonly"]))
    return out


def _volume_info(root: str) -> Optional[Dict[str, object]]:
    import ctypes
    from ctypes import wintypes
    k32 = _k32()
    kind = KINDS.get(k32.GetDriveTypeW(root), "unknown")
    if kind in ("network", "none", "unknown"):
        # asking a disconnected network drive for details can hang for a long time;
        # these are never used for libraries anyway
        return {"label": "", "fstype": "", "kind": kind, "readonly": False, "root": root}
    label = ctypes.create_unicode_buffer(261)
    fsname = ctypes.create_unicode_buffer(261)
    serial, maxlen, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    old = wintypes.DWORD(0)
    k32.SetThreadErrorMode(1, ctypes.byref(old))   # no "insert a disk" pop-ups (this thread only)
    try:
        ok = k32.GetVolumeInformationW(root, label, 261, ctypes.byref(serial),
                                       ctypes.byref(maxlen), ctypes.byref(flags), fsname, 261)
    finally:
        k32.SetThreadErrorMode(old.value, None)
    if not ok:
        return None
    return {"label": label.value, "fstype": fsname.value, "kind": kind,
            "readonly": bool(flags.value & FILE_READ_ONLY_VOLUME), "root": root}


def volume_of(path) -> Dict[str, object]:
    """Drive facts for any path (also works for folders that don't exist yet)."""
    p = unlp(str(path))
    if SIMULATED:
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
    import ctypes
    k32 = _k32()
    probe = os.path.abspath(p)
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    buf = ctypes.create_unicode_buffer(1024)
    if not k32.GetVolumePathNameW(probe, buf, 1024):
        return {"root": "", "letter": "", "fstype": "unknown", "kind": "unknown", "readonly": False, "label": ""}
    info = _volume_info(buf.value) or {"label": "", "fstype": "unknown", "kind": "unknown", "readonly": False}
    root = buf.value
    return {"root": root, "letter": root.rstrip("\\"), **{k: info[k] for k in ("label", "fstype", "kind", "readonly")}}


def is_reparse_point(path: str) -> bool:
    """Junctions and symlinks (rare in games). They are not copied blindly."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT) or os.path.islink(path)


# ------------------------------------------------------------- compression
def compressed_size(path: str) -> Optional[int]:
    """Real space the file takes after Windows compression (None if unknown)."""
    if SIMULATED:
        try:
            st = os.stat(path)
        except OSError:
            return None
        rec = fake_state()["compressed"].get(norm(path))
        # Writing to a compressed file makes Windows store it uncompressed again
        if rec and rec[1] == st.st_mtime and rec[2] == st.st_size:
            return int(rec[0])
        return st.st_size
    import ctypes
    from ctypes import wintypes
    k32 = _k32()
    high = wintypes.DWORD(0)
    ctypes.set_last_error(0)
    low = k32.GetCompressedFileSizeW(lp(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:   # INVALID_FILE_SIZE plus a real error
        return None
    return (high.value << 32) + low


def compact(files: List[str], algo: Optional[str]) -> List[str]:
    """Compress (algo) or uncompress (None) files with compact.exe /EXE.
    Returns error lines (empty if all went well)."""
    if SIMULATED:
        return _fake_compact(files, algo)
    errors: List[str] = []
    base = ["compact", "/U", "/EXE", "/I", "/Q"] if algo is None else \
        ["compact", "/C", "/EXE:%s" % algo, "/I", "/Q"]
    batch: List[str] = []
    length = 0

    def flush() -> None:
        nonlocal batch, length
        if not batch:
            return
        try:
            p = subprocess.run(base + batch, capture_output=True, timeout=7200,
                               creationflags=CREATE_NO_WINDOW)
            if p.returncode:
                raw = p.stdout + p.stderr
                try:
                    out = raw.decode("oem", errors="replace")   # console code page
                except LookupError:
                    out = raw.decode(errors="replace")
                errors.append(out.strip().splitlines()[-1] if out.strip() else "compact exit %d" % p.returncode)
        except (OSError, subprocess.SubprocessError) as e:
            errors.append(str(e))
        batch, length = [], 0

    for f in files:
        batch.append(f)
        length += len(f) + 3
        if length > 20000 or len(batch) >= 200:
            flush()
    flush()
    return errors


def _fake_compact(files: List[str], algo: Optional[str]) -> List[str]:
    from . import estimate
    st = fake_state()
    st["compact_calls"].append({"algo": algo, "files": [os.path.basename(f) for f in files]})
    if os.environ.get("DDG_FAKE_COMPACT_ERROR"):
        save_fake_state(st)
        return ["Access is denied. [%s]" % os.path.basename(files[0])] if files else []
    for f in files:
        s = os.stat(f)
        if algo is None:
            st["compressed"].pop(norm(f), None)
        else:
            with open(f, "rb") as fh:
                data = fh.read()
            st["compressed"][norm(f)] = [estimate.model_size(data, algo), s.st_mtime, s.st_size]
    save_fake_state(st)
    return []


# -------------------------------------------------------------------- lock
def lock_file(fd: int) -> bool:
    try:
        if IS_WINDOWS:
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def unlock_file(fd: int) -> None:
    try:
        if IS_WINDOWS:
            import msvcrt
            os.lseek(fd, 0, 0)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
