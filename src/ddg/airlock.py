# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Airlock: a desktop "portal" you open from Game Mode.

Tap the Airlock tile in Steam's Game Mode and a KDE desktop opens in a window
inside Game Mode. Your game and Steam keep running; close it and you are
straight back, with no logout and no reboot.

Fallback ladder (each step is tried only if the one before it failed):
  1. full    - nested Plasma desktop in a window inside gamescope (game keeps running)
  2. lite    - nested KWin + just a file manager / terminal (less RAM)
  3. switch  - real switch to Desktop Mode with steamos-session-select (the game
               closes; always asks first)
  4. advise  - tells you how to switch by hand. NEVER reboots by itself.

Written by Matthew Armstrong with Claude; first built as a separate "Airlock
workbench" and merged into Deck Deck Goose in 0.5.0-wip (sleep hook fixed in 0.6.0-wip). Standard library only.
NOT yet tested on a real ROG Ally: see docs/RESEARCH.md and `ddg airlock probe`.
"""
from __future__ import annotations

import configparser
try:
    import fcntl
except ImportError:  # Windows: this module's Linux-only parts are never used there
    fcntl = None
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import __build_date__, __version__, core, gamemode, padbridge, touchmode
from .core import Step

APP = "deck-deck-goose"
DATA = Path(__file__).resolve().parent / "airlock_data"
NESTED_SOCKET = "wayland-ddg"
SLEEP_HOOK_NAME = "40-ddg-airlock"
BACK_LAUNCHER = "ddg-back-to-game.desktop"

# ErrCode-style 4-character codes (see the ErrCode project); kept stable once released.
ERRCODES = {
    "A0OK": "OK",
    "A1KW": "kwin_wayland not found",
    "A2PS": "plasmashell not found (full desktop skipped)",
    "A3TO": "nested desktop did not appear before the watchdog timeout",
    "A4EX": "nested compositor exited straight away",
    "A5RM": "not enough free RAM for this mode",
    "A6LK": "a portal is already open",
    "A7SS": "session switch command not found",
    "A8NG": "not running inside Game Mode",
    "A9BT": "battery low",
    "AAHK": "a mode-change hook failed (ignored)",
    "ABNO": "user said no to the session switch",
    "ACDP": "no display to nest into (DISPLAY / GAMESCOPE_WAYLAND_DISPLAY unset)",
    "ADST": "closed desktop programs left behind by Airlock",
    "AEPD": "controller bridge not running (the pad will not move the mouse)",
}

DEFAULTS = {
    "watchdog_seconds": "15",
    "min_ram_full_mb": "1500",
    "min_ram_lite_mb": "600",
    "low_battery_pct": "10",
    "width": "",
    "height": "",
    "resolution": "auto",      # auto = about 720p (bigger icons, faster) | native | WIDTHxHEIGHT
    "backend": "auto",         # auto = gamescope's Wayland first, then X11 | wayland | x11
    "pad": "on",               # controller drives the mouse/keyboard inside Airlock (on | off)
    "pad_speed": "normal",     # slow | normal | fast
    "touch_button": "on",      # small touch-mode button in the Airlock desktop's top-left corner (Game Mode only)
    "touch": "steam",          # touch mode Airlock starts in: steam (leave Steam's) or the last one picked (1,2,4,6)
    "lite_apps": "dolphin konsole",
    "allow_session_switch": "yes",
}

# the three mode colours (also used by the band at the top of the window and by RGB hooks)
INDICATORS = {
    "portal": {"mode": "portal", "colour": "#f5a623", "text": "AIRLOCK - game still running"},
    "desktop": {"mode": "desktop", "colour": "#3b82f6", "text": "DESKTOP MODE"},
    "game": {"mode": "game", "colour": "#22c55e", "text": "GAME MODE"},
    "unknown": {"mode": "unknown", "colour": "#9ca3af", "text": "Mode unknown"},
}


# ------------------------------------------------------------------ paths (looked up when used, so tests can move HOME)
def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or (core.home() / ".local" / "state")) / APP


def conf_path() -> Path:
    return core.config_dir() / "portal.ini"


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid())


def lock_path() -> Path:
    return runtime_dir() / (APP + "-portal.lock")


def ready_path() -> Path:
    return runtime_dir() / (APP + "-ready")


def state_file() -> Path:
    return state_dir() / "portal.json"


def log_file() -> Path:
    return state_dir() / "portal.log"


def hook_dirs() -> List[Path]:
    return [DATA / "hooks" / "mode-change.d", core.config_dir() / "hooks" / "mode-change.d"]


# ------------------------------------------------------------------ logging / state
def log(code: str, msg: str = "") -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    line = "%s [%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), code, ERRCODES.get(code, "?")) + \
        (" - %s" % msg if msg else "")
    f = log_file()
    with f.open("a") as fh:
        fh.write(line + "\n")
    try:                                    # keep the log small on a handheld
        if f.stat().st_size > 256_000:
            f.write_text("\n".join(f.read_text().splitlines()[-1000:]) + "\n")
    except OSError:
        pass


def write_state(mode: str, status: str, **extra) -> dict:
    state_dir().mkdir(parents=True, exist_ok=True)
    data = {"mode": mode, "status": status, "time": time.time(),
            "version": __version__, "build": __build_date__, **extra}
    tmp = state_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(state_file())
    return data


def read_state() -> dict:
    try:
        return json.loads(state_file().read_text())
    except (OSError, ValueError):
        return {}


def load_config() -> configparser.SectionProxy:
    cp = configparser.ConfigParser(interpolation=None)
    cp["portal"] = DEFAULTS
    try:
        cp.read(conf_path())
    except configparser.Error:
        pass                                # a broken portal.ini falls back to the defaults
    return cp["portal"]


# ------------------------------------------------------------------ detection
def detect_session(env: Optional[dict] = None) -> str:
    """'game' (gamescope Game Mode), 'desktop' (Plasma) or 'unknown'."""
    env = os.environ if env is None else env
    desk = env.get("XDG_CURRENT_DESKTOP", "").lower()
    if env.get("THISPC_GAMEMODE") or "gamescope" in desk or env.get("GAMESCOPE_WAYLAND_DISPLAY") \
            or env.get("SteamGamepadUI") == "1":
        return "game"
    if "kde" in desk or env.get("KDE_FULL_SESSION") == "true":
        return "desktop"
    return "unknown"


def os_release() -> dict:
    out = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v.strip().strip('"')
    except OSError:
        pass
    return out


def parse_kwin_help(text: str) -> dict:
    return {
        "x11_display": "--x11-display" in text,
        "wayland_display": "--wayland-display" in text,
        "exit_with_session": "--exit-with-session" in text,
        "no_lockscreen": "--no-lockscreen" in text,
        "socket": "--socket" in text,
        "width": "--width" in text,
        "xwayland": "--xwayland" in text,
    }


def detect(env: Optional[dict] = None) -> dict:
    env = dict(os.environ if env is None else env)
    osr = os_release()
    kwin = shutil.which("kwin_wayland")
    kflags: dict = {}
    if kwin:
        try:
            r = subprocess.run([kwin, "--help"], capture_output=True, text=True, timeout=10)
            kflags = parse_kwin_help(r.stdout + r.stderr)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "distro": osr.get("ID", "unknown"),
        "distro_name": osr.get("PRETTY_NAME", "unknown"),
        "variant": osr.get("VARIANT_ID", ""),
        "session": detect_session(env),
        "display": env.get("DISPLAY", ""),
        "gamescope_wayland": env.get("GAMESCOPE_WAYLAND_DISPLAY", ""),
        "kwin": kwin,
        "kwin_flags": kflags,
        "plasmashell": shutil.which("plasmashell"),
        "dbus_run_session": shutil.which("dbus-run-session"),
        "session_select": shutil.which("steamos-session-select"),
        "notify_send": shutil.which("notify-send"),
        "kdialog": shutil.which("kdialog"),
        "zenity": shutil.which("zenity"),
    }


def mem_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return -1


def battery() -> Tuple[int, bool]:
    """(percent or -1, discharging?)"""
    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        try:
            pct = int((bat / "capacity").read_text().strip())
            return pct, (bat / "status").read_text().strip().lower() == "discharging"
        except (OSError, ValueError):
            continue
    return -1, False


# ------------------------------------------------------------------ planning (pure, testable)
def plan_modes(det: dict, mem_mb: int, batt: Tuple[int, bool], cfg) -> Tuple[List[str], List[str]]:
    """Return (ordered modes to try, notes as 'CODE: text')."""
    modes: List[str] = []
    notes: List[str] = []
    pct, discharging = batt
    if pct >= 0 and discharging and pct <= int(cfg["low_battery_pct"]):
        notes.append("A9BT: battery %d%% - portal still allowed, plug in soon" % pct)
    can_nest = bool(det.get("kwin")) and bool(det.get("display") or det.get("gamescope_wayland"))
    if not det.get("kwin"):
        notes.append("A1KW: kwin_wayland missing - nested modes skipped")
    elif not can_nest:
        notes.append("ACDP: no display to nest into - nested modes skipped")
    if can_nest:
        if not det.get("plasmashell"):
            notes.append("A2PS: plasmashell missing - full desktop skipped")
        elif 0 <= mem_mb < int(cfg["min_ram_full_mb"]):
            notes.append("A5RM: %d MB free < %s MB - full desktop skipped" % (mem_mb, cfg["min_ram_full_mb"]))
        else:
            modes.append("full")
        if 0 <= mem_mb < int(cfg["min_ram_lite_mb"]):
            notes.append("A5RM: %d MB free < %s MB - lite skipped" % (mem_mb, cfg["min_ram_lite_mb"]))
        else:
            modes.append("lite")
    if cfg.get("allow_session_switch", "yes").lower() in ("yes", "true", "1"):
        if det.get("session_select"):
            modes.append("switch")
        else:
            notes.append("A7SS: steamos-session-select missing - session switch skipped")
    modes.append("advise")
    return modes, notes


def build_kwin_cmd(det: dict, width: int, height: int, inner: str, backend: str = "") -> List[str]:
    f = det.get("kwin_flags", {})
    cmd: List[str] = []
    if det.get("dbus_run_session"):
        cmd += [det["dbus_run_session"], "--"]          # own D-Bus: can't clash with Steam's session bus
    cmd.append(det["kwin"])
    if not backend:
        backend = (backends(det, {"backend": "auto"}) or ["x11"])[0]
    if backend == "wayland" and det.get("gamescope_wayland"):
        cmd += ["--wayland-display", det["gamescope_wayland"]]   # straight to gamescope: one copy less per frame
    elif det.get("display"):
        cmd += ["--x11-display", det["display"]]         # through gamescope's Xwayland (0.5-0.6.3 default)
    if f.get("socket", True):
        cmd += ["--socket", NESTED_SOCKET]
    if f.get("xwayland"):
        cmd.append("--xwayland")                         # X11 apps inside the nested desktop stay inside it
    if f.get("no_lockscreen", True):
        cmd.append("--no-lockscreen")
    if f.get("width", True):
        cmd += ["--width", str(width), "--height", str(height)]
    if f.get("exit_with_session", True):
        cmd += ["--exit-with-session", inner]
    else:
        cmd.append(inner)                                # older KWin: positional app to start
    return cmd


# ------------------------------------------------------------------ side effects
class PortalLock:
    """One Airlock at a time. Held by the launcher for as long as the portal lives."""

    def __init__(self, path: Optional[Path] = None):
        self.path, self.fh = path or lock_path(), None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.path.open("a")            # "a": a second attempt must not wipe the first one's pid
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise RuntimeError("A6LK")
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(str(os.getpid()))
        self.fh.flush()
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def _pid_alive(pid) -> bool:
    """True if pid exists and looks like one of ours (reused pids of other programs don't count)."""
    try:
        pid = int(pid)
        os.kill(pid, 0)
        cmd = Path("/proc/%d/cmdline" % pid).read_bytes()
    except (TypeError, ValueError, OSError):
        return False
    return b"kwin" in cmd or b"dbus-run-session" in cmd or b"ddg" in cmd or os.environ.get("DDG_FAKE_KWIN") is not None


def run_hooks(mode: str) -> None:
    """Indicator hooks: every executable in a mode-change.d folder gets the new mode as $1."""
    for d in hook_dirs():
        if not d.is_dir():
            continue
        for hook in sorted(d.iterdir()):
            if hook.is_file() and os.access(str(hook), os.X_OK):
                try:
                    subprocess.run([str(hook), mode], timeout=5, check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.SubprocessError) as e:
                    log("AAHK", "%s: %s" % (hook.name, e))


def notify(det: dict, title: str, body: str) -> None:
    if det.get("notify_send"):
        try:
            subprocess.Popen([det["notify_send"], "-a", "Deck Deck Goose", title, body],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


def ask_yes_no(det: dict, question: str) -> bool:
    """Shown inside gamescope (dialogs work through Xwayland). No dialog tool, or no answer in
    90 s, counts as No, so nothing ever happens to your game by accident."""
    cmd = None
    if det.get("kdialog"):
        cmd = [det["kdialog"], "--title", "Deck Deck Goose", "--yesno", question]
    elif det.get("zenity"):
        cmd = [det["zenity"], "--question", "--title=Deck Deck Goose", "--text=%s" % question]
    if not cmd:
        return False
    try:
        return subprocess.run(cmd, timeout=90).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def tell(det: dict, text: str) -> None:
    try:
        if det.get("kdialog"):
            subprocess.run([det["kdialog"], "--title", "Deck Deck Goose", "--msgbox", text], timeout=120)
        elif det.get("zenity"):
            subprocess.run([det["zenity"], "--info", "--title=Deck Deck Goose", "--text=%s" % text], timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        pass
    print(text)


def backends(det: dict, cfg) -> List[str]:
    """Which ways to reach gamescope, best first. Wayland skips the X11 copy (smoother); X11 is the proven one."""
    want = (cfg.get("backend") or "auto").strip().lower()
    f = det.get("kwin_flags", {})
    can = []
    if det.get("gamescope_wayland") and f.get("wayland_display", True):
        can.append("wayland")
    if det.get("display") and f.get("x11_display", True):
        can.append("x11")
    if want in can:
        return [want] + [b for b in can if b != want]
    try:                                    # whichever worked last time goes first
        last = (state_dir() / "backend").read_text().strip()
        if last in can:
            can.remove(last)
            can.insert(0, last)
    except OSError:
        pass
    return can


def panel_size() -> Tuple[int, int]:
    """The built-in screen's real size, from the kernel (gamescope's Xwayland can report a bigger virtual one)."""
    drm = Path(os.environ.get("DDG_DRM_DIR") or "/sys/class/drm")
    found = []
    try:
        for d in sorted(drm.iterdir()):
            try:
                if (d / "status").read_text().strip() != "connected":
                    continue
                mode = (d / "modes").read_text().split()[0]
                w, h = (int(x) for x in mode.split("x")[:2])
                found.append((0 if "eDP" in d.name or "DSI" in d.name else 1, w, h))
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        pass
    if found:
        found.sort()
        return found[0][1], found[0][2]
    return 0, 0


def screen_size(cfg) -> Tuple[int, int]:
    try:
        if cfg.get("width") and cfg.get("height"):
            return int(cfg["width"]), int(cfg["height"])
    except ValueError:
        pass
    res = (cfg.get("resolution") or "auto").strip().lower()
    if "x" in res:
        try:
            w, h = (int(x) for x in res.split("x"))
            return w, h
        except ValueError:
            pass
    w, h = panel_size()
    if not w:
        w, h = _xrandr_size()
    if res != "native" and w > 1280:        # auto: about 720p - icons 1.5x bigger on a 1080p handheld, far fewer pixels
        h = int(round(h * 1280 / w / 2)) * 2
        w = 1280
    return w, h


def _xrandr_size() -> Tuple[int, int]:
    xr = shutil.which("xrandr")
    if xr:
        try:
            out = subprocess.run([xr, "--current"], capture_output=True, text=True, timeout=5).stdout
            for tok in out.split(","):
                if "current" in tok:
                    w, h = tok.replace("current", "").split("x")
                    return int(w), int(h)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return 1920, 1080       # ROG Ally native


def _kill_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=4)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _tail(path: Path, n: int = 12) -> str:
    try:
        return " | ".join(path.read_text(errors="replace").strip().splitlines()[-n:])[-900:]
    except OSError:
        return ""


# ------------------------------------------------------------------ strays: desktop programs that escape the nested window
# Seen on a real ROG Ally (0.5.0): the full rung counted as failed, but a Plasma shell + window manager it had started
# kept running on Game Mode's own display, outside Airlock's process group, so "Exit game" could not close it.
# So: note what desktop programs exist before Airlock starts, and close anything new that is still there between
# rungs and when Airlock ends. In Game Mode no Plasma shell ever belongs there, so those are treated as strays too.
STRAY_NAMES = ("plasmashell", "kwin_wayland", "kwin_x11", "ksmserver", "kded6", "kded5", "krunner",
               "plasma_session", "startplasma-way", "startplasma-x11", "xembedsniproxy", "gmenudbusmenupr",
               "kactivitymanage", "dolphin", "konsole")
GAME_MODE_NEVER = ("plasmashell", "kwin_x11", "ksmserver", "krunner", "plasma_session", "startplasma-way",
                   "startplasma-x11")
STRAY_UNITS = ("plasma-plasmashell.service", "plasma-kwin_wayland.service", "plasma-kwin_x11.service",
               "plasma-ksmserver.service", "plasma-kded6.service", "plasma-krunner.service")


def desktop_procs() -> Dict[int, str]:
    """{pid: name} of this user's desktop programs (from /proc; DDG_FAKE_PROCS=<json file> in tests)."""
    fake = os.environ.get("DDG_FAKE_PROCS")
    if fake:
        try:
            return {int(k): v for k, v in json.loads(Path(fake).read_text()).items() if v in STRAY_NAMES}
        except (OSError, ValueError):
            return {}
    out: Dict[int, str] = {}
    uid = os.getuid()
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            if d.stat().st_uid != uid:
                continue
            name = (d / "comm").read_text().strip()
        except OSError:
            continue
        if name in STRAY_NAMES:
            out[int(d.name)] = name
    return out


def _systemctl() -> Optional[str]:
    return os.environ.get("DDG_SYSTEMCTL") or shutil.which("systemctl")


def active_units() -> List[str]:
    sc = _systemctl()
    if not sc:
        return []
    try:
        p = subprocess.run([sc, "--user", "is-active"] + list(STRAY_UNITS), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    states = p.stdout.split()
    return [u for u, st in zip(STRAY_UNITS, states) if st in ("active", "activating", "reloading")]


def snapshot() -> dict:
    return {"pids": sorted(desktop_procs()), "units": active_units()}


def _kill_pids(pids: List[int]) -> None:
    if os.environ.get("DDG_FAKE_PROCS"):          # tests: drop them from the fake process table
        f = Path(os.environ["DDG_FAKE_PROCS"])
        try:
            table = json.loads(f.read_text())
            for p in pids:
                table.pop(str(p), None)
            f.write_text(json.dumps(table))
        except (OSError, ValueError):
            pass
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        left = [p for p in pids if _pid_alive(p)]
        for p in left:
            try:
                os.kill(p, sig)
            except (ProcessLookupError, PermissionError):
                pass
        if not left:
            return
        for _ in range(12):
            if not any(_pid_alive(p) for p in left):
                return
            time.sleep(0.25)


def cleanup_strays(before: Optional[dict], game_mode: bool) -> List[str]:
    """Close desktop programs Airlock left behind. Returns what was closed ('name pid' / unit names)."""
    before = before or {"pids": [], "units": []}
    old = set(before.get("pids", []))
    procs = desktop_procs()
    victims = {p: n for p, n in procs.items() if p not in old}
    if game_mode:                                   # these never belong in Game Mode, whoever started them
        victims.update({p: n for p, n in procs.items() if n in GAME_MODE_NEVER})
    closed = []
    units = [u for u in active_units() if game_mode or u not in before.get("units", [])]
    sc = _systemctl()
    if units and sc:
        try:
            subprocess.run([sc, "--user", "stop"] + units, capture_output=True, timeout=20)
            closed += units
        except (OSError, subprocess.TimeoutExpired):
            pass
    _kill_pids(sorted(victims))
    closed += ["%s %d" % (n, p) for p, n in sorted(victims.items())]
    if closed:
        log("ADST", ", ".join(closed)[:600])
    return closed


def cleanup() -> dict:
    """`ddg airlock cleanup` / the Airlock tab button: closes leftovers when no Airlock is open."""
    st = read_state()
    if st.get("status") == "open" and _pid_alive(st.get("pid")):
        return {"code": "A6LK", "closed": [], "message": "An Airlock is open: close it first (Back to Game)."}
    game = detect_session() == "game"
    before = st.get("before")
    if before is None:          # no record of what was there before: only Game Mode's never-allowed ones
        before = snapshot()
    closed = cleanup_strays(before, game)
    return {"code": "A0OK", "closed": closed,
            "message": ("Closed: " + ", ".join(closed)) if closed else "Nothing left over."}


def launch_nested(kind: str, det: dict, cfg) -> str:
    """Run a nested session and block until it closes. Returns an ErrCode.
    Tries each way of reaching gamescope (Wayland, then X11) before giving up on this mode."""
    order = backends(det, cfg) or ["x11"]
    code = "A4EX"
    for i, backend in enumerate(order):
        code = _launch_once(kind, det, cfg, backend, first=(i == 0))
        if code == "A0OK":
            try:
                (state_dir() / "backend").write_text(backend)
            except OSError:
                pass
            return code
        if i + 1 < len(order):
            cleanup_strays(read_state().get("before"), det.get("session") == "game")
            log(code, "%s via %s failed, trying %s" % (kind, backend, order[i + 1]))
    return code


def _drawing(out: Path) -> str:
    """'software' when KWin fell back to CPU drawing (slow, ~20 fps), else 'gpu' / ''."""
    try:
        text = out.read_text(errors="replace").lower()
    except OSError:
        return ""
    if "qpainter" in text or "llvmpipe" in text or "softpipe" in text:
        return "software"
    return "gpu" if ("opengl" in text or "egl" in text) else ""


def _launch_once(kind: str, det: dict, cfg, backend: str, first: bool = True) -> str:
    ready_path().unlink(missing_ok=True)
    w, h = screen_size(cfg)
    inner = str(DATA / "nested-session.sh")
    cmd = build_kwin_cmd(det, w, h, inner, backend)
    env = dict(os.environ, DDG_NESTED_KIND=kind, DDG_LITE_APPS=cfg["lite_apps"], DDG_READY=str(ready_path()),
               QT_LOGGING_RULES="kwin_scene_opengl.info=true;kwin_core.info=true")
    env.pop("THISPC_GAMEMODE", None)        # the window opened inside is a normal desktop one
    touch_on = det.get("session") == "game" and bool(det.get("display")) and str(cfg.get("touch_button", "on")).lower() != "off"
    if touch_on:
        import sys as _sys
        env.update(DDG_TOUCH_BUTTON="1", DDG_OUTER_DISPLAY=det.get("display", ""), DDG_PY=_sys.executable,
                   DDG_SRC=str(Path(__file__).resolve().parent.parent))
    out = state_dir() / ("nested-%s.log" % kind)
    state_dir().mkdir(parents=True, exist_ok=True)
    log("A0OK", "starting %s via %s at %dx%d: %s" % (kind, backend, w, h, " ".join(cmd)))
    with out.open("w" if first else "a") as outfh:
        outfh.write("=== %s via %s, %dx%d, %s\n" % (kind, backend, w, h, time.strftime("%Y-%m-%d %H:%M:%S")))
        outfh.flush()
        proc = subprocess.Popen(cmd, env=env, start_new_session=True, stdout=outfh, stderr=subprocess.STDOUT)

    import threading
    main = threading.current_thread() is threading.main_thread()      # handlers can only be set there
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)} if main else {}

    bridge = None
    touch = None

    def _bail(signum, _frame):              # Steam's "Exit game" sends SIGTERM to us
        if bridge:
            bridge.stop()
        if touch:
            touch.end()                     # Steam's own touch mode back before anything else

        _kill_group(proc)
        cleanup_strays(read_state().get("before"), det.get("session") == "game")
        ready_path().unlink(missing_ok=True)
        write_state(kind, "closed", reason="signal %d" % signum)
        run_hooks("game")
        raise SystemExit(0)
    for s in old:
        signal.signal(s, _bail)
    try:
        deadline = time.time() + int(cfg["watchdog_seconds"])
        while time.time() < deadline:
            if ready_path().exists():       # checked first: opened-then-closed-quickly is a success
                break
            if proc.poll() is not None:
                log("A4EX", "%s via %s exit code %s: %s" % (kind, backend, proc.returncode, _tail(out)))
                return "A4EX"
            time.sleep(0.25)
        else:
            log("A3TO", "%s via %s: %s" % (kind, backend, _tail(out)))
            _kill_group(proc)
            return "A3TO"
        pad_note = "off in settings"
        if str(cfg.get("pad", "on")).lower() != "off":
            try:
                bridge, pad_note = padbridge.start(str(cfg.get("pad_speed", "normal")))
            except Exception as e:                  # the pad is a bonus: never stop the desktop opening
                bridge, pad_note = None, "error: %s" % e
            if bridge is None:
                log("AEPD", pad_note)
        touch_note = "off in settings" if str(cfg.get("touch_button", "on")).lower() == "off" else \
            ("not in Game Mode" if not touch_on else "")
        if touch_on:
            t = str(cfg.get("touch", "steam"))
            try:
                touch = touchmode.Session(det["display"], int(t) if t.isdigit() else None, log=lambda m: log("A0OK", m))
                touch_note = "button on (%s)" % touchmode.label(touch.refresh(force=True) if touch.ok else None) if touch.ok \
                    else "gamescope's display not reachable"
                if not touch.ok:
                    touch = None
            except Exception as e:                  # a bonus too: never stop the desktop opening
                touch, touch_note = None, "error: %s" % e
        write_state(kind, "open", pid=proc.pid, backend=backend, size="%dx%d" % (w, h), pad=pad_note, touch=touch_note,
                    before=read_state().get("before"))
        run_hooks("portal")
        notify(det, "Airlock open - game still running",
               "Back to game: Steam button > Exit game, or open Back to Game in the menu.")
        while proc.poll() is None:              # 5 checks a second for the touch button; cheap
            if touch:
                picked = touch.poll()
                if picked is not None:
                    _save_touch(picked)
            time.sleep(0.2)
        if touch:
            touch.end()
            touch = None
        if bridge:
            bridge.stop()
            bridge = None
        ready_path().unlink(missing_ok=True)
        drawing = _drawing(out)
        write_state(kind, "closed", backend=backend, size="%dx%d" % (w, h), drawing=drawing, pad=pad_note,
                    touch=touch_note, before=read_state().get("before"))
        if drawing == "software":
            log("A0OK", "note: KWin drew on the CPU (software), which caps it near 20 fps; see nested-%s.log" % kind)
        run_hooks("game")
        return "A0OK"
    finally:
        if touch:
            touch.end()
        if bridge:
            bridge.stop()
        for s, h_ in old.items():
            signal.signal(s, h_ if h_ is not None else signal.SIG_DFL)


def _save_touch(value: int) -> None:
    """Remembers the last picked touch mode for the next Airlock (only Airlock uses it)."""
    try:
        set_options(touch=str(value))
    except Exception:  # noqa: BLE001
        pass


def set_options(resolution: Optional[str] = None, backend: Optional[str] = None,
                pad: Optional[str] = None, pad_speed: Optional[str] = None,
                touch_button: Optional[str] = None, touch: Optional[str] = None) -> dict:
    """Saves the Airlock tab's choices into portal.ini (other settings in it are kept)."""
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(conf_path())
    except configparser.Error:
        pass
    if "portal" not in cp:
        cp["portal"] = {}
    if resolution is not None:
        r = resolution.strip().lower()
        if r not in ("auto", "native") and not re.fullmatch(r"\d{3,4}x\d{3,4}", r):
            return {"error": "resolution must be auto, native or WIDTHxHEIGHT"}
        cp["portal"]["resolution"] = r
        cp["portal"]["width"] = ""
        cp["portal"]["height"] = ""
    if backend is not None:
        if backend not in ("auto", "wayland", "x11"):
            return {"error": "connection must be auto, wayland or x11"}
        cp["portal"]["backend"] = backend
    if pad is not None:
        if pad not in ("on", "off"):
            return {"error": "controller must be on or off"}
        cp["portal"]["pad"] = pad
    if pad_speed is not None:
        if pad_speed not in padbridge.SPEEDS:
            return {"error": "pointer speed must be slow, normal or fast"}
        cp["portal"]["pad_speed"] = pad_speed
    if touch_button is not None:
        if touch_button not in ("on", "off"):
            return {"error": "touch button must be on or off"}
        cp["portal"]["touch_button"] = touch_button
    if touch is not None:
        if touch != "steam" and not (touch.isdigit() and int(touch) in touchmode.MODES):
            return {"error": "touch must be steam or a mode number"}
        cp["portal"]["touch"] = touch
    conf_path().parent.mkdir(parents=True, exist_ok=True)
    with conf_path().open("w") as fh:
        cp.write(fh)
    c = load_config()
    return {"resolution": c.get("resolution"), "backend": c.get("backend"), "pad": c.get("pad"),
            "pad_speed": c.get("pad_speed"), "touch_button": c.get("touch_button"), "touch": c.get("touch")}


def switch_session(det: dict, target: str) -> str:
    """target: 'plasma' (Desktop Mode) or 'gamescope' (Game Mode)."""
    if not det.get("session_select"):
        log("A7SS")
        return "A7SS"
    write_state("desktop" if target == "plasma" else "game", "switching")
    run_hooks("desktop" if target == "plasma" else "game")
    subprocess.Popen([det["session_select"], target])
    return "A0OK"


def start(force: bool = False, det: Optional[dict] = None) -> dict:
    """What the Airlock tile runs. force=True also allows it from Desktop Mode (to try it out)."""
    cfg = load_config()
    det = det or detect()
    if det["session"] != "game" and not force:
        log("A8NG", det["session"])
        where = "already on the desktop" if det["session"] == "desktop" else "in a mode it couldn't recognise"
        return {"code": "A8NG", "message": "Airlock opens from Game Mode (you're %s). "
                "To try it from here anyway: ddg airlock start --force" % where}
    try:
        with PortalLock():
            game_mode = det["session"] == "game"
            if game_mode:                           # leftovers from an earlier run: clear them first
                cleanup_strays(snapshot(), True)
            before = snapshot()
            write_state("game" if game_mode else "desktop", "starting", before=before)
            modes, notes = plan_modes(det, mem_available_mb(), battery(), cfg)
            for n in notes:
                log(n[:4], n[6:])
            tried: List[str] = []
            for mode in modes:
                tried.append(mode)
                if mode in ("full", "lite"):
                    code = launch_nested(mode, det, cfg)
                    closed = cleanup_strays(before, game_mode)     # after closing AND before the next rung
                    if closed:
                        notes.append("ADST: closed leftovers: " + ", ".join(closed))
                    if code == "A0OK":
                        return {"code": code, "mode": mode, "tried": tried, "notes": notes}
                elif mode == "switch":
                    if ask_yes_no(det, "The nested desktop couldn't open.\n\nSwitch to full Desktop Mode instead?\n"
                                       "Your game WILL close."):
                        return {"code": switch_session(det, "plasma"), "mode": "switch", "tried": tried,
                                "notes": notes}
                    log("ABNO")
                    notes.append("ABNO: session switch declined")
                else:        # advise: last resort, never reboots
                    write_state("game", "failed", tried=tried, notes=notes)
                    tell(det, "Airlock couldn't open a desktop.\n\nYou can still use Steam button > Power > "
                              "Switch to Desktop, or restart if that fails.\n\nLog: %s" % log_file())
                    cleanup_strays(before, game_mode)
                    return {"code": "A3TO", "mode": "advise", "tried": tried, "notes": notes}
    except RuntimeError:
        log("A6LK")
        return {"code": "A6LK", "message": "A portal is already open."}
    return {"code": "A3TO"}


def stop() -> dict:
    """Close an open Airlock (the Back to Game button). Safe to call when nothing is open."""
    st = read_state()
    pid = st.get("pid")
    write_state(st.get("mode", "game"), "closed", reason="stop")      # first: we may be inside the group
    if st.get("status") == "open" and pid and _pid_alive(pid):
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        for _ in range(16):
            if not _pid_alive(pid):
                break
            time.sleep(0.25)
    closed = cleanup_strays(st.get("before"), detect_session() == "game")
    return {"code": "A0OK", "closed": closed}


def status() -> dict:
    st = read_state()
    session = detect_session()
    open_now = st.get("status") == "open" and _pid_alive(st.get("pid"))
    key = "portal" if open_now else session if session in INDICATORS else "unknown"
    cfg = load_config()
    return {"version": __version__, "build": __build_date__, "session": session, "portal_open": open_now,
            "last": st, "indicator": INDICATORS[key],
            "options": {"resolution": cfg.get("resolution", "auto"), "backend": cfg.get("backend", "auto"),
                        "size": "%dx%d" % screen_size(cfg), "panel": "%dx%d" % panel_size(),
                        "pad": cfg.get("pad", "on"), "pad_speed": cfg.get("pad_speed", "normal"),
                        "touch_button": cfg.get("touch_button", "on"), "touch": cfg.get("touch", "steam")},
            "touch_label": touchmode.label(int(cfg["touch"])) if str(cfg.get("touch", "")).isdigit() else "Steam's choice",
            "pad": padbridge.check()}


def plan() -> dict:
    """Dry run: what would be tried, and why. Launches nothing."""
    det = detect()
    modes, notes = plan_modes(det, mem_available_mb(), battery(), load_config())
    return {"session": det["session"], "modes": modes, "notes": notes,
            "have": {k: bool(det.get(k)) for k in ("kwin", "plasmashell", "dbus_run_session", "session_select",
                                                   "kdialog", "zenity", "notify_send")}}


# ------------------------------------------------------------------ install helpers (run from Desktop Mode)
def back_launcher_path() -> Path:
    return core.home() / ".local" / "share" / "applications" / BACK_LAUNCHER


def write_back_launcher() -> Step:
    """A "Back to Game" entry for the nested desktop's app menu: a way out that needs no keyboard."""
    p = back_launcher_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lib = core.data_dir() / "lib"
    p.write_text("[Desktop Entry]\nType=Application\nName=Back to Game (close Airlock)\n"
                 "Comment=Closes the Airlock desktop and returns to your game (Deck Deck Goose)\n"
                 "Exec=env PYTHONPATH=%s python3 -m ddg airlock stop\nIcon=go-previous\nTerminal=false\n"
                 "Categories=System;\n" % lib)
    return Step("Back to Game menu entry", p.exists(), str(p))


def remove_back_launcher() -> None:
    try:
        back_launcher_path().unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ optional sleep hook (needs a password once)
SLEEP_UNIT_NAME = "ddg-airlock-sleep.service"
SLEEP_UNIT = """# Deck Deck Goose: close Airlock before sleep (installed by `ddg airlock sleep-hook on`).
# systemd only runs hook scripts from its compiled-in system-sleep folder, never /etc/systemd/system-sleep,
# so this hangs a unit on sleep.target instead (the same trick Zombie-Deck uses).
[Unit]
Description=Deck Deck Goose: close Airlock before sleep
Before=sleep.target
StopWhenUnneeded=yes

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=-%(script)s pre
TimeoutStartSec=8

[Install]
WantedBy=sleep.target
"""


def sleep_dir() -> Path:
    """Where the hook script goes when Zombie-Deck is installed: its own sleep.d folder."""
    return Path(os.environ.get("DDG_ZOMBIE_SLEEPD") or "/etc/zombie-deck/sleep.d")


def unit_dir() -> Path:
    return Path(os.environ.get("DDG_UNIT_DIR") or "/etc/systemd/system")


def libexec_dir() -> Path:
    return Path(os.environ.get("DDG_LIBEXEC") or "/usr/local/libexec/deck-deck-goose")


def sleep_hook_installed() -> bool:
    return (sleep_dir() / SLEEP_HOOK_NAME).exists() or (unit_dir() / SLEEP_UNIT_NAME).exists()


def sleep_hook_how() -> str:
    """'zombie-deck' (drop-in), 'unit' (own sleep.target unit) or '' (not installed)."""
    if (sleep_dir() / SLEEP_HOOK_NAME).exists():
        return "zombie-deck"
    return "unit" if (unit_dir() / SLEEP_UNIT_NAME).exists() else ""


def _zombie_installed() -> bool:
    from . import zombie
    return zombie.binary() is not None and sleep_dir().parent.exists()


def _privileged(cmd: List[str]) -> Tuple[int, str]:
    """Runs a command as root through pkexec, which shows KDE's normal password window."""
    if os.environ.get("DDG_FAKE_PKEXEC"):          # tests: do it as the current user
        pre: List[str] = []
    else:
        pre = ["pkexec"]
    try:
        p = subprocess.run(pre + cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return 127, "pkexec is not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out waiting for the password window"
    return p.returncode, (p.stdout + p.stderr).strip()


def sleep_hook(on: bool) -> List[Step]:
    """Closes an open Airlock before suspend/hibernate, so you wake up in plain Game Mode.

    With Zombie-Deck installed the script goes into its /etc/zombie-deck/sleep.d/ (root, 5 s limit).
    Without it, a small unit on sleep.target does the same. (/etc/systemd/system-sleep is never read.)"""
    name = "Sleep hook"
    if gamemode.in_game_mode():
        return [Step(name, False, "Do this from Desktop Mode: the password window can't appear in Game Mode.")]
    fake = bool(os.environ.get("DDG_FAKE_PKEXEC"))
    script = DATA / "ddg-airlock-sleep.sh"
    if on:
        if _zombie_installed():
            dest = sleep_dir() / SLEEP_HOOK_NAME
            rc, out = _privileged(["install", "-D", "-m755", str(script), str(dest)])
            how = "in Zombie-Deck's sleep.d folder"
        else:
            lib = libexec_dir() / "ddg-airlock-sleep.sh"
            unit = unit_dir() / SLEEP_UNIT_NAME
            tmp = state_dir() / SLEEP_UNIT_NAME
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(SLEEP_UNIT % {"script": lib})
            sysctl = "" if fake else " && systemctl daemon-reload && systemctl enable %s" % shlex.quote(SLEEP_UNIT_NAME)
            rc, out = _privileged(["sh", "-c", "install -D -m755 %s %s && install -D -m644 %s %s%s" % (
                shlex.quote(str(script)), shlex.quote(str(lib)), shlex.quote(str(tmp)), shlex.quote(str(unit)), sysctl)])
            how = "as a small sleep.target unit (install Zombie-Deck and it will use that instead)"
    else:
        sysctl = "" if fake else "; systemctl disable %s >/dev/null 2>&1; systemctl daemon-reload" % shlex.quote(SLEEP_UNIT_NAME)
        rc, out = _privileged(["sh", "-c", "rm -f %s %s %s %s%s" % (
            shlex.quote(str(sleep_dir() / SLEEP_HOOK_NAME)), "/etc/systemd/system-sleep/" + SLEEP_HOOK_NAME,
            shlex.quote(str(unit_dir() / SLEEP_UNIT_NAME)),
            shlex.quote(str(libexec_dir() / "ddg-airlock-sleep.sh")), sysctl)])
        how = ""
    if rc in (126, 127):
        return [Step(name, False, "Not changed: %s." % ("the password window was cancelled" if rc == 126 else out))]
    ok = rc == 0 and (sleep_hook_installed() == on)
    return [Step(name, ok, ("installed " + how if on else "removed") if ok else (out or "could not be changed"))]


# ------------------------------------------------------------------ probe
def probe(live: bool = False) -> Tuple[bool, str]:
    """Runs the read-only hardware/OS check (and, with live=True, a 10-second nested test window)."""
    bash = shutil.which("bash")
    if not bash:
        return False, "bash was not found, so the check can't run."
    try:
        p = subprocess.run([bash, str(DATA / "probe.sh")] + (["--live"] if live else []),
                           capture_output=True, text=True, timeout=60, env=dict(os.environ, HOME=str(core.home())))
    except subprocess.TimeoutExpired:
        return False, "The check took longer than a minute and was stopped."
    return p.returncode == 0, p.stdout + p.stderr


def spawn_start(force: bool = False) -> None:
    """Start Airlock without blocking the window's server (the launcher blocks until it closes)."""
    import sys
    cmd = [sys.executable, "-m", "ddg", "airlock", "start"] + (["--force"] if force else [])
    src = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ, PYTHONPATH=src + os.pathsep + os.environ.get("PYTHONPATH", ""))
    state_dir().mkdir(parents=True, exist_ok=True)
    with (state_dir() / "spawn.log").open("a") as fh:
        subprocess.Popen(cmd, env=env, start_new_session=True, stdin=subprocess.DEVNULL, stdout=fh, stderr=fh)
