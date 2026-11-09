# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Install Deck Deck Goose on this handheld in one step (no password, nothing outside your home folder):

  1. a copy of the program (and the bundled Zombie-Deck) in ~/.local/share/deck-deck-goose,
  2. "Deck Deck Goose" in the desktop's application menu (KDE / GNOME / Mint),
  3. the Deck Deck Goose and Airlock tiles in Steam's Game Mode (Steam closes for a moment).

After that the unzipped download folder can be deleted. Running a newer download and pressing
Install again updates the copy and the tiles. Linux only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List

from . import __version__, core, gamemode
from .core import Step

MENU_FILE = "deck-deck-goose.desktop"


def menu_path() -> Path:
    return core.home() / ".local" / "share" / "applications" / MENU_FILE


def installed_version() -> str:
    try:
        m = re.search(r'__version__\s*=\s*"([^"]+)"', (core.data_dir() / "lib" / "ddg" / "__init__.py").read_text())
        return m.group(1) if m else ""
    except OSError:
        return ""


def running_installed_copy() -> bool:
    here = Path(__file__).resolve().parent
    return here == (core.data_dir() / "lib" / "ddg").resolve()


def menu_text() -> str:
    lib = core.data_dir() / "lib"
    icon = lib / "ddg" / "assets" / "gm-icon.png"
    return ("[Desktop Entry]\nType=Application\nName=Deck Deck Goose\nGenericName=Disks, Steam games, Airlock\n"
            "Comment=This PC folder, Steam game manager, Game Mode tiles, sound fix, Airlock and Wake\n"
            "Exec=env PYTHONPATH=%s python3 -m ddg gui\nIcon=%s\nTerminal=false\n"
            "Categories=System;Utility;Game;\nKeywords=steam;games;disks;this pc;airlock;\n" % (lib, icon))


def status() -> dict:
    v = installed_version()
    gm = gamemode.status()
    return {"installed": bool(v), "version": v, "running": __version__, "from_install": running_installed_copy(),
            "update": bool(v) and v != __version__ and not running_installed_copy(),
            "menu": menu_path().exists(), "tiles": bool(gm.get("installed")), "airlock_tile": bool(gm.get("airlock_installed")),
            "browser": gm.get("browser", ""), "in_game_mode": gm.get("in_game_mode")}


def install(tiles: bool = True) -> List[Step]:
    if gamemode.in_game_mode():
        return [Step("Install", False, "Install from Desktop Mode: Steam has to be closed for a moment.")]
    steps = [core.copy_program()]
    if not steps[0].ok:
        return steps
    p = menu_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(menu_text())
    os.chmod(str(p), 0o755)
    ok = p.exists() and str(core.data_dir() / "lib") in p.read_text()
    steps.append(Step("Application menu entry", ok, "Deck Deck Goose (Utilities / System)" if ok else str(p)))
    core.run(["update-desktop-database", str(p.parent)])
    if tiles:
        steps += gamemode.install()
    return steps


def uninstall() -> List[Step]:
    """Removes the menu entry and the Game Mode tiles. Your settings, the This PC folder and the
    program copy (used by the refresh timer) stay; 'ddg uninstall' removes the This PC parts."""
    steps: List[Step] = []
    p = menu_path()
    p.unlink(missing_ok=True)
    steps.append(Step("Application menu entry", not p.exists(), "removed"))
    if gamemode.status().get("installed") or gamemode.status().get("airlock_installed"):
        steps += gamemode.remove()
    return steps
