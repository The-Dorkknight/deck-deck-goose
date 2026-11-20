# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Desktop guard: Airlock must never lock you out of the real Desktop Mode.

Airlock's nested Plasma runs as you, with your real settings and your real per-user services. Anything it changes
there can follow you into Desktop Mode: the display layout of its small virtual screen, panel layout, a saved
session, or the user session's environment pointing at Airlock's display. Plasma then trips over it at the next
Desktop Mode start and can drop straight back to Game Mode (seen on the Ally on 2026-11-15; the exact cause was not
pinned down, which is why this guards every one of these paths).

So, around every Airlock:
* begin(): copies the files below and the user session's environment to a backup folder first;
* end(): puts them back exactly (and removes ones that did not exist before); safe to call twice;
* repair_stale(): if Airlock died without end(), does it at the next Airlock / Deck Deck Goose start;
* repair(): the "Repair Desktop Mode" button: all of the above plus Airlock's own leftovers.
Anything you change in Plasma's look *inside* Airlock is therefore not kept: Airlock's desktop is a temporary view.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import core

# relative to ~/.config (files) and ~/.local/share (folders)
CONFIG_FILES = ("kwinoutputconfig.json", "kwinrc", "plasmashellrc", "plasma-org.kde.plasma.desktop-appletsrc",
                "ksmserverrc", "plasma-localerc", "kscreenlockerrc")
CONFIG_DIRS = ("session",)
DATA_DIRS = ("kscreen",)
# variables the nested session sets; the user session's own values are what Desktop Mode will inherit
ENV_KEYS = ("WAYLAND_DISPLAY", "DISPLAY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XDG_SESSION_TYPE",
            "KDE_FULL_SESSION", "KDE_SESSION_VERSION", "QT_QPA_PLATFORM", "DDG_AIRLOCK")


def _cfg() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (core.home() / ".config"))


def _data() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (core.home() / ".local" / "share"))


def _items() -> Dict[str, Path]:
    out = {"config/" + n: _cfg() / n for n in CONFIG_FILES + CONFIG_DIRS}
    out.update({"data/" + n: _data() / n for n in DATA_DIRS})
    return out


def backup_dir() -> Path:
    from . import airlock
    return airlock.state_dir() / "desktop-guard"


def manifest() -> Path:
    return backup_dir() / "manifest.json"


# ------------------------------------------------------------------ the user session's environment
def _sc() -> Optional[str]:
    return os.environ.get("DDG_SYSTEMCTL") or shutil.which("systemctl")


def user_env() -> Optional[Dict[str, str]]:
    fake = os.environ.get("DDG_FAKE_USERENV")
    if fake:
        try:
            return json.loads(Path(fake).read_text())
        except (OSError, ValueError):
            return {}
    sc = _sc()
    if not sc:
        return None
    try:
        p = subprocess.run([sc, "--user", "show-environment"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    env = {}
    for line in p.stdout.splitlines():
        k, sep, v = line.partition("=")
        if sep and k in ENV_KEYS:
            env[k] = v[1:-1] if len(v) > 1 and v[0] == v[-1] == "'" else v
    return env


def _env_apply(sets: Dict[str, str], unsets: List[str]) -> bool:
    fake = os.environ.get("DDG_FAKE_USERENV")
    if fake:
        env = user_env() or {}
        env.update(sets)
        for k in unsets:
            env.pop(k, None)
        Path(fake).write_text(json.dumps(env))
        return True
    sc = _sc()
    if not sc:
        return False
    ok = True
    try:
        if sets:
            ok &= subprocess.run([sc, "--user", "set-environment"] + ["%s=%s" % kv for kv in sets.items()],
                                 capture_output=True, timeout=10).returncode == 0
        if unsets:
            ok &= subprocess.run([sc, "--user", "unset-environment"] + list(unsets), capture_output=True,
                                 timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    return ok


# ------------------------------------------------------------------ begin / end
def begin() -> bool:
    """Back up before Airlock opens. Keeps an existing backup (a retry of the same Airlock, or one that died)."""
    if manifest().exists():
        repair_stale()
        if manifest().exists():           # still ours (same Airlock retrying another way): keep the first copy
            return True
    d = backup_dir()
    try:
        shutil.rmtree(str(d), ignore_errors=True)
        (d / "files").mkdir(parents=True)
        present = {}
        for rel, src in _items().items():
            present[rel] = src.exists()
            if not src.exists():
                continue
            dst = d / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(str(src), str(dst), symlinks=True)
            else:
                shutil.copy2(str(src), str(dst))
        man = {"pid": os.getpid(), "time": time.time(), "present": present, "env": user_env()}
        tmp = manifest().with_suffix(".tmp")
        tmp.write_text(json.dumps(man))
        os.replace(str(tmp), str(manifest()))      # written last: a half-made backup is never used
        return True
    except OSError:
        shutil.rmtree(str(d), ignore_errors=True)
        return False


def _same(a: Path, b: Path) -> bool:
    try:
        if a.is_dir() != b.is_dir():
            return False
        if a.is_file():
            return a.read_bytes() == b.read_bytes()
        la = sorted(p.relative_to(a).as_posix() for p in a.rglob("*"))
        lb = sorted(p.relative_to(b).as_posix() for p in b.rglob("*"))
        return la == lb and all(_same(a / r, b / r) for r in la if (a / r).is_file())
    except OSError:
        return False


def end() -> List[str]:
    """Put everything back as it was before Airlock. Returns what had changed (empty when nothing did)."""
    try:
        man = json.loads(manifest().read_text())
    except (OSError, ValueError):
        return []
    d = backup_dir() / "files"
    fixed: List[str] = []
    for rel, dst in _items().items():
        was = bool(man.get("present", {}).get(rel))
        src = d / rel
        try:
            if was and src.exists():
                if dst.exists() and _same(src, dst):
                    continue
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(str(dst))
                elif dst.exists() or dst.is_symlink():
                    dst.unlink()
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(str(src), str(dst), symlinks=True)
                else:
                    shutil.copy2(str(src), str(dst))
                fixed.append("restored " + rel.split("/", 1)[1])
            elif not was and (dst.exists() or dst.is_symlink()):
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(str(dst))
                else:
                    dst.unlink()
                fixed.append("removed " + rel.split("/", 1)[1] + " (made inside Airlock)")
        except OSError as e:
            fixed.append("could not restore %s: %s" % (rel, e))
    before = man.get("env")
    now = user_env()
    if isinstance(before, dict) and now is not None:
        sets = {k: v for k, v in before.items() if now.get(k) != v}
        unsets = [k for k in ENV_KEYS if k in now and k not in before]
        if sets or unsets:
            if _env_apply(sets, unsets):
                fixed.append("user session environment put back (%s)" % ", ".join(sorted(list(sets) + unsets)))
            else:
                fixed.append("could not put the user session environment back")
    shutil.rmtree(str(backup_dir()), ignore_errors=True)
    if fixed:
        try:
            from . import airlock
            airlock.log("ADGR", "; ".join(fixed)[:600])
        except Exception:  # noqa: BLE001
            pass
    return fixed


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def repair_stale() -> List[str]:
    """An Airlock that died without end(): restore now (only when that Airlock is really gone)."""
    try:
        man = json.loads(manifest().read_text())
    except (OSError, ValueError):
        return []
    if _alive(man.get("pid")) and int(man.get("pid", 0)) != os.getpid():
        return []
    return end()


def repair() -> dict:
    """The 'Repair Desktop Mode' button: everything Deck Deck Goose can do without a password."""
    from . import airlock, touchmode
    done: List[str] = []
    st = airlock.read_state()
    if st.get("status") == "open" and airlock._pid_alive(st.get("pid")):
        return {"ok": False, "done": [], "message": "An Airlock is open. Close it first (Back to Game), then repair."}
    if st.get("status") in ("open", "switching", "starting"):
        airlock.write_state(st.get("mode", "game"), "closed", reason="repair (stale state)")
        done.append("Airlock's state said '%s' with nothing running: reset" % st.get("status"))
    try:
        airlock.ready_path().unlink()
        done.append("removed a stale Airlock ready file")
    except OSError:
        pass
    try:
        lp = airlock.lock_path()
        if lp.exists():
            lp.unlink()
            done.append("removed a stale Airlock lock file")
    except OSError:
        pass
    r = airlock.cleanup()
    if r.get("closed"):
        done.append("closed leftovers: " + ", ".join(r["closed"]))
    done += end()
    try:
        if touchmode.repair_stale():
            done.append("Steam's touch mode put back")
    except Exception:  # noqa: BLE001
        pass
    msg = ("Repaired: " + "; ".join(done)) if done else "Nothing to repair: Airlock left nothing behind."
    msg += " If Desktop Mode still returns to Game Mode, run Can't Bear It - Rescue (option 1) for a full report."
    return {"ok": True, "done": done, "message": msg}
