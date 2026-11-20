# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Airlock touch gestures, read straight from the touchscreen (read-only: never grabbed, every touch still goes to
gamescope as usual).

* four fingers dragged downwards  -> "four_down"         (Airlock: lock / unlock the touch mode)
* three fingers tapped twice      -> "three_double_tap"  (Airlock: next touch mode, only while unlocked)

Linux multi-touch protocol B: ABS_MT_SLOT picks a finger, ABS_MT_TRACKING_ID -1 lifts it, positions follow, SYN_REPORT
closes a frame. The Recognizer is a pure state machine (feed it events, it returns gestures) so it can be tested with
recorded finger tracks; Watcher opens the real touchscreen(s) in a thread.
"""
from __future__ import annotations

import os
import select
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import padbridge

EV_SYN, EV_ABS = 0x00, 0x03
SYN_REPORT = 0
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2F, 0x35, 0x36, 0x39
BTN_TOUCH = 0x14A
INPUT_PROP_DIRECT = 0x01

SWIPE_MIN = 0.15        # four-finger drag: at least 15 % of the screen height downwards
SWIPE_MAX_S = 1.5
TAP_MAX_S = 0.35        # a tap: short ...
TAP_MAX_MOVE = 0.04     # ... and still (4 % of the screen)
DOUBLE_TAP_S = 0.6      # two three-finger taps within this time


class Recognizer:
    def __init__(self, xr: Tuple[int, int] = (0, 1919), yr: Tuple[int, int] = (0, 1079)):
        self.xr, self.yr = xr, yr
        self.slot = 0
        self.fingers: Dict[int, List[int]] = {}       # slot -> [x, y]
        self._pending: Dict[int, List[Optional[int]]] = {}
        self._reset_stroke()
        self.last_tap = -10.0

    def _reset_stroke(self):
        self.t0 = None
        self.max_n = 0
        self.c0: Optional[Tuple[float, float]] = None      # centroid when the most fingers first touched
        self.c_last: Optional[Tuple[float, float]] = None  # centroid while the most fingers were down
        self.move = 0.0

    def _norm(self, x, y):
        w = max(1, self.xr[1] - self.xr[0])
        h = max(1, self.yr[1] - self.yr[0])
        return (x - self.xr[0]) / w, (y - self.yr[0]) / h

    def _centroid(self):
        pts = [self._norm(*p) for p in self.fingers.values() if p[0] is not None and p[1] is not None]
        if not pts:
            return None
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)

    def feed(self, etype: int, code: int, value: int, t: float) -> Optional[str]:
        if etype == EV_ABS:
            if code == ABS_MT_SLOT:
                self.slot = value
            elif code == ABS_MT_TRACKING_ID:
                if value < 0:
                    self.fingers.pop(self.slot, None)
                else:
                    self.fingers.setdefault(self.slot, [None, None])
            elif code in (ABS_MT_POSITION_X, ABS_MT_POSITION_Y) and self.slot in self.fingers:
                self.fingers[self.slot][0 if code == ABS_MT_POSITION_X else 1] = value
            return None
        if etype == EV_SYN and code == SYN_REPORT:
            return self._frame(t)
        return None

    def _frame(self, t: float) -> Optional[str]:
        n = len(self.fingers)
        if n and self.t0 is None:
            self._reset_stroke()
            self.t0 = t
        if self.t0 is None:
            return None
        c = self._centroid()
        if n > self.max_n and c:
            self.max_n, self.c0, self.c_last = n, c, c
        elif n == self.max_n and c:
            self.c_last = c
            self.move = max(self.move, abs(c[0] - self.c0[0]) + abs(c[1] - self.c0[1]))
        if n:
            return None
        # every finger is up: what was it?
        dur = t - self.t0
        g = None
        if self.max_n >= 4 and self.c0 and self.c_last:
            dy = self.c_last[1] - self.c0[1]
            dx = abs(self.c_last[0] - self.c0[0])
            if dy >= SWIPE_MIN and dy > dx and dur <= SWIPE_MAX_S:
                g = "four_down"
        elif self.max_n == 3 and dur <= TAP_MAX_S and self.move <= TAP_MAX_MOVE:
            if t - self.last_tap <= DOUBLE_TAP_S:
                g = "three_double_tap"
                self.last_tap = -10.0
            else:
                self.last_tap = t
        self.t0 = None
        return g


# ------------------------------------------------------------------ the real touchscreen
def _prop_bits(text: str) -> Dict[str, set]:
    out = {}
    for block in text.strip().split("\n\n"):
        ev = next((h for line in block.splitlines() if line.startswith("H: Handlers=")
                   for h in line.split("=", 1)[1].split() if h.startswith("event")), None)
        prop = next((line.split("=", 1)[1] for line in block.splitlines() if line.startswith("B: PROP=")), "0")
        if ev:
            out["/dev/input/" + ev] = padbridge._bits(prop)
    return out


def find_touchscreens(text: Optional[str] = None) -> List[dict]:
    if text is None:
        try:
            text = Path(os.environ.get("DDG_INPUT_DEVICES") or padbridge.PROC_DEVICES).read_text()
        except OSError:
            return []
    props = _prop_bits(text)
    out = []
    for d in padbridge.parse_devices(text):
        if (ABS_MT_POSITION_X in d["abs"] and ABS_MT_SLOT in d["abs"] and INPUT_PROP_DIRECT in props.get(d["event"], set())
                and not d["name"].startswith(padbridge.OWN_NAME)):
            out.append(d)
    return out


class Watcher:
    """Reads the touchscreen(s) in a thread and calls on_gesture(name). Never grabs the device."""

    def __init__(self, on_gesture: Callable[[str], None], scan: Callable[[], List[dict]] = find_touchscreens):
        self.on_gesture, self.scan = on_gesture, scan
        self.fds: Dict[int, Recognizer] = {}
        self.names: List[str] = []
        self.note = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _open(self) -> None:
        for d in self.scan():
            try:
                fd = os.open(d["event"], os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                self.note = "cannot read %s (%s)" % (d["name"], e.strerror)
                continue
            xr = padbridge._abs_range(fd, ABS_MT_POSITION_X) or (0, 1919)
            yr = padbridge._abs_range(fd, ABS_MT_POSITION_Y) or (0, 1079)
            self.fds[fd] = Recognizer(xr, yr)
            self.names.append(d["name"])
        if self.fds:
            self.note = "gestures on (%s)" % ", ".join(self.names)
        elif not self.note:
            self.note = "no touchscreen found"

    def run(self) -> None:
        self._open()
        while not self._stop.is_set() and self.fds:
            try:
                ready, _, _ = select.select(list(self.fds), [], [], 0.5)
            except (OSError, ValueError):
                break
            for fd in ready:
                try:
                    data = os.read(fd, padbridge.EVENT_SIZE * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    self.fds.pop(fd, None)
                    continue
                rec = self.fds.get(fd)
                now = time.monotonic()
                for _s, _u, et, code, val in struct.iter_unpack(padbridge.EVENT_FMT,
                                                                  data[:len(data) - len(data) % padbridge.EVENT_SIZE]):
                    g = rec.feed(et, code, val, now) if rec else None
                    if g:
                        try:
                            self.on_gesture(g)
                        except Exception:  # noqa: BLE001 - a gesture must never stop the reader
                            pass

    def start(self) -> "Watcher":
        self._thread = threading.Thread(target=self.run, name="ddg-touchgesture", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        for fd in list(self.fds):
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds.clear()
