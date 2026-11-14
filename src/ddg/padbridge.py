# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Controller -> mouse and keyboard for the Airlock desktop.

Why it is needed: inside Game Mode, Steam hands a game (the Airlock tile counts as one) a
*gamepad*. The nested KDE desktop only understands mouse, keyboard and touch, so the buttons,
d-pad and sticks do nothing there while touch works. This module reads the pad and types for it.

How: it finds the controller(s) in /proc/bus/input/devices, reads their evdev events, and writes
mouse and keyboard events to two small virtual devices made through /dev/uinput. Game Mode's
gamescope picks those up like any plugged-in mouse and keyboard and passes them to the Airlock window.

Layout (the same idea as Steam's own "desktop" layout):
  left stick  = mouse          right stick = scroll        d-pad  = arrow keys
  A / RT      = left click     X / LT      = right click   L3     = middle click
  B           = Esc            Y           = Super (Start menu; press B or Y again to close it)
  LB          = Backspace      RB          = Tab
  Start/Menu  = Enter          View/Select = Alt+Tab (switch windows)

Light on purpose: it sleeps in select() until a pad moves; it only wakes 125 times a second
while a stick is held away from centre. Standard library only.
"""
from __future__ import annotations

import os
import select
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # Windows/macOS: never used there
    fcntl = None

# ---- linux/input-event-codes.h
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0, 1, 6, 8
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 0x10, 0x11
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2, BTN_SELECT, BTN_START = 0x136, 0x137, 0x138, 0x139, 0x13A, 0x13B
BTN_THUMBL, BTN_THUMBR = 0x13D, 0x13E
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223
KEY_ESC, KEY_BACKSPACE, KEY_TAB, KEY_ENTER, KEY_LEFTALT, KEY_LEFTMETA = 1, 14, 15, 28, 56, 125
KEY_UP, KEY_LEFT, KEY_RIGHT, KEY_DOWN = 103, 105, 106, 108

BUTTONS: Dict[int, Tuple[int, ...]] = {          # pad button -> keys/buttons pressed together
    BTN_SOUTH: (BTN_LEFT,), BTN_TR2: (BTN_LEFT,),
    BTN_NORTH: (BTN_RIGHT,), BTN_TL2: (BTN_RIGHT,),
    BTN_EAST: (KEY_ESC,), BTN_WEST: (KEY_LEFTMETA,),
    BTN_TL: (KEY_BACKSPACE,), BTN_TR: (KEY_TAB,),
    BTN_START: (KEY_ENTER,), BTN_SELECT: (KEY_LEFTALT, KEY_TAB),
    BTN_THUMBL: (BTN_MIDDLE,),
    BTN_DPAD_UP: (KEY_UP,), BTN_DPAD_DOWN: (KEY_DOWN,),
    BTN_DPAD_LEFT: (KEY_LEFT,), BTN_DPAD_RIGHT: (KEY_RIGHT,),
}
MOUSE_KEYS = (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE)
KEYBOARD_KEYS = sorted({c for v in BUTTONS.values() for c in v} - set(MOUSE_KEYS))
SPEEDS = {"slow": 500.0, "normal": 900.0, "fast": 1500.0}     # pixels a second at full stick
SCROLL_PER_SEC = 10.0                                           # wheel notches a second at full stick
DEADZONE = 0.20
TICK = 0.008

PROC_DEVICES = "/proc/bus/input/devices"
OWN_NAME = "Deck Deck Goose Airlock"


# ------------------------------------------------------------------ finding the controller
def _bits(words: str) -> set:
    """'B: KEY=' hex words (biggest first, 64 bits each) -> set of bit numbers."""
    out = set()
    for i, w in enumerate(reversed(words.split())):
        try:
            n = int(w, 16)
        except ValueError:
            continue
        for b in range(64):
            if n >> b & 1:
                out.add(i * 64 + b)
    return out


def parse_devices(text: str) -> List[dict]:
    """Every input device in /proc/bus/input/devices: name, event path, key bits, abs bits."""
    devs = []
    for block in text.strip().split("\n\n"):
        d = {"name": "", "event": "", "key": set(), "abs": set()}
        for line in block.splitlines():
            if line.startswith("N: Name="):
                d["name"] = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("H: Handlers="):
                for h in line.split("=", 1)[1].split():
                    if h.startswith("event"):
                        d["event"] = "/dev/input/" + h
            elif line.startswith("B: KEY="):
                d["key"] = _bits(line.split("=", 1)[1])
            elif line.startswith("B: ABS="):
                d["abs"] = _bits(line.split("=", 1)[1])
        if d["event"]:
            devs.append(d)
    return devs


def is_pad(d: dict) -> bool:
    return (BTN_SOUTH in d["key"] and ABS_X in d["abs"] and ABS_Y in d["abs"]
            and not d["name"].startswith(OWN_NAME))


def find_pads(text: Optional[str] = None) -> List[dict]:
    if text is None:
        try:
            text = Path(os.environ.get("DDG_INPUT_DEVICES") or PROC_DEVICES).read_text()
        except OSError:
            return []
    return [d for d in parse_devices(text) if is_pad(d)]


# ------------------------------------------------------------------ the virtual mouse + keyboard
def _ioc_w(nr: int, size: int = 4) -> int:       # _IOW('U', nr, int)
    return (1 << 30) | (size << 16) | (ord("U") << 8) | nr


UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = _ioc_w(100), _ioc_w(101), _ioc_w(102)
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)


def uinput_path() -> str:
    return os.environ.get("DDG_UINPUT") or "/dev/uinput"


def uinput_state() -> str:
    """'ok' | 'missing' (no /dev/uinput: the module isn't loaded) | 'denied' (not allowed to write)."""
    p = uinput_path()
    if not os.path.exists(p):
        return "missing"
    return "ok" if os.access(p, os.W_OK) else "denied"


class UinputDevice:
    """One virtual input device. emit() queues, sync() sends."""

    def __init__(self, name: str, keys=(), rels=()):
        self.fd = os.open(uinput_path(), os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for k in keys:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
            if rels:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
                for r in rels:
                    fcntl.ioctl(self.fd, UI_SET_RELBIT, r)
            # struct uinput_user_dev: name[80], input_id (bus, vendor, product, version), ff_effects_max, 4 x abs[64]
            os.write(self.fd, struct.pack("80sHHHHI", name.encode()[:79], 3, 0x1209, 0xDD6, 1, 0) + bytes(4 * 64 * 4))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except Exception:
            os.close(self.fd)
            raise
        self.buf = bytearray()

    def emit(self, etype: int, code: int, value: int) -> None:
        self.buf += struct.pack(EVENT_FMT, 0, 0, etype, code, value)

    def sync(self) -> None:
        if not self.buf:
            return
        self.buf += struct.pack(EVENT_FMT, 0, 0, EV_SYN, 0, 0)
        try:
            os.write(self.fd, bytes(self.buf))
        except OSError:
            pass
        self.buf.clear()

    def close(self) -> None:
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


class Sink:
    """The mouse and keyboard pair the translator writes to."""

    def __init__(self):
        self.mouse = UinputDevice(OWN_NAME + " Mouse", MOUSE_KEYS, (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL))
        try:
            self.kbd = UinputDevice(OWN_NAME + " Keyboard", KEYBOARD_KEYS)
        except Exception:
            self.mouse.close()
            raise

    def emit(self, etype: int, code: int, value: int) -> None:
        (self.mouse if etype == EV_REL or code in MOUSE_KEYS else self.kbd).emit(etype, code, value)

    def sync(self) -> None:
        self.mouse.sync()
        self.kbd.sync()

    def close(self) -> None:
        self.mouse.close()
        self.kbd.close()


# ------------------------------------------------------------------ pad events -> mouse/keyboard events
class Translator:
    """Pure logic (no files), so it is easy to test: feed() pad events in, tick() once per frame."""

    def __init__(self, emit: Callable[[int, int, int], None], speed: str = "normal"):
        self.emit = emit
        self.pps = SPEEDS.get(speed, SPEEDS["normal"])
        self.axis = {ABS_X: 0.0, ABS_Y: 0.0, ABS_RX: 0.0, ABS_RY: 0.0}
        self.held: Dict[int, int] = {}          # output code -> how many pad buttons hold it
        self.down: Dict[int, bool] = {}         # pad button (or virtual trigger/hat) -> pressed
        self.frac = [0.0, 0.0, 0.0, 0.0]        # leftovers: mouse x, y, wheel, hwheel
        self.last = None                        # time of the previous tick

    # -- outputs, counted so two buttons mapped to one click don't release it early
    def _press(self, outs: Tuple[int, ...]) -> None:
        for o in outs:
            self.held[o] = self.held.get(o, 0) + 1
            if self.held[o] == 1:
                self.emit(EV_KEY, o, 1)

    def _release(self, outs: Tuple[int, ...]) -> None:
        for o in reversed(outs):
            n = self.held.get(o, 0) - 1
            if n <= 0:
                self.held.pop(o, None)
                if n == 0:
                    self.emit(EV_KEY, o, 0)
            else:
                self.held[o] = n

    def _button(self, key: int, pressed: bool) -> None:
        outs = BUTTONS.get(key)
        if outs is None or self.down.get(key, False) == pressed:
            return
        self.down[key] = pressed
        (self._press if pressed else self._release)(outs)

    def feed(self, etype: int, code: int, value: int, rng: Optional[Tuple[int, int]] = None) -> None:
        """rng = (min, max) of the axis, as the device reports it."""
        if etype == EV_KEY:
            self._button(code, bool(value))
        elif etype == EV_ABS:
            lo, hi = rng if rng and rng[1] > rng[0] else ((-32768, 32767) if code < ABS_Z or code in (ABS_RX, ABS_RY) else (0, 255))
            if code in self.axis:
                mid, half = (lo + hi) / 2.0, (hi - lo) / 2.0
                self.axis[code] = max(-1.0, min(1.0, (value - mid) / half))
            elif code in (ABS_Z, ABS_RZ):               # analogue triggers: pressed past the half-way point
                pos = (value - lo) / float(hi - lo)
                key = BTN_TL2 if code == ABS_Z else BTN_TR2
                if pos > 0.55:
                    self._button(key, True)
                elif pos < 0.35:
                    self._button(key, False)
            elif code == ABS_HAT0X:
                self._button(BTN_DPAD_LEFT, value < 0)
                self._button(BTN_DPAD_RIGHT, value > 0)
            elif code == ABS_HAT0Y:
                self._button(BTN_DPAD_UP, value < 0)
                self._button(BTN_DPAD_DOWN, value > 0)

    @staticmethod
    def _curve(v: float) -> float:
        """Dead zone, then a gentle curve: small pushes are precise, big pushes are quick."""
        a = abs(v)
        if a < DEADZONE:
            return 0.0
        a = (a - DEADZONE) / (1.0 - DEADZONE)
        return (a * a if v > 0 else -(a * a))

    def moving(self) -> bool:
        return any(abs(v) >= DEADZONE for v in self.axis.values())

    def tick(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        dt = 0.0 if self.last is None else min(now - self.last, 0.1)
        self.last = now if self.moving() else None      # idle: forget the clock so the first step isn't huge
        if dt <= 0:
            return
        moves = (self.pps * dt * self._curve(self.axis[ABS_X]), self.pps * dt * self._curve(self.axis[ABS_Y]),
                 -SCROLL_PER_SEC * dt * self._curve(self.axis[ABS_RY]),
                 SCROLL_PER_SEC * dt * self._curve(self.axis[ABS_RX]))
        codes = ((REL_X, 0), (REL_Y, 1), (REL_WHEEL, 2), (REL_HWHEEL, 3))
        for (code, i), amount in zip(codes, moves):
            self.frac[i] += amount
            whole = int(self.frac[i])
            if whole:
                self.frac[i] -= whole
                self.emit(EV_REL, code, whole)

    def release_all(self) -> None:
        for k in [k for k, v in self.down.items() if v]:
            self._button(k, False)


# ------------------------------------------------------------------ the running bridge
EVIOCGABS = lambda a: 0x80184540 + a            # _IOR('E', 0x40+abs, struct input_absinfo)


def _abs_range(fd: int, code: int) -> Optional[Tuple[int, int]]:
    try:
        info = struct.unpack("6i", fcntl.ioctl(fd, EVIOCGABS(code), bytes(24)))
        return info[1], info[2]
    except (OSError, AttributeError, struct.error):
        return None


class Bridge:
    """Reads every controller it can find and drives the virtual mouse/keyboard until stop()."""

    def __init__(self, sink, speed: str = "normal", scan: Callable[[], List[dict]] = find_pads):
        self.sink = sink
        self.tr = Translator(self._emit, speed)
        self.scan = scan
        self.fds: Dict[int, Tuple[str, Dict[int, Tuple[int, int]]]] = {}     # fd -> (name, ranges)
        self.names: List[str] = []
        self._paths: Dict[int, str] = {}        # fd -> /dev/input/eventN, so a pad isn't opened twice
        self.events = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _emit(self, etype, code, value):
        self.sink.emit(etype, code, value)

    def add_fd(self, fd: int, name: str, ranges: Optional[dict] = None) -> None:
        self.fds[fd] = (name, ranges or {})
        if name not in self.names:
            self.names.append(name)

    def _open_new(self) -> None:
        paths = self._paths
        for d in self.scan():
            if d["event"] in paths.values():
                continue
            try:
                fd = os.open(d["event"], os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            rng = {c: r for c in (ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ) if (r := _abs_range(fd, c))}
            paths[fd] = d["event"]
            self.add_fd(fd, d["name"], rng)

    def _drop(self, fd: int) -> None:
        self.fds.pop(fd, None)
        self._paths.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass
        self.tr.release_all()
        self.sink.sync()

    def pump(self, timeout: float) -> int:
        """One round: wait for pad events (up to `timeout`), translate them, advance the sticks."""
        n = 0
        if self.fds:
            try:
                ready, _, _ = select.select(list(self.fds), [], [], timeout)
            except (OSError, ValueError):
                ready = []
            for fd in ready:
                try:
                    data = os.read(fd, EVENT_SIZE * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    self._drop(fd)
                    continue
                if not data:
                    self._drop(fd)
                    continue
                ranges = self.fds[fd][1]
                for _s, _u, etype, code, value in struct.iter_unpack(EVENT_FMT, data[:len(data) - len(data) % EVENT_SIZE]):
                    self.tr.feed(etype, code, value, ranges.get(code))
                    n += 1
        else:
            time.sleep(min(timeout, 0.5))
        self.tr.tick()
        self.sink.sync()
        self.events += n
        return n

    def run(self) -> None:
        next_scan = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_scan:                    # pads can appear late (Steam makes its virtual one on launch)
                    self._open_new()
                    next_scan = now + (5.0 if self.fds else 2.0)
                self.pump(TICK if self.tr.moving() else 0.5)
        finally:
            self.tr.release_all()
            try:
                self.sink.sync()
            except Exception:
                pass

    def start(self) -> "Bridge":
        self._thread = threading.Thread(target=self.run, name="ddg-padbridge", daemon=True)
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
        try:
            self.sink.close()
        except Exception:
            pass


def start(speed: str = "normal") -> Tuple[Optional[Bridge], str]:
    """Starts the bridge for an open Airlock. Returns (bridge or None, a plain-words note)."""
    if fcntl is None:
        return None, "controller bridge is Linux-only"
    st = uinput_state()
    if st != "ok":
        return None, ("/dev/uinput is not there (the 'uinput' kernel module is off)" if st == "missing"
                      else "not allowed to write /dev/uinput")
    try:
        b = Bridge(Sink(), speed).start()
    except OSError as e:
        return None, "could not create the virtual mouse/keyboard: %s" % e
    return b, "controller bridge on"


def check() -> dict:
    """What the Airlock tab shows: is a pad visible, and may we make a virtual mouse/keyboard?"""
    pads = [d["name"] for d in find_pads()]
    return {"pads": pads, "uinput": uinput_state()}


UDEV_RULE = 'KERNEL=="uinput", MODE="0660", TAG+="uaccess", OPTIONS+="static_node=uinput"\n'


def grant_access() -> Tuple[bool, str]:
    """One-time fix when /dev/uinput isn't writable: a udev rule + the uinput module, via the polkit password window."""
    script = ("modprobe uinput; printf '%s' '" + UDEV_RULE.strip() + "\n' > /etc/udev/rules.d/60-ddg-uinput.rules; "
              "echo uinput > /etc/modules-load.d/ddg-uinput.conf; udevadm control --reload; "
              "udevadm trigger --name-match=uinput; sleep 1; chmod 0666 /dev/uinput 2>/dev/null; true")
    pre = [] if os.environ.get("DDG_FAKE_PKEXEC") else ["pkexec"]
    try:
        p = subprocess.run(pre + ["sh", "-c", script], capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return False, "pkexec is not installed"
    except subprocess.TimeoutExpired:
        return False, "timed out waiting for the password window"
    ok = p.returncode == 0 and uinput_state() == "ok"
    return ok, "Controller access is ready." if ok else (p.stderr.strip() or "Could not change /dev/uinput.")


def watch(seconds: float = 15.0) -> int:
    """`ddg airlock pad --watch`: prints what the pad sends so you can see it is reaching the app."""
    pads = find_pads()
    if not pads:
        print("No controller found in %s" % PROC_DEVICES)
        return 1
    fds = {}
    for d in pads:
        try:
            fds[os.open(d["event"], os.O_RDONLY | os.O_NONBLOCK)] = d["name"]
        except OSError as e:
            print("%s (%s): cannot read - %s" % (d["name"], d["event"], e))
    if not fds:
        return 1
    print("Press buttons and move the sticks (%d seconds)..." % seconds)
    end = time.monotonic() + seconds
    seen = 0
    while time.monotonic() < end:
        ready, _, _ = select.select(list(fds), [], [], 0.25)
        for fd in ready:
            try:
                data = os.read(fd, EVENT_SIZE * 64)
            except OSError:
                continue
            for _s, _u, t, c, v in struct.iter_unpack(EVENT_FMT, data[:len(data) - len(data) % EVENT_SIZE]):
                if t in (EV_KEY, EV_ABS):
                    seen += 1
                    if seen <= 60:
                        print("%s: %s code 0x%x value %d" % (fds[fd], "key" if t == EV_KEY else "axis", c, v))
    print("%d events seen." % seen)
    return 0 if seen else 2
