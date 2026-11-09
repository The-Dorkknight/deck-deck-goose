# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""On-screen keyboard for KDE Plasma Desktop Mode (Bazzite KDE, Nobara KDE).

KDE has two touch keyboards: the new Plasma Keyboard (package
plasma-keyboard, default from Plasma 6.6 on Fedora-based systems) and the
older Maliit (maliit-keyboard). Either only appears once it is chosen as the
"virtual keyboard" input method. That setting lives in ~/.config/kwinrc:

    [Wayland]
    InputMethod=/usr/share/applications/com.github.maliit.keyboard.desktop

(KDE developer Martin Gräßlin's description; it is the same as System
Settings > Keyboard > Virtual Keyboard.) With it set, the keyboard pops up
when you tap a text box with the touchscreen; a keyboard icon also appears
in the system tray. This module sets it (and can undo it), asks KWin to
reload its settings, and adds a "Show on-screen keyboard" launcher.
Nothing outside your home folder is changed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from . import core
from .core import Step

KEY_GROUP = "Wayland"
KEY = "InputMethod"
SHOW_LAUNCHER = "ddg-show-keyboard.desktop"
# preferred first: KDE's own new keyboard, then Maliit
KEYBOARDS = [("org.kde.plasma.keyboard.desktop", "Plasma Keyboard"),
             ("com.github.maliit.keyboard.desktop", "Maliit"),
             ("org.maliit.keyboard.desktop", "Maliit")]
# packages to try, per package manager, in order
PACKAGES = {"dnf": ["plasma-keyboard", "maliit-keyboard"],
            "rpm-ostree": ["plasma-keyboard", "maliit-keyboard"],
            "apt": ["plasma-keyboard", "maliit-keyboard"],
            "zypper": ["plasma6-keyboard", "maliit-keyboard"],
            "pacman": ["plasma-keyboard", "maliit-keyboard"]}


def apps_dirs() -> List[Path]:
    override = os.environ.get("THISPC_APPS_DIR")
    if override:
        return [Path(override)]
    return [Path("/usr/share/applications"), Path("/usr/local/share/applications"),
            core.home() / ".local/share/applications",
            # a Flatpak keyboard is a last resort (openSUSE users report it needs the native package)
            core.home() / ".local/share/flatpak/exports/share/applications",
            Path("/var/lib/flatpak/exports/share/applications")]


def find_keyboard() -> Optional[Path]:
    """The desktop file of an installed KDE-compatible virtual keyboard
    (Plasma Keyboard preferred, then Maliit)."""
    dirs = [d for d in apps_dirs() if d.is_dir()]
    for name, _label in KEYBOARDS:
        for d in dirs:
            if (d / name).is_file():
                return d / name
    for d in dirs:
        hits = sorted(d.glob("*maliit*keyboard*.desktop")) + sorted(d.glob("org.kde.plasma.keyboard*.desktop"))
        if hits:
            return hits[0]
    return None


def keyboard_label(path: Optional[Path]) -> str:
    if not path:
        return ""
    for name, label in KEYBOARDS:
        if path.name == name:
            return label
    return "Plasma Keyboard" if "plasma" in path.name else "Maliit"


def package_manager() -> str:
    fake = os.environ.get("THISPC_FAKE_PKG")
    if fake is not None:
        return fake
    if Path("/run/ostree-booted").exists():
        return "rpm-ostree"            # Bazzite, Kinoite, Silverblue...
    for pm, exe in (("dnf", "dnf"), ("apt", "apt-get"), ("zypper", "zypper"), ("pacman", "pacman")):
        if shutil.which(exe):
            return pm
    return ""


def install_command(pm: str, pkg: str) -> List[str]:
    return {"dnf": ["pkexec", "dnf", "install", "-y", pkg],
            "rpm-ostree": ["pkexec", "rpm-ostree", "install", "--idempotent", "-y", pkg],
            "apt": ["pkexec", "apt-get", "install", "-y", pkg],
            "zypper": ["pkexec", "zypper", "--non-interactive", "install", pkg],
            "pacman": ["pkexec", "pacman", "-S", "--noconfirm", "--needed", pkg]}[pm]


def _run_install(cmd: List[str]) -> "tuple[int, str]":
    """Runs the package manager through pkexec (KDE asks for your password)."""
    fake = os.environ.get("THISPC_FAKE_PKG_RESULT")
    if fake is not None:
        rc = int(fake.split(":", 1)[0])
        if rc == 0 and ":" in fake:          # pretend the package dropped its desktop file
            d = apps_dirs()[0]
            d.mkdir(parents=True, exist_ok=True)
            (d / fake.split(":", 1)[1]).write_text("[Desktop Entry]\nName=Keyboard\n")
        return rc, "simulated"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        return 127, "pkexec is not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out after 30 minutes"
    return p.returncode, (p.stdout + p.stderr).strip()


def kwinrc() -> Path:
    return core.home() / ".config" / "kwinrc"


def read_key(path: Path, group: str, key: str) -> Optional[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    current = None
    for line in lines:
        s = line.strip()
        m = re.fullmatch(r"\[(.+)\]", s)
        if m:
            current = m.group(1)
            continue
        if current == group and "=" in s:
            k, v = s.split("=", 1)
            if re.sub(r"\[.*\]$", "", k.strip()) == key:     # also matches InputMethod[$e]
                return v.strip()
    return None


def write_key(path: Path, group: str, key: str, value: Optional[str]) -> None:
    """Set (or remove, with value None) one key, keeping everything else as it was."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    out, current, done, group_seen = [], None, False, False
    for line in lines:
        s = line.strip()
        m = re.fullmatch(r"\[(.+)\]", s)
        if m:
            if current == group and not done and value is not None:
                out.append("%s=%s" % (key, value))
                done = True
            current = m.group(1)
            group_seen = group_seen or current == group
            out.append(line)
            continue
        if current == group and "=" in s and re.sub(r"\[.*\]$", "", s.split("=", 1)[0].strip()) == key:
            if value is not None and not done:
                out.append("%s=%s" % (key, value))
                done = True
            continue                              # drop the old line (or remove it)
        out.append(line)
    if value is not None and not done:
        if current == group:
            out.append("%s=%s" % (key, value))
        else:
            if out and out[-1].strip():
                out.append("")
            out += ["[%s]" % group, "%s=%s" % (key, value)]
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".thispc-backup")
    if path.exists() and not backup.exists():
        shutil.copy2(str(path), str(backup))
    tmp = path.with_name(path.name + ".thispc-tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def session() -> Dict[str, object]:
    desk = os.environ.get("XDG_CURRENT_DESKTOP", "")
    return {"kde": "KDE" in desk.upper(), "wayland": os.environ.get("XDG_SESSION_TYPE", "") == "wayland",
            "desktop": desk, "type": os.environ.get("XDG_SESSION_TYPE", "")}


def status() -> Dict[str, object]:
    kb = find_keyboard()
    cur = read_key(kwinrc(), KEY_GROUP, KEY)
    pm = package_manager()
    return {"keyboard": str(kb) if kb else "", "label": keyboard_label(kb),
            "enabled": bool(kb and cur and Path(cur).name == kb.name),
            "current": cur or "", "package_manager": pm,
            "can_install": bool(pm) and not kb, "needs_reboot": pm == "rpm-ostree", **session()}


def install_keyboard() -> List[Step]:
    """Install a touch keyboard with the system's package manager, then turn it on."""
    name = "Install on-screen keyboard"
    if find_keyboard():
        return enable()
    from . import gamemode
    if gamemode.in_game_mode():
        return [Step(name, False, "Switch to Desktop Mode first: the password window can't appear in Game Mode.")]
    pm = package_manager()
    if not pm:
        return [Step(name, False, "Couldn't tell which package manager this system uses. Install the "
                     "'plasma-keyboard' package (or 'maliit-keyboard') with your software centre, then press Turn on.")]
    tried = []
    for pkg in PACKAGES[pm]:
        rc, out = _run_install(install_command(pm, pkg))
        tried.append(pkg)
        if rc in (126, 127) and "pkexec" in " ".join(install_command(pm, pkg)):
            why = "the password prompt was cancelled" if rc == 126 else out
            return [Step(name, False, "Not installed: %s." % why)]
        if rc == 0:
            if pm == "rpm-ostree":
                return [Step(name, True, "%s has been added to the system. Restart the computer, then come back "
                             "and press Turn on (Bazzite and other image-based systems only apply new "
                             "packages after a restart)." % pkg)]
            if find_keyboard():
                return [Step(name, True, "installed %s" % pkg)] + enable()
    return [Step(name, False, "None of these packages could be installed: %s. Last message: %s"
                 % (", ".join(tried), (out.splitlines() or ["(none)"])[-1][:300]))]


def _reload_kwin() -> bool:
    for cmd in (["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"],
                ["qdbus", "org.kde.KWin", "/KWin", "reconfigure"],
                ["gdbus", "call", "--session", "--dest", "org.kde.KWin", "--object-path", "/KWin",
                 "--method", "org.kde.KWin.reconfigure"]):
        rc, _ = core.run(cmd)
        if rc == 0:
            return True
    return False


def _show_launcher_text() -> str:
    return ("[Desktop Entry]\nType=Application\nName=Show on-screen keyboard\n"
            "Comment=Open KDE's touch keyboard (Deck Deck Goose)\nIcon=input-keyboard-virtual\n"
            "Exec=gdbus call --session --dest org.kde.KWin --object-path /VirtualKeyboard --method "
            "org.freedesktop.DBus.Properties.Set org.kde.kwin.VirtualKeyboard active \"<true>\"\n"
            "Categories=Utility;Accessibility;\n")


def enable() -> List[Step]:
    name = "On-screen keyboard"
    s = session()
    kb = find_keyboard()
    if not kb:
        pm = package_manager()
        return [Step(name, False, "No KDE touch keyboard is installed (looked for Plasma Keyboard and Maliit). "
                     + ("Press Install keyboard to add one (it asks for your password)." if pm else
                        "Install the 'plasma-keyboard' package with your software centre.")
                     + " Steam's own keyboard also works in Desktop Mode while Steam is open (Steam button + X).")]
    steps: List[Step] = []
    if not s["kde"]:
        steps.append(Step("Desktop", True, "This doesn't look like KDE Plasma (%s); the setting is written "
                          "anyway but only KDE uses it." % (s["desktop"] or "unknown")))
    if s["type"] and not s["wayland"]:
        steps.append(Step("Session", True, "This is an X11 session; KDE's virtual keyboard needs Wayland "
                          "(the default on Bazzite). Choose 'Plasma (Wayland)' at the login screen."))
    tool = shutil.which("kwriteconfig6") if not os.environ.get("THISPC_FAKE_TOOLS") else None
    if tool:
        core.run([tool, "--notify", "--file", "kwinrc", "--group", KEY_GROUP, "--key", KEY, str(kb)])
        core.run([tool, "--notify", "--file", "kwinrc", "--group", KEY_GROUP, "--key", "VirtualKeyboardEnabled", "true"])
    if read_key(kwinrc(), KEY_GROUP, KEY) != str(kb):
        write_key(kwinrc(), KEY_GROUP, KEY, str(kb))
    if read_key(kwinrc(), KEY_GROUP, "VirtualKeyboardEnabled") != "true":
        write_key(kwinrc(), KEY_GROUP, "VirtualKeyboardEnabled", "true")
    ok = Path(read_key(kwinrc(), KEY_GROUP, KEY) or "").name == kb.name
    steps.append(Step(name, ok, ("set KDE's virtual keyboard to %s (%s)" % (keyboard_label(kb), kb.name)) if ok
                      else "the setting could not be read back from %s" % kwinrc()))
    apps = core.home() / ".local/share/applications"
    apps.mkdir(parents=True, exist_ok=True)
    (apps / SHOW_LAUNCHER).write_text(_show_launcher_text())
    steps.append(Step("Show keyboard launcher", True, "added 'Show on-screen keyboard' to the app menu "
                      "(for when no text box is focused; may not work on every Plasma version)"))
    live = _reload_kwin()
    steps.append(Step("Apply", True, ("KWin reloaded. Tap a text box with the touchscreen; the keyboard should "
                                      "appear. If not, log out and back in, then turn it on from the keyboard "
                                      "icon in the system tray.") if live else
                      "Log out and back in, then tap a text box (or use the keyboard icon in the system tray)."))
    return steps


def disable() -> List[Step]:
    write_key(kwinrc(), KEY_GROUP, KEY, None)
    write_key(kwinrc(), KEY_GROUP, "VirtualKeyboardEnabled", None)
    try:
        (core.home() / ".local/share/applications" / SHOW_LAUNCHER).unlink()
    except OSError:
        pass
    _reload_kwin()
    ok = read_key(kwinrc(), KEY_GROUP, KEY) is None
    return [Step("On-screen keyboard", ok, "turned off (log out and back in if it still appears)" if ok
                 else "could not remove the setting")]
