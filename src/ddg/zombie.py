# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Zombie-Deck inside Deck Deck Goose (the "Wake" tab).

Zombie-Deck stays its own project: root systemd units that bring a handheld properly back from
sleep or hibernation. Deck Deck Goose bundles a copy under components/zombie-deck/, and only
  * installs / updates / removes it by calling Zombie-Deck's own installer (one password window),
  * turns it on or off with its own `enable` / `disable`,
  * shows `zombie-deck json` (version, armed, last wake result) and its log.
Nothing in components/ is ever edited. Linux only; on other systems every call says so.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import library
from .core import Step

NOT_LINUX = "Zombie-Deck is for Linux handhelds (Bazzite / Nobara)."


def bundled_dir() -> Path:
    env = os.environ.get("DDG_ZOMBIE_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for c in (here.parent.parent / "components" / "zombie-deck", here / "components" / "zombie-deck"):
        if (c / "install.sh").exists():
            return c
    return here.parent.parent / "components" / "zombie-deck"


def binary() -> Optional[str]:
    env = os.environ.get("DDG_ZOMBIE_BIN")
    if env:
        return env if os.access(env, os.X_OK) else None
    for c in (shutil.which("zombie-deck"), "/usr/local/bin/zombie-deck"):
        if c and os.access(c, os.X_OK):
            return c
    return None


def version_tuple(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4]) or (0,)


def bundled_version() -> str:
    try:
        m = re.search(r'^ZD_VERSION="([^"]+)"', (bundled_dir() / "src" / "zombie-deck").read_text(), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def _run(cmd: List[str], timeout: int = 15) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def raw_status() -> Optional[dict]:
    b = binary()
    if not b:
        return None
    rc, out = _run([b, "json"])
    try:
        return json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        return {"app": "zombie-deck", "version": "", "unreadable": out[:200]}


def band(st: Optional[dict], bundled: str) -> Dict[str, str]:
    """The coloured line the Wake tab shows, from the facts alone."""
    if st is None:
        return {"state": "missing", "colour": "#6b7280", "text": "Not installed (your handheld sleeps the stock way)"}
    if st.get("unreadable") is not None:
        return {"state": "broken", "colour": "#f5a623", "text": "Installed, but its status could not be read: press Install / Update to repair"}
    if st.get("disabled"):
        return {"state": "off", "colour": "#6b7280", "text": "Off (stock sleep). Turn it on to get the wake fix back"}
    if not st.get("armed"):
        return {"state": "unarmed", "colour": "#f5a623", "text": "Installed but not armed: press Install / Update to repair"}
    sl, wk = st.get("sleeps"), st.get("wakes")
    if st.get("last_result") == "fail":
        return {"state": "fail", "colour": "#ef4444", "text": "Last wake failed: open the log"}
    if isinstance(sl, int) and isinstance(wk, int) and wk < sl:
        return {"state": "behind", "colour": "#f5a623", "text": "It went to sleep %d times but only revived %d: open the log" % (sl, wk)}
    when = (st.get("last_wake") or "")[-8:-3]
    return {"state": "ok", "colour": "#22c55e",
            "text": "Wake fix armed" + (" · last wake OK (%s)" % when if when and st.get("last_result") == "ok" else " · waiting for the first sleep")}


def status() -> dict:
    if not library.is_linux():
        return {"supported": False, "message": NOT_LINUX}
    st = raw_status()
    bv = bundled_version()
    iv = (st or {}).get("version", "") if st else ""
    can_update = bool(st) and bool(bv) and version_tuple(bv) > version_tuple(iv)
    newer_installed = bool(st) and bool(iv) and version_tuple(iv) > version_tuple(bv or "0")
    return {"supported": True, "installed": st is not None, "version": iv, "bundled": bv,
            "update": can_update, "newer_installed": newer_installed, "bundle_ok": bool(bv),
            "band": band(st, bv), "status": st,
            "icon": "ok" if not st or st.get("last_result") != "fail" else "fail"}


def _privileged(cmd: List[str], stdin: str = "") -> Tuple[int, str]:
    if os.environ.get("DDG_FAKE_PKEXEC"):
        pre: List[str] = []
    else:
        pre = ["pkexec"]
    try:
        p = subprocess.run(pre + cmd, input=stdin, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return 127, "pkexec is not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out waiting for the password window"
    return p.returncode, (p.stdout + p.stderr).strip()


def _guard(name: str) -> Optional[Step]:
    if not library.is_linux():
        return Step(name, False, NOT_LINUX)
    from . import gamemode
    if gamemode.in_game_mode():
        return Step(name, False, "Do this from Desktop Mode: the password window can't appear in Game Mode.")
    return None


def _result(name: str, rc: int, out: str, good: str) -> Step:
    if rc in (126, 127):
        return Step(name, False, "Not changed: %s." % ("the password window was cancelled" if rc == 126 else out))
    tail = " ".join(out.splitlines()[-3:])
    return Step(name, rc == 0, good if rc == 0 else (tail or "failed (code %d)" % rc))


def install() -> List[Step]:
    name = "Install Zombie-Deck"
    bad = _guard(name)
    if bad:
        return [bad]
    d = bundled_dir()
    if not (d / "install.sh").exists():
        return [Step(name, False, "The bundled copy is missing (components/zombie-deck).")]
    st = raw_status()
    if st and version_tuple(st.get("version", "")) > version_tuple(bundled_version()):
        return [Step(name, False, "A newer Zombie-Deck (%s) is already installed; not replacing it with the bundled %s." %
                     (st.get("version"), bundled_version()))]
    rc, out = _privileged(["sh", str(d / "install.sh"), "install"])
    s = raw_status()
    ok = rc == 0 and bool(s) and bool(s.get("armed"))
    if rc == 0 and not ok:
        return [Step(name, False, "Installed, but it does not report as armed: run Check in the Zombie-Deck menu.")]
    return [_result(name, 0 if ok else rc, out, "Zombie-Deck %s installed and armed" % ((s or {}).get("version") or bundled_version()))]


def uninstall() -> List[Step]:
    name = "Remove Zombie-Deck"
    bad = _guard(name)
    if bad:
        return [bad]
    d = bundled_dir()
    rc, out = _privileged(["sh", str(d / "install.sh"), "uninstall"], stdin="n\n")     # keep the config file
    gone = binary() is None
    return [_result(name, 0 if (rc == 0 and gone) else (rc or 1), out, "removed (your settings file was kept)")]


def set_enabled(on: bool) -> List[Step]:
    name = "Turn Zombie-Deck %s" % ("on" if on else "off")
    bad = _guard(name)
    if bad:
        return [bad]
    b = binary()
    if not b:
        return [Step(name, False, "Zombie-Deck is not installed yet.")]
    rc, out = _privileged([b, "enable" if on else "disable"])
    return [_result(name, rc, out, "on" if on else "off (stock sleep)")]


def log_text(lines: int = 60) -> str:
    b = binary()
    if not b:
        return "Zombie-Deck is not installed."
    rc, out = _run([b, "log"])
    return "\n".join(out.splitlines()[-lines:])


def sleep_dropin_dir() -> Path:
    return Path(os.environ.get("DDG_ZOMBIE_SLEEPD") or "/etc/zombie-deck/sleep.d")
