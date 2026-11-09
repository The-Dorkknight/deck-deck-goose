# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Sound: fix the quiet ROG Ally speakers and boost the volume.

On Bazzite the ROG Ally has two outputs: "ROG Ally", a sound-processing
(EQ) device made with a PipeWire filter-chain, and "Family 17h/19h/1ah HD
Audio Controller", the real speakers it plays through. The two volumes
multiply, so if the hardware one sits below 100% everything is quiet however
high the ROG Ally slider goes.

* Fix quiet sound: sets the real (non-HDMI) hardware outputs under a
  sound-processing output to 100% and unmutes them.
* Boost: raises the default output above 100% (software gain, up to 150%,
  KDE's own limit) and lets KDE's volume slider go that high too.
* Use this: makes another output the default (e.g. the Family device
  directly: louder at the same slider position, but without the EQ).
* Keep after restart: a small user service re-applies the above at login.

Talks to PipeWire with pactl (pipewire-pulse) or, failing that, pw-dump and
wpctl. Only changes volumes, the default output, ~/.config/plasmaparc
(RaiseMaximumVolume) and its own user service.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import core
from .core import Step

MAX_BOOST = 150
SERVICE = "ddg-sound.service"
SERVICE_TEXT = """[Unit]
Description=Deck Deck Goose: re-apply sound fix and volume boost
After=pipewire.service pipewire-pulse.service wireplumber.service

[Service]
Type=oneshot
Environment=PYTHONPATH=%h/.local/share/deck-deck-goose/lib
ExecStartPre=/usr/bin/sleep 8
ExecStart=/usr/bin/env python3 -m ddg sound apply

[Install]
WantedBy=default.target
"""


# ----------------------------------------------------------- talking to PipeWire
def _fake_file() -> Optional[Path]:
    f = os.environ.get("THISPC_FAKE_AUDIO")
    return Path(f) if f else None


def _fake(cmd: List[str]) -> Tuple[int, str]:
    """Stand-in for pactl, used by the tests and the screenshots."""
    f = _fake_file()
    st = json.loads(f.read_text())
    sinks = {s["name"]: s for s in st["sinks"]}

    def save() -> Tuple[int, str]:
        st.setdefault("calls", []).append(cmd[1:])
        f.write_text(json.dumps(st))
        return 0, ""
    if cmd[0] != "pactl":
        return 127, "not found"
    if cmd[1:] == ["-f", "json", "list", "sinks"]:
        out = [{"index": i, "name": s["name"], "description": s["description"], "mute": s.get("mute", False),
                "volume": {"front-left": {"value_percent": "%d%%" % s["volume"]},
                           "front-right": {"value_percent": "%d%%" % s["volume"]}},
                "properties": s.get("properties", {})} for i, s in enumerate(st["sinks"])]
        return 0, json.dumps(out)
    if cmd[1] == "get-default-sink":
        return 0, st["default"]
    if cmd[1] in ("set-sink-volume", "set-sink-mute", "set-default-sink") and cmd[2] not in sinks:
        return 1, "Failure: No such entity"
    if cmd[1] == "set-sink-volume":
        sinks[cmd[2]]["volume"] = int(cmd[3].rstrip("%"))
        return save()
    if cmd[1] == "set-sink-mute":
        sinks[cmd[2]]["mute"] = cmd[3] in ("1", "true", "yes")
        return save()
    if cmd[1] == "set-default-sink":
        st["default"] = cmd[2]
        return save()
    return 1, "unknown"


def _run(cmd: List[str]) -> Tuple[int, str]:
    if _fake_file():
        return _fake(cmd)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return 127, "%s: not found" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "%s: timed out" % cmd[0]
    return p.returncode, (p.stdout if p.returncode == 0 else p.stdout + p.stderr).strip()


def backend() -> str:
    if _fake_file():
        return "pactl"
    if shutil.which("pactl"):
        return "pactl"
    if shutil.which("wpctl") and shutil.which("pw-dump"):
        return "wpctl"
    return ""


def _percent(vol: dict) -> int:
    vals = []
    for ch in (vol or {}).values():
        m = re.match(r"\s*(\d+)", str(ch.get("value_percent", "")))
        if m:
            vals.append(int(m.group(1)))
    return max(vals) if vals else 0


def _classify(name: str, desc: str, props: dict) -> Dict[str, bool]:
    api = props.get("device.api", "")
    hardware = name.startswith("alsa_output.") or api == "alsa"
    text = (name + " " + desc).lower()
    internal = hardware and (".pci-" in name or props.get("device.bus") == "pci")
    return {"hardware": hardware, "internal": internal,
            "hdmi": hardware and any(w in text for w in ("hdmi", "displayport", "radeon high definition")),
            "bluetooth": name.startswith("bluez_output.") or api == "bluez5",
            # a sound-processing device (filter-chain, EQ, effects) that plays through another output
            "processing": not hardware and not name.startswith("bluez_output.") and api not in ("bluez5",)
                          and props.get("media.class", "Audio/Sink") in ("Audio/Sink", "")}


def parse_pactl(text: str) -> List[dict]:
    out = []
    for s in json.loads(text or "[]"):
        name, desc, props = s.get("name", ""), s.get("description", "") or s.get("name", ""), s.get("properties") or {}
        out.append(dict(id=str(s.get("index", "")), name=name, desc=desc, volume=_percent(s.get("volume")),
                        muted=bool(s.get("mute")), **_classify(name, desc, props)))
    return out


def parse_pw_dump(text: str) -> Tuple[List[dict], str]:
    """Sinks and the default sink name from pw-dump's JSON."""
    sinks, default = [], ""
    for o in json.loads(text or "[]"):
        if o.get("type") == "PipeWire:Interface:Metadata" and (o.get("props") or {}).get("metadata.name") == "default":
            for m in o.get("metadata") or []:
                if m.get("key") == "default.audio.sink":
                    v = m.get("value")
                    if isinstance(v, str):
                        try:
                            v = json.loads(v)
                        except ValueError:
                            v = {"name": v}
                    default = (v or {}).get("name", "")
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        props = (o.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Sink":
            continue
        name = props.get("node.name", "")
        desc = props.get("node.description") or props.get("node.nick") or name
        sinks.append(dict(id=str(o.get("id")), name=name, desc=desc, volume=0, muted=False,
                          **_classify(name, desc, props)))
    return sinks, default


def sinks() -> Tuple[List[dict], str, str]:
    """(outputs, default output name, error)."""
    be = backend()
    if be == "pactl":
        rc, out = _run(["pactl", "-f", "json", "list", "sinks"])
        if rc != 0:
            return [], "", out or "pactl failed"
        try:
            lst = parse_pactl(out)
        except ValueError:
            return [], "", "pactl gave output this version can't read"
        rc, d = _run(["pactl", "get-default-sink"])
        return lst, d.strip() if rc == 0 else "", ""
    if be == "wpctl":
        rc, out = _run(["pw-dump"])
        if rc != 0:
            return [], "", out
        try:
            lst, d = parse_pw_dump(out)
        except ValueError:
            return [], "", "pw-dump gave output this version can't read"
        for s in lst:
            rc, v = _run(["wpctl", "get-volume", s["id"]])
            m = re.search(r"Volume:\s*([\d.]+)", v)
            if rc == 0 and m:
                s["volume"] = int(round(float(m.group(1)) * 100))
                s["muted"] = "MUTED" in v
        return lst, d, ""
    return [], "", "Neither pactl nor wpctl was found, so the sound system can't be reached."


def _set_volume(s: dict, pct: int) -> Tuple[int, str]:
    if backend() == "wpctl":
        return _run(["wpctl", "set-volume", s["id"], "%.2f" % (pct / 100.0)])
    return _run(["pactl", "set-sink-volume", s["name"], "%d%%" % pct])


def _unmute(s: dict) -> Tuple[int, str]:
    if backend() == "wpctl":
        return _run(["wpctl", "set-mute", s["id"], "0"])
    return _run(["pactl", "set-sink-mute", s["name"], "0"])


# ----------------------------------------------------------------- KDE slider
def plasmaparc() -> Path:
    return core.home() / ".config" / "plasmaparc"


def _kde_raise(on: bool) -> None:
    from . import osk          # same careful one-line ini editor as the keyboard setting
    osk.write_key(plasmaparc(), "General", "RaiseMaximumVolume", "true" if on else None)


# ---------------------------------------------------------------- the actions
def status() -> Dict[str, object]:
    lst, default, err = sinks()
    d = next((s for s in lst if s["name"] == default), None)
    under = [s for s in lst if s["internal"] and not s["hdmi"]]
    cfg = core.load_config().get("sound", {})
    return {"error": err, "sinks": lst, "default": default,
            "default_desc": d["desc"] if d else "", "volume": d["volume"] if d else 0,
            "processing": bool(d and d["processing"]),
            "can_fix": bool(d and d["processing"] and under),
            "fixed": bool(d and d["processing"] and under and all(s["volume"] >= 100 and not s["muted"] for s in under)),
            "max_boost": MAX_BOOST, "saved": cfg,
            "keep": (core.systemd_dir() / SERVICE).exists()}


def fix_quiet() -> List[Step]:
    """Set the real speakers under a sound-processing output to 100%."""
    name = "Fix quiet sound"
    lst, default, err = sinks()
    if err:
        return [Step(name, False, err)]
    d = next((s for s in lst if s["name"] == default), None)
    if not d or not d["processing"]:
        return [Step(name, True, "Nothing to fix: the default output (%s) is the hardware itself. "
                     "Use Boost if it is still too quiet." % (d["desc"] if d else "none"))]
    steps = []
    for s in [x for x in lst if x["internal"] and not x["hdmi"]]:      # built-in only: never a USB/Bluetooth headset
        rc, out = _set_volume(s, 100)
        rc2, out2 = _unmute(s)
        steps.append(Step("%s to 100%%" % s["desc"], rc == 0 and rc2 == 0,
                          "was %d%%%s" % (s["volume"], ", unmuted" if s["muted"] else "") if rc == 0 else out or out2))
    if not steps:
        return [Step(name, False, "No built-in hardware output was found under %s." % d["desc"])]
    core.update_config(merge_key="sound", merge={"fix": True})
    return steps


def boost(pct: int) -> List[Step]:
    """Set the default output's volume; above 100% is software gain."""
    pct = max(10, min(MAX_BOOST, int(pct)))
    lst, default, err = sinks()
    if err:
        return [Step("Volume", False, err)]
    d = next((s for s in lst if s["name"] == default), None)
    if not d:
        return [Step("Volume", False, "There is no default output.")]
    rc, out = _set_volume(d, pct)
    steps = [Step("%s to %d%%" % (d["desc"], pct), rc == 0,
                  ("was %d%%" % d["volume"] + (" (above 100% is a software boost: very loud parts may "
                   "distort)" if pct > 100 else "")) if rc == 0 else out)]
    if rc == 0:
        _kde_raise(pct > 100)
        if pct > 100:
            steps.append(Step("KDE volume slider", True, "allowed up to 150% (Raise maximum volume)"))
        core.update_config(merge_key="sound", merge={"boost": pct})
    return steps


def use_output(name: str) -> List[Step]:
    lst, _default, err = sinks()
    if err:
        return [Step("Default output", False, err)]
    s = next((x for x in lst if x["name"] == name), None)
    if not s:
        return [Step("Default output", False, "That output is no longer there.")]
    if backend() == "wpctl":
        rc, out = _run(["wpctl", "set-default", s["id"]])
    else:
        rc, out = _run(["pactl", "set-default-sink", s["name"]])
    if rc == 0:
        core.update_config(merge_key="sound", merge={"default": s["name"]})
    return [Step("Default output", rc == 0, s["desc"] if rc == 0 else out)]


def apply_saved() -> List[Step]:
    """Re-apply what was chosen (run at login by the user service)."""
    cfg = core.load_config().get("sound", {})
    steps: List[Step] = []
    if cfg.get("default"):
        lst, _d, _e = sinks()
        if any(s["name"] == cfg["default"] for s in lst):
            steps += use_output(cfg["default"])
    if cfg.get("fix"):
        steps += fix_quiet()
    if cfg.get("boost"):
        steps += boost(cfg["boost"])
    return steps or [Step("Sound", True, "nothing saved to re-apply")]


def keep(on: bool) -> List[Step]:
    """Install or remove the user service that re-applies the settings at login."""
    unit = core.systemd_dir() / SERVICE
    if on:
        steps = [core.copy_program()]
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(SERVICE_TEXT)
        core.run(["systemctl", "--user", "daemon-reload"])
        rc, out = core.run(["systemctl", "--user", "enable", SERVICE])
        steps.append(Step("Re-apply at login", rc == 0, "on (%s)" % SERVICE if rc == 0 else out))
        return steps
    core.run(["systemctl", "--user", "disable", SERVICE])
    try:
        unit.unlink()
    except OSError:
        pass
    core.run(["systemctl", "--user", "daemon-reload"])
    return [Step("Re-apply at login", True, "off")]


def reset() -> List[Step]:
    """Back to 100%, KDE's slider limit back to normal, forget the saved choices,
    remove the login service. The hardware level and default output stay as they are."""
    steps = boost(100) if backend() else []
    _kde_raise(False)
    core.update_config(remove=("sound",))
    return steps + keep(False)


def service_installed() -> bool:
    return (core.systemd_dir() / SERVICE).exists()
