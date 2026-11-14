# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The touch-mode button in the Airlock desktop's top-left corner.

Runs INSIDE the nested desktop (started by airlock_data/nested-session.sh, on the nested Xwayland's DISPLAY), so it
exists only while Airlock is open and never in the real Desktop Mode or Game Mode. Tapping it asks the Airlock
supervisor (outside, which can reach gamescope) for the next touch mode, then shows a popup saying which mode is on.
Draws its own 5x7 pixel font with filled rectangles: no fonts, toolkits or packages needed, only libX11.

Run by hand for a look:  DDG_TOUCH_DEMO=1 python3 -m ddg.touchbutton   (cycles locally, changes nothing)
"""
from __future__ import annotations

import ctypes
import os
import select
import sys
import time
from typing import List, Optional

from . import touchmode

# 5x7 font, one string of 7 rows x 5 columns per character ('#' = pixel)
_F = {
    "A": " ### " "#   #" "#   #" "#####" "#   #" "#   #" "#   #", "B": "#### #   ##   ##### #   ##   ##### ", "C": " ### #   ##    #    #    #   # ### ",
    "D": "#### #   ##   ##   ##   ##   ##### ", "E": "######    #    #### #    #    #####", "F": "######    #    #### #    #    #    ",
    "G": " ### #   ##    # ####   ##   # ####", "H": "#   ##   ##   #######   ##   ##   #", "I": " ###   #    #    #    #    #   ### ",
    "J": "  ###" "   # " "   # " "   # " "#  # " "#  # " " ##  ", "K": "#   ##  # # #  ##   # #  #  # #   #", "L": "#    #    #    #    #    #    #####",
    "M": "#   ### ### # ##   ##   ##   ##   #", "N": "#   ###  ## # ##  ###   ##   ##   #", "O": " ### #   ##   ##   ##   ##   # ### ",
    "P": "#### #   ##   ##### #    #    #    ", "Q": " ### #   ##   ##   ## # ##  #  ## #", "R": "#### #   ##   ##### # #  #  # #   #",
    "S": " ####" "#    " "#    " " ### " "    #" "    #" "#### ", "T": "#####  #    #    #    #    #    #  ", "U": "#   ##   ##   ##   ##   ##   # ### ",
    "V": "#   ##   ##   ##   ##   # # #   #  ", "W": "#   ##   ##   ## # ## # ### ###   #", "X": "#   ##   # # #   #   # # #   ##   #",
    "Y": "#   ##   # # #   #    #    #    #  ", "Z": "#####    #   #   #   #   #    #####", " ": " " * 35,
    "0": " ### #   ##  ### # ###  ##   # ### ", "1": "  #   ##    #    #    #    #   ### ", "2": " ### #   #    #  ##  #   #    #####",
    "3": "#####" "   # " "  #  " "   # " "    #" "#   #" " ### ", "4": "   # " "  ## " " # # " "#  # " "#####" "   # " "   # ", "5": "######    ####     #    ##   # ### ",
    "6": "  ##  #   #    #### #   ##   # ### ", "7": "#####    #   #   #   #    #    #   ", "8": " ### #   ##   # ### #   ##   # ### ",
    "9": " ### #   ##   # ####    #   #  ##  ", ",": "               ##   ##    #   #    ", ".": "                         ##   ##   ",
    ":": "      ##   ##        ##   ##       ", "-": "               #####               ", "(": "   #   #   #    #    #     #     # ",
    ")": " #     #     #    #    #   #   #   ", "?": " ### #   #    #   #   #         #  ", "'": "  #    #   #                       ",
    "/": "         #   #   #   #   #         ",
}

ExposureMask, ButtonPressMask, ButtonReleaseMask, StructureNotifyMask = 1 << 15, 1 << 2, 1 << 3, 1 << 17
Expose, ButtonPress, ButtonRelease = 12, 4, 5
CWOverrideRedirect, CWBackPixel = 1 << 9, 1 << 1


class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
                ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong), ("bit_gravity", ctypes.c_int),
                ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int), ("event_mask", ctypes.c_long),
                ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", ctypes.c_int),
                ("colormap", ctypes.c_ulong), ("cursor", ctypes.c_ulong)]


class XAnyEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong)]


class XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xany", XAnyEvent), ("pad", ctypes.c_long * 24)]


def _lib():
    lib = touchmode.x11()
    vp, ul, i = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int
    for name, res, args in (
            ("XDefaultScreen", i, [vp]), ("XDisplayWidth", i, [vp, i]), ("XDisplayHeight", i, [vp, i]),
            ("XConnectionNumber", i, [vp]), ("XPending", i, [vp]), ("XNextEvent", i, [vp, ctypes.POINTER(XEvent)]),
            ("XCreateWindow", ul, [vp, ul, i, i, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, i, ctypes.c_uint, vp, ul,
                                   ctypes.POINTER(XSetWindowAttributes)]),
            ("XSelectInput", i, [vp, ul, ctypes.c_long]), ("XMapRaised", i, [vp, ul]), ("XUnmapWindow", i, [vp, ul]),
            ("XRaiseWindow", i, [vp, ul]), ("XCreateGC", vp, [vp, ul, ul, vp]), ("XSetForeground", i, [vp, vp, ul]),
            ("XFillRectangle", i, [vp, ul, vp, i, i, ctypes.c_uint, ctypes.c_uint]), ("XStoreName", i, [vp, ul, ctypes.c_char_p]),
            ("XMoveResizeWindow", i, [vp, ul, i, i, ctypes.c_uint, ctypes.c_uint])):
        f = getattr(lib, name)
        f.restype, f.argtypes = res, args
    return lib


def text_px(text: str) -> int:
    return len(text) * 6 - 1          # 5 wide + 1 gap, in font pixels


class Button:
    BG, FG, EDGE, POP_BG = 0x10213A, 0xEAF4FF, 0x5CC8FF, 0x0B1A2E

    def __init__(self, display: Optional[str] = None, demo: bool = False):
        self.lib = _lib()
        self.dpy = self.lib.XOpenDisplay(display.encode() if display else None)
        if not self.dpy:
            raise OSError("no X display (is Xwayland running inside the Airlock desktop?)")
        scr = self.lib.XDefaultScreen(self.dpy)
        self.sw, self.sh = self.lib.XDisplayWidth(self.dpy, scr), self.lib.XDisplayHeight(self.dpy, scr)
        self.root = self.lib.XDefaultRootWindow(self.dpy)
        self.demo = demo
        self.size = max(44, min(96, self.sw // 26))
        self.scale = max(2, self.size // 9)                    # one big letter fills the button
        self.mode: Optional[int] = None
        self.btn = self._window(6, 6, self.size, self.size, "Airlock touch mode")
        self.pop = self._window(0, 0, 10, 10, "Airlock touch mode popup")
        self.gc_b = self.lib.XCreateGC(self.dpy, self.btn, 0, None)
        self.gc_p = self.lib.XCreateGC(self.dpy, self.pop, 0, None)
        self.pop_until = 0.0
        self.pop_lines: List[str] = []
        self.lib.XMapRaised(self.dpy, self.btn)
        self.lib.XFlush(self.dpy)
        self.read_current()

    def _window(self, x, y, w, h, name):
        a = XSetWindowAttributes()
        a.override_redirect = 1                                 # no frame, not in the task bar, stays on top
        a.background_pixel = self.BG
        win = self.lib.XCreateWindow(self.dpy, self.root, x, y, w, h, 0, 0, 1, None, CWOverrideRedirect | CWBackPixel,
                                     ctypes.byref(a))
        self.lib.XSelectInput(self.dpy, win, ExposureMask | ButtonPressMask | ButtonReleaseMask)
        self.lib.XStoreName(self.dpy, win, name.encode())
        return win

    # ---------------------------------------------------------- drawing
    def _text(self, win, gc, text, x, y, s, color):
        self.lib.XSetForeground(self.dpy, gc, color)
        for k, ch in enumerate(text.upper()):
            rows = _F.get(ch, _F["?"])
            for r in range(7):
                for c in range(5):
                    if rows[r * 5 + c] == "#":
                        self.lib.XFillRectangle(self.dpy, win, gc, x + (k * 6 + c) * s, y + r * s, s, s)

    def draw_button(self):
        s, n = self.scale, self.size
        lib, gc = self.lib, self.gc_b
        lib.XSetForeground(self.dpy, gc, self.EDGE)
        lib.XFillRectangle(self.dpy, self.btn, gc, 0, 0, n, n)
        lib.XSetForeground(self.dpy, gc, self.BG)
        lib.XFillRectangle(self.dpy, self.btn, gc, 2, 2, n - 4, n - 4)
        letter = touchmode.MODES.get(self.mode, ("", "?", ""))[1] if self.mode is not None else "S"
        self._text(self.btn, gc, letter, (n - 5 * s) // 2, (n - 7 * s) // 2, s, self.FG)
        lib.XFlush(self.dpy)

    def show_popup(self, lines: List[str], seconds: float = 1.8):
        self.pop_lines = lines
        s = max(2, self.sw // 320)
        big = s * 2
        w = max(text_px(lines[0]) * big, max((text_px(t) * s for t in lines[1:]), default=0)) + 56
        h = 7 * big + sum(7 * s + 10 for _ in lines[1:]) + 52
        self.pop_geom = ((self.sw - w) // 2, (self.sh - h) // 2, w, h, s)
        self.lib.XMoveResizeWindow(self.dpy, self.pop, *self.pop_geom[:4])
        self.lib.XMapRaised(self.dpy, self.pop)
        self.pop_until = time.time() + seconds
        self.draw_popup()

    def draw_popup(self):
        if not self.pop_lines:
            return
        x, y, w, h, s = self.pop_geom
        lib, gc = self.lib, self.gc_p
        lib.XSetForeground(self.dpy, gc, self.EDGE)
        lib.XFillRectangle(self.dpy, self.pop, gc, 0, 0, w, h)
        lib.XSetForeground(self.dpy, gc, self.POP_BG)
        lib.XFillRectangle(self.dpy, self.pop, gc, 3, 3, w - 6, h - 6)
        big = s * 2
        self._text(self.pop, gc, self.pop_lines[0], (w - text_px(self.pop_lines[0]) * big) // 2, 24, big, self.EDGE)
        yy = 24 + 7 * big + 12
        for t in self.pop_lines[1:]:
            self._text(self.pop, gc, t, (w - text_px(t) * s) // 2, yy, s, self.FG)
            yy += 7 * s + 10
        lib.XFlush(self.dpy)

    # ---------------------------------------------------------- behaviour
    def read_current(self) -> None:
        if self.demo:
            return
        try:
            t = touchmode.current_file().read_text().strip()
            m = int(t) if t.isdigit() else None
        except OSError:
            m = self.mode
        if m != self.mode:
            self.mode = m
            self.draw_button()

    def tap(self) -> None:
        want = touchmode.next_mode(self.mode)
        name, _, what = touchmode.MODES[want]
        if self.demo:
            self.mode = want
            self.draw_button()
            self.show_popup([name, what])
            return
        try:
            touchmode._write(touchmode.request_file(), str(want))
        except OSError as e:
            self.show_popup(["Could not ask", str(e)[:40]])
            return
        deadline = time.time() + 1.5                           # the supervisor polls 5 times a second
        while time.time() < deadline:
            time.sleep(0.05)
            try:
                if touchmode.current_file().read_text().strip() == str(want):
                    self.mode = want
                    self.draw_button()
                    self.show_popup([name, what])
                    return
            except OSError:
                pass
        self.show_popup(["No change", "Airlock did not answer"])

    def run(self) -> int:
        ev = XEvent()
        fd = self.lib.XConnectionNumber(self.dpy)
        last_raise = 0.0
        self.draw_button()
        while True:
            while self.lib.XPending(self.dpy):
                self.lib.XNextEvent(self.dpy, ctypes.byref(ev))
                if ev.type == Expose:
                    if ev.xany.window == self.btn:
                        self.draw_button()
                    elif ev.xany.window == self.pop:
                        self.draw_popup()
                elif ev.type == ButtonRelease and ev.xany.window == self.btn:
                    self.tap()
                elif ev.type == ButtonRelease and ev.xany.window == self.pop:
                    self.pop_until = 0
            now = time.time()
            if self.pop_lines and now > self.pop_until:
                self.lib.XUnmapWindow(self.dpy, self.pop)
                self.pop_lines = []
                self.lib.XFlush(self.dpy)
            if now - last_raise > 2:                            # new windows must not cover the button
                self.read_current()
                self.lib.XRaiseWindow(self.dpy, self.btn)
                self.lib.XFlush(self.dpy)
                last_raise = now
            select.select([fd], [], [], 0.25)


def main() -> int:
    try:
        b = Button(os.environ.get("DISPLAY"), demo=bool(os.environ.get("DDG_TOUCH_DEMO")))
    except OSError as e:
        print("touch button: %s" % e, file=sys.stderr)
        return 1
    return b.run()


if __name__ == "__main__":
    sys.exit(main())
