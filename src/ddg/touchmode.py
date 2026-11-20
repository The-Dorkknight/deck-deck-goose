# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Airlock touch modes: how gamescope turns a finger on the screen into input.

Gamescope (the Game Mode compositor) reads one number, the CARDINAL property STEAM_TOUCH_CLICK_MODE on the
root window of its Xwayland, and Steam normally sets it (see gamescope src/backend.h, src/wlserver.cpp):
    0 hover, 1 left (mouse), 2 right, 3 middle, 4 passthrough (real touch), 5 disabled, 6 trackpad.

Airlock lets you cycle a few of these with a small button inside the Airlock desktop. It never follows you out:
* when Airlock opens, Steam's own value is saved to a file first;
* when Airlock closes (normally, by signal, or by "Exit game"), that value is put back;
* if Airlock was killed so hard that it could not, the next Airlock start or Deck Deck Goose start puts it back.
Only inside Game Mode (gamescope); in Desktop Mode there is no such property and no button.

Standard library only: libX11 through ctypes.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

ATOM_NAME = b"STEAM_TOUCH_CLICK_MODE"
FOCUS_ATOM_NAME = b"GAMESCOPE_FOCUSED_APP"      # gamescope src/steamcompmgr.cpp: app id of the input-focus window
STEAM_UI_APPID = 769                             # Steam's own UI (menus, Quick Access, overlay)
XA_CARDINAL = 6
PROP_MODE_REPLACE = 0

#            value: (short name, letter on the button, one-line explanation)
MODES = {1: ("Mouse", "M", "Tap clicks, drag works"),
         4: ("Native touch", "T", "Hold for right-click"),
         6: ("Trackpad", "P", "Slide to move, tap clicks"),
         2: ("Right-click", "R", "Every tap right-clicks"),
         0: ("Hover", "H", "Taps only move the pointer"),
         5: ("Off", "X", "Touchscreen ignored")}
CYCLE = (1, 4, 6, 2)          # what the button steps through
GAMESCOPE_DEFAULT = 1         # gamescope's own default when the property was never set


# ------------------------------------------------------------------ libX11 (tiny subset)
_x11 = None


def x11():
    global _x11
    if _x11 is None:
        name = ctypes.util.find_library("X11") or "libX11.so.6"
        lib = ctypes.CDLL(name)
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XGetWindowProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
                                           ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                           ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
                                           ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
        lib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        _x11 = lib
    return _x11


class Root:
    """The root window of one X display (gamescope's Xwayland)."""

    def __init__(self, display: str):
        self.lib = x11()
        self.dpy = self.lib.XOpenDisplay(display.encode() if display else None)
        if not self.dpy:
            raise OSError("cannot open X display %r" % display)
        self.root = self.lib.XDefaultRootWindow(self.dpy)
        self.atom = self.lib.XInternAtom(self.dpy, ATOM_NAME, 0)
        self.focus_atom = self.lib.XInternAtom(self.dpy, FOCUS_ATOM_NAME, 0)

    def focused_app(self) -> Optional[int]:
        """gamescope's GAMESCOPE_FOCUSED_APP: the app id with input focus (Steam's own menus and overlay: 769)."""
        return self.get(self.focus_atom)

    def get(self, atom: Optional[int] = None) -> Optional[int]:
        actual, fmt, n, left, data = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        r = self.lib.XGetWindowProperty(self.dpy, self.root, atom or self.atom, 0, 1, 0, XA_CARDINAL, ctypes.byref(actual),
                                        ctypes.byref(fmt), ctypes.byref(n), ctypes.byref(left), ctypes.byref(data))
        try:
            if r != 0 or not data.value or n.value < 1 or fmt.value != 32:
                return None
            return int(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0] & 0xFFFFFFFF)   # format 32 = C long
        finally:
            if data.value:
                self.lib.XFree(data)

    def set(self, value: int) -> None:
        v = (ctypes.c_ulong * 1)(int(value))
        self.lib.XChangeProperty(self.dpy, self.root, self.atom, XA_CARDINAL, 32, PROP_MODE_REPLACE, v, 1)
        self.lib.XSync(self.dpy, 0)

    def close(self) -> None:
        if self.dpy:
            self.lib.XCloseDisplay(self.dpy)
            self.dpy = None


# ------------------------------------------------------------------ files shared with the button
def _dir() -> Path:
    from . import airlock
    return airlock.state_dir()


def restore_file() -> Path:
    return _dir() / "touch-restore.json"


def request_file() -> Path:
    return _dir() / "touch-request"


def current_file() -> Path:
    """For the button: '<mode> <L|U> <F|B>' = mode number (or '-'), Locked/Unlocked, Airlock in Front / in the Background."""
    return _dir() / "touch-current"


def message_file() -> Path:
    """For the button: '<n>\t<title>\t<line>' - a popup to show (gestures, refusals)."""
    return _dir() / "touch-message"


def parse_current(text: str) -> dict:
    parts = (text or "").split()
    mode = int(parts[0]) if parts and parts[0].isdigit() else None
    return {"mode": mode, "locked": len(parts) > 1 and parts[1] == "L", "front": not (len(parts) > 2 and parts[2] == "B")}


def label(value: Optional[int]) -> str:
    if value is None:
        return "Steam default"
    return MODES.get(value, ("Mode %d" % value, "?", ""))[0]


def next_mode(value: Optional[int]) -> int:
    try:
        return CYCLE[(CYCLE.index(value) + 1) % len(CYCLE)]
    except ValueError:
        return CYCLE[0]


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(str(tmp), str(p))


# ------------------------------------------------------------------ the Airlock supervisor side
class Session:
    """Lives as long as one Airlock.

    * saves Steam's touch value first and puts it back at the end;
    * applies the button's requests and the gestures (four fingers down: lock/unlock; three-finger double tap: next
      mode, only while unlocked);
    * keeps the chosen mode while Airlock is in front: Steam resets it now and then (the "muddled after a few minutes"
      report), so it is put back, and logged;
    * hands control back while Steam's own menu or overlay is in front (GAMESCOPE_FOCUSED_APP), so nothing of
      Airlock's follows you there; on_focus(front) tells the supervisor (it pauses the pad mouse).
    """

    LEARN_S = 10.0           # the first app id seen in front (not Steam's) within this time is Airlock's

    def __init__(self, display: str, start_mode: Optional[int] = None, log=None, locked: bool = True,
                 on_focus: Optional[Callable[[bool], None]] = None, clock: Callable[[], float] = time.monotonic):
        self.display, self.log = display, (log or (lambda *_: None))
        self.on_focus, self.clock = on_focus, clock
        self.root: Optional[Root] = None
        self.last_refresh = -1e9
        self.locked = locked
        self.chosen: Optional[int] = start_mode if start_mode in MODES else None
        self.front = True
        self.ours: Optional[int] = None
        self.t_start = clock()
        self.msg_n = 0
        self.put_back = 0
        try:
            self.root = Root(display)
        except OSError as e:
            self.log("cannot reach gamescope's X display: %s" % e)
            return
        repair_stale(skip_running_check=True)          # an older Airlock may have been killed before restoring
        self.orig = self.root.get()
        _write(restore_file(), json.dumps({"display": display, "orig": self.orig, "pid": os.getpid(), "time": time.time()}))
        for f in (request_file(), message_file()):
            try:
                f.unlink()
            except OSError:
                pass
        if self.chosen is not None:
            self.root.set(self.chosen)
        self.refresh(force=True)

    @property
    def ok(self) -> bool:
        return self.root is not None

    # ---------------------------------------------------------- what the button sees
    def refresh(self, force: bool = False) -> Optional[int]:
        if not self.root or (not force and self.clock() - self.last_refresh < 1.0):
            return None
        self.last_refresh = self.clock()
        v = self.root.get()
        try:
            _write(current_file(), "%s %s %s" % ("-" if v is None else v, "L" if self.locked else "U", "F" if self.front else "B"))
        except OSError:
            pass
        return v

    def say(self, title: str, line: str = "") -> None:
        self.msg_n += 1
        try:
            _write(message_file(), "%d\t%s\t%s" % (self.msg_n, title, line))
        except OSError:
            pass

    # ---------------------------------------------------------- focus
    def _front_now(self) -> bool:
        app = self.root.focused_app()
        if app is None:
            return True                                  # gamescope does not say: assume Airlock
        if self.ours is None:
            if app not in (0, STEAM_UI_APPID) and self.clock() - self.t_start <= self.LEARN_S:
                self.ours = app
            return app != STEAM_UI_APPID
        return app == self.ours

    def _focus(self) -> None:
        f = self._front_now()
        if f == self.front:
            return
        self.front = f
        cur = self.root.get()
        if not f:                                        # Steam's menu in front: give Steam its own touch behaviour
            if self.chosen is not None and cur == self.chosen and self.orig != self.chosen:
                self.root.set(self.orig if self.orig is not None else GAMESCOPE_DEFAULT)
        elif self.chosen is not None and cur != self.chosen:
            self.root.set(self.chosen)
        if self.on_focus:
            try:
                self.on_focus(f)
            except Exception:  # noqa: BLE001
                pass
        self.refresh(force=True)

    def _keep(self) -> None:
        """Steam put its own value back while Airlock is in front: put the chosen one back (the 'muddled' fix)."""
        if not self.front or self.chosen is None:
            return
        cur = self.root.get()
        if cur != self.chosen:
            self.root.set(self.chosen)
            self.put_back += 1
            if self.put_back <= 20 or self.put_back % 50 == 0:
                self.log("touch mode was changed to %s while Airlock was in front; put back %s (%d times)"
                         % (label(cur), label(self.chosen), self.put_back))
            self.refresh(force=True)

    # ---------------------------------------------------------- changes
    def _apply(self, value: int) -> int:
        self.chosen = value
        self.root.set(value)
        self.refresh(force=True)
        self.log("touch mode -> %s" % label(value))
        return value

    def gesture(self, name: str) -> Optional[int]:
        """From the touchscreen watcher (same thread as poll). Returns a newly applied mode, if any."""
        if not self.root or not self.front:
            return None
        if name == "four_down":
            self.locked = not self.locked
            self.refresh(force=True)
            cur = label(self.chosen if self.chosen is not None else self.root.get())
            if self.locked:
                self.say("Locked: " + cur, "Four fingers down to unlock")
            else:
                self.say("Unlocked", "3-finger double tap: next mode")
            self.log("touch lock %s by gesture" % ("on" if self.locked else "off"))
            return None
        if name == "three_double_tap":
            if self.locked:
                self.say("Locked", "Four fingers down to unlock")
                return None
            v = self._apply(next_mode(self.chosen if self.chosen is not None else self.root.get()))
            name_, _, what = MODES[v]
            self.say(name_, what)
            return v
        return None

    def poll(self) -> Optional[int]:
        """Call often (cheap): focus, keeping the mode, and requests from the button. Returns a mode it applied."""
        if not self.root:
            return None
        self._focus()
        self._keep()
        try:
            want = request_file().read_text().strip()
            request_file().unlink()
        except OSError:
            self.refresh()
            return None
        if not want.isdigit() or int(want) not in MODES:
            return None
        if self.locked:
            self.say("Locked", "Four fingers down to unlock")
            self.refresh(force=True)
            return None
        return self._apply(int(want))

    def end(self) -> None:
        """Puts Steam's value back. Safe to call twice."""
        if not self.root:
            return
        restore(self.root)
        self.root.close()
        self.root = None
        for f in (current_file(), request_file(), message_file()):
            try:
                f.unlink()
            except OSError:
                pass


def restore(root: Optional[Root] = None) -> bool:
    try:
        info = json.loads(restore_file().read_text())
    except (OSError, ValueError):
        return False
    own = root is None
    try:
        if own:
            root = Root(str(info.get("display", "")))
        orig = info.get("orig")
        root.set(int(orig) if isinstance(orig, int) else GAMESCOPE_DEFAULT)
    except OSError:
        return False
    finally:
        if own and root:
            root.close()
    try:
        restore_file().unlink()
    except OSError:
        pass
    return True


def repair_stale(skip_running_check: bool = False) -> bool:
    """If an Airlock died without restoring Steam's value, restore it now (only when no Airlock is running)."""
    try:
        info = json.loads(restore_file().read_text())
    except (OSError, ValueError):
        return False
    if not skip_running_check:
        pid = info.get("pid")
        try:
            os.kill(int(pid), 0)
            return False                     # that Airlock is still alive; it will restore itself
        except (TypeError, ValueError, OSError):
            pass
    return restore()
