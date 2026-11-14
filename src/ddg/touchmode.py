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
from typing import Optional

ATOM_NAME = b"STEAM_TOUCH_CLICK_MODE"
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

    def get(self) -> Optional[int]:
        actual, fmt, n, left, data = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        r = self.lib.XGetWindowProperty(self.dpy, self.root, self.atom, 0, 1, 0, XA_CARDINAL, ctypes.byref(actual),
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
    return _dir() / "touch-current"


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
    """Lives as long as one Airlock: saves Steam's value, applies requests from the button, restores at the end."""

    def __init__(self, display: str, start_mode: Optional[int] = None, log=None):
        self.display, self.log = display, (log or (lambda *_: None))
        self.root: Optional[Root] = None
        self.last_refresh = 0.0
        try:
            self.root = Root(display)
        except OSError as e:
            self.log("cannot reach gamescope's X display: %s" % e)
            return
        repair_stale(skip_running_check=True)          # an older Airlock may have been killed before restoring
        orig = self.root.get()
        _write(restore_file(), json.dumps({"display": display, "orig": orig, "pid": os.getpid(), "time": time.time()}))
        for f in (request_file(),):
            try:
                f.unlink()
            except OSError:
                pass
        if start_mode in MODES:
            self.root.set(start_mode)
        self.refresh(force=True)

    @property
    def ok(self) -> bool:
        return self.root is not None

    def refresh(self, force: bool = False) -> Optional[int]:
        """Publishes the real value for the button (Steam may change it, e.g. when its menu opens)."""
        if not self.root or (not force and time.time() - self.last_refresh < 1.0):
            return None
        self.last_refresh = time.time()
        v = self.root.get()
        try:
            _write(current_file(), "" if v is None else str(v))
        except OSError:
            pass
        return v

    def poll(self) -> Optional[int]:
        """Call often (cheap): applies a pending request from the button. Returns the mode it applied."""
        if not self.root:
            return None
        try:
            want = request_file().read_text().strip()
            request_file().unlink()
        except OSError:
            self.refresh()
            return None
        if not want.isdigit() or int(want) not in MODES:
            return None
        self.root.set(int(want))
        self.refresh(force=True)
        self.log("touch mode -> %s" % label(int(want)))
        return int(want)

    def end(self) -> None:
        """Puts Steam's value back. Safe to call twice."""
        if not self.root:
            return
        restore(self.root)
        self.root.close()
        self.root = None
        for f in (current_file(), request_file()):
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
