# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Game Mode tiles: adds "Deck Deck Goose" (the app, full screen) and "Airlock"
(the desktop portal, see airlock.py) to Steam's library as non-Steam
shortcuts, so they can be opened in Game Mode (Bazzite / SteamOS-style
gamescope session). A tile made under the old name, "This PC Games", is
replaced.

What "Add to Game Mode" does (once, from Desktop Mode, Steam closed):
  1. Keeps a copy of this program in ~/.local/share/deck-deck-goose/lib.
  2. Writes a small launcher script next to it. In Game Mode the launcher
     starts the program and opens its page in a browser in kiosk (full
     screen) mode; pressing "Back to Steam" (or quitting from Steam) closes both.
  3. Adds a shortcut to each Steam account's shortcuts.vdf (binary file,
     backup kept) and puts tile artwork in that account's grid folder.
Steam must be closed while shortcuts.vdf is written, or it would overwrite
the change; Steam is restarted afterwards if it was running.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import core, steam, vdf
from .core import Step

SHORTCUT_NAME = "Deck Deck Goose"
AIRLOCK_NAME = "Airlock"
LAUNCHER = "ddg-gamemode.sh"
AIRLOCK_LAUNCHER = "ddg-airlock.sh"
LEGACY_NAME = "This PC Games"                 # the tile made by This PC for Linux (<= 0.4)
LEGACY_LAUNCHER = "this-pc-gamemode.sh"
MARK = "gamemode-installed"
BACKUP = ".thispc-backup"
# flatpak id, launch style
BROWSERS = [("org.mozilla.firefox", "firefox"), ("com.google.Chrome", "chromium"),
            ("org.chromium.Chromium", "chromium"), ("com.brave.Browser", "chromium"),
            ("com.microsoft.Edge", "chromium"), ("io.github.ungoogled_software.ungoogled_chromium", "chromium")]
NATIVE = [("firefox", "native-firefox"), ("chromium-browser", "native-chromium"),
          ("chromium", "native-chromium"), ("google-chrome", "native-chromium")]
ART = {"p.png": "gm-portrait.png", ".png": "gm-wide.png", "_hero.png": "gm-hero.png",
       "_logo.png": "gm-logo.png", "_icon.png": "gm-icon.png"}
AIRLOCK_ART = {"p.png": "airlock-portrait.png", ".png": "airlock-wide.png", "_hero.png": "airlock-hero.png",
               "_logo.png": "airlock-logo.png", "_icon.png": "airlock-icon.png"}
# key -> (shortcut name, launcher file name, artwork map, icon file)
TILES = {"ddg": (SHORTCUT_NAME, LAUNCHER, ART, "gm-icon.png"),
         "airlock": (AIRLOCK_NAME, AIRLOCK_LAUNCHER, AIRLOCK_ART, "airlock-icon.png")}
FIREFOX_PREFS = """// Deck Deck Goose: quiet, full-screen profile for the Game Mode tile
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.tabs.warnOnClose", false);
user_pref("trailhead.firstrun.didSeeAboutWelcome", true);
// Game Mode has no network-status service for Flatpak apps, so Firefox decided it was offline and
// refused even this local page ("needs internet"). The page is on this handheld: never go offline.
user_pref("network.manage-offline-status", false);
user_pref("network.offline-mirrors-connectivity", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.proxy.type", 0);
user_pref("network.proxy.no_proxies_on", "127.0.0.1, localhost");
"""


def in_game_mode() -> bool:
    """True when running inside the Game Mode (gamescope) session or from the tile."""
    if os.environ.get("THISPC_GAMEMODE"):
        return True
    return "gamescope" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def launcher_path(tile: str = "ddg") -> Path:
    return core.data_dir() / TILES[tile][1]


def exe_field(tile: str = "ddg") -> str:
    return '"%s"' % launcher_path(tile)


def shortcut_appid(exe: str, name: str) -> int:
    """The id Steam uses for a non-Steam shortcut's artwork (unsigned 32-bit)."""
    return zlib.crc32((exe + name).encode("utf-8")) | 0x80000000


def _signed(u: int) -> int:
    return u - 2 ** 32 if u >= 2 ** 31 else u


def find_browser() -> Optional[Tuple[str, str]]:
    """(name, style) of a browser that can show the page full screen."""
    rc, out = core.run(["flatpak", "list", "--app", "--columns=application"])
    have = set(out.split()) if rc == 0 else set()
    for app, kind in BROWSERS:
        if app in have:
            return app, kind
    if not os.environ.get("THISPC_FAKE_TOOLS"):
        for cmd, kind in NATIVE:
            if shutil.which(cmd):
                return cmd, kind
    return None


def launcher_text(browser: str, kind: str) -> str:
    return r'''#!/bin/sh
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Deck Deck Goose - Game Mode launcher, written by "Add to Game Mode".
# Starts the program, shows its page full screen, and closes both together.
if [ -f /.flatpak-info ] && command -v flatpak-spawn >/dev/null 2>&1; then
  exec flatpak-spawn --host "$0" "$@"          # Steam itself is a Flatpak: run on the host
fi
DATA="$HOME/.local/share/deck-deck-goose"
URLFILE="${XDG_RUNTIME_DIR:-/tmp}/ddg-gm-$$.url"
rm -f "$URLFILE"
PYTHONPATH="$DATA/lib" THISPC_GAMEMODE=1 python3 -m ddg gui --no-browser --url-file "$URLFILE" \
  >"$DATA/gamemode.log" 2>&1 &
SERVER=$!
i=0
while [ ! -s "$URLFILE" ] && [ $i -lt 100 ]; do sleep 0.2; i=$((i+1)); done
URL=$(cat "$URLFILE" 2>/dev/null)
if [ -z "$URL" ]; then kill $SERVER 2>/dev/null; exit 1; fi
echo "$(date '+%F %T') server $URL (pid $SERVER)" > "$DATA/gamemode-browser.log"
BROWSER="__BROWSER__"
case "__KIND__" in
  firefox)
    P="$HOME/.var/app/$BROWSER/data/ddg-gamemode-profile"; mkdir -p "$P"
    cat > "$P/user.js" <<'PREFS'
__PREFS__PREFS
    flatpak run "$BROWSER" --new-instance --profile "$P" --kiosk "$URL" >>"$DATA/gamemode-browser.log" 2>&1 & ;;
  chromium)
    D="$HOME/.var/app/$BROWSER/data/ddg-gamemode-chrome"; mkdir -p "$D"
    flatpak run "$BROWSER" --user-data-dir="$D" --no-first-run --no-proxy-server --kiosk --app="$URL" >>"$DATA/gamemode-browser.log" 2>&1 & ;;
  native-firefox)
    P="$DATA/ddg-gamemode-profile"; mkdir -p "$P"
    cat > "$P/user.js" <<'PREFS'
__PREFS__PREFS
    "$BROWSER" --new-instance --profile "$P" --kiosk "$URL" >>"$DATA/gamemode-browser.log" 2>&1 & ;;
  native-chromium)
    D="$DATA/ddg-gamemode-chrome"; mkdir -p "$D"
    "$BROWSER" --user-data-dir="$D" --no-first-run --no-proxy-server --kiosk --app="$URL" >>"$DATA/gamemode-browser.log" 2>&1 & ;;
esac
BR=$!
trap 'kill $BR $SERVER 2>/dev/null; pkill -f ddg-gamemode-p 2>/dev/null; pkill -f ddg-gamemode-c 2>/dev/null' EXIT INT TERM
while kill -0 $SERVER 2>/dev/null && kill -0 $BR 2>/dev/null; do sleep 1; done
rm -f "$URLFILE"
'''.replace("__BROWSER__", browser).replace("__KIND__", kind).replace("__PREFS__", FIREFOX_PREFS)


def airlock_launcher_text() -> str:
    return r'''#!/bin/sh
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Deck Deck Goose - Airlock launcher, written by "Add to Game Mode".
# Opens a desktop in a window inside Game Mode; your game keeps running.
if [ -f /.flatpak-info ] && command -v flatpak-spawn >/dev/null 2>&1; then
  exec flatpak-spawn --host "$0" "$@"          # Steam itself is a Flatpak: run on the host
fi
DATA="$HOME/.local/share/deck-deck-goose"
cd "$DATA" || exit 1
THISPC_GAMEMODE=1 PYTHONPATH="$DATA/lib" exec python3 -m ddg airlock start >>"$DATA/airlock.log" 2>&1
'''


def steam_users(root: Path) -> List[Path]:
    return sorted(p for p in (root / "userdata").glob("*") if p.is_dir() and p.name.isdigit() and p.name != "0")


def _read_shortcuts(path: Path) -> dict:
    if not path.exists():
        return {"shortcuts": {}}
    data = vdf.loads_binary(path.read_bytes())
    if not isinstance(data.get("shortcuts", {}), dict):
        raise ValueError("no shortcuts block")
    data.setdefault("shortcuts", {})
    return data


def _write_shortcuts(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(str(path), str(path.with_name("%s%s-%s" % (path.name, BACKUP, stamp))))
        for extra in sorted(path.parent.glob(path.name + BACKUP + "-*"))[:-3]:
            extra.unlink()
    tmp = path.with_name(path.name + ".thispc-tmp")
    tmp.write_bytes(vdf.dumps_binary(data))
    os.replace(str(tmp), str(path))


def _exe(entry: dict) -> str:
    return str(entry.get("Exe", entry.get("exe", ""))) if isinstance(entry, dict) else ""


def _ours(entry: dict, tile: str = "ddg") -> bool:
    return TILES[tile][1] in _exe(entry)


def _legacy(entry: dict) -> bool:
    return LEGACY_LAUNCHER in _exe(entry)


def _entry(appid: int, tile: str = "ddg") -> Dict[str, object]:
    assets = core.data_dir() / "lib" / "ddg" / "assets"
    return {"appid": _signed(appid), "AppName": TILES[tile][0], "Exe": exe_field(tile),
            "StartDir": '"%s"' % core.data_dir(), "icon": str(assets / TILES[tile][3]),
            "ShortcutPath": "", "LaunchOptions": "", "IsHidden": 0, "AllowDesktopConfig": 1,
            "AllowOverlay": 1, "OpenVR": 0, "Devkit": 0, "DevkitGameID": "", "DevkitOverrideAppID": 0,
            "LastPlayTime": 0, "FlatpakAppID": "", "tags": {}}


def start_steam() -> None:
    if os.environ.get("THISPC_FAKE_TOOLS"):
        core.run(["steam", "-start"])
        return
    try:
        subprocess.Popen(["steam"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def status() -> Dict[str, object]:
    users = legacy = 0
    per = {k: 0 for k in TILES}
    for root, _flat in steam.steam_roots():
        for u in steam_users(root):
            users += 1
            try:
                items = list(_read_shortcuts(u / "config" / "shortcuts.vdf")["shortcuts"].values())
            except (OSError, ValueError):
                continue
            for k in TILES:
                per[k] += any(_ours(e, k) for e in items)
            legacy += any(_legacy(e) for e in items)
    b = find_browser()
    return {"installed": per["ddg"] > 0 and launcher_path().exists(), "users": users,
            "installed_users": per["ddg"], "airlock_installed": per["airlock"] > 0 and launcher_path("airlock").exists(),
            "legacy_tiles": legacy, "browser": b[0] if b else "", "in_game_mode": in_game_mode()}


def _with_steam_closed(name: str, fn) -> List[Step]:
    if in_game_mode():
        return [Step(name, False, "Do this once from Desktop Mode: in Game Mode, Steam is the screen and "
                     "cannot be closed to update its shortcut list.")]
    with steam.job_lock() as mine:
        if not mine:
            return [Step(name, False, steam.BUSY)]
        steps: List[Step] = []
        was_running = steam.steam_running()
        if was_running:
            st = steam.close_steam()
            steps.append(st)
            if not st.ok:
                return steps
        try:
            steps += fn()
        finally:
            if was_running:
                start_steam()
                steps.append(Step("Steam", True, "started again"))
        return steps


def _drop_legacy(items: dict, grid: Path) -> int:
    """Remove the old "This PC Games" entries (and their artwork) from one shortcuts block."""
    gone = 0
    for key in [k for k, e in items.items() if _legacy(e)]:
        e = items.pop(key)
        old_id = shortcut_appid(_exe(e), str(e.get("AppName", e.get("appname", LEGACY_NAME))))
        for suffix in ART:
            try:
                (grid / ("%d%s" % (old_id, suffix))).unlink()
            except OSError:
                pass
        gone += 1
    return gone


def install(tiles: Tuple[str, ...] = ("ddg", "airlock")) -> List[Step]:
    def work() -> List[Step]:
        steps: List[Step] = []
        want = [t for t in tiles if t in TILES]
        if "ddg" in want:
            b = find_browser()
            if not b:
                steps.append(Step("Browser", False, "No browser found to show the page in Game Mode, so the Deck Deck "
                                  "Goose tile was skipped. Install Firefox (Bazzite: Bazaar or Discover, it is a "
                                  "Flatpak) and try again."))
                want.remove("ddg")
            else:
                steps.append(Step("Browser for Game Mode", True, b[0]))
        if not want:
            return steps
        steps.append(core.copy_program())
        for t in want:
            launcher_path(t).write_text(launcher_text(*b) if t == "ddg" else airlock_launcher_text())
            os.chmod(str(launcher_path(t)), 0o755)
            steps.append(Step("Launcher: " + TILES[t][0], True, str(launcher_path(t))))
        assets = core.data_dir() / "lib" / "ddg" / "assets"
        done = 0
        roots = steam.steam_roots()
        for root, flat in roots:
            users = steam_users(root)
            if not users:
                steps.append(Step("Steam account", False, "No Steam account folder in %s. Log in to Steam once, "
                                  "then try again." % root))
            for u in users:
                f = u / "config" / "shortcuts.vdf"
                try:
                    data = _read_shortcuts(f)
                except ValueError as e:
                    steps.append(Step("Steam shortcuts (account %s)" % u.name, False,
                                      "Steam's shortcut list could not be read (%s); left untouched." % e))
                    continue
                items = data["shortcuts"]
                grid = u / "config" / "grid"
                old = _drop_legacy(items, grid)
                if old:
                    steps.append(Step("Old tile (account %s)" % u.name, True,
                                      "replaced '%s' with the new name" % LEGACY_NAME))
                for t in want:
                    key = next((k for k, e in items.items() if _ours(e, t)), None)
                    if key is None:
                        n = 0
                        while str(n) in items:
                            n += 1
                        key = str(n)
                    items[key] = _entry(shortcut_appid(exe_field(t), TILES[t][0]), t)
                _write_shortcuts(f, data)
                back = _read_shortcuts(f)["shortcuts"].values()
                grid.mkdir(parents=True, exist_ok=True)
                for t in want:
                    appid = shortcut_appid(exe_field(t), TILES[t][0])
                    for suffix, src in TILES[t][2].items():
                        shutil.copy2(str(assets / src), str(grid / ("%d%s" % (appid, suffix))))
                    ok = any(_ours(e, t) for e in back)
                    steps.append(Step("Steam shortcut '%s' (account %s)" % (TILES[t][0], u.name), ok,
                                      "added with its artwork" if ok else "could not read it back"))
                    done += ok
                if flat:
                    steps.append(Step("Flatpak Steam", True, "Steam is a Flatpak here; the tiles run the program "
                                      "outside Steam's sandbox (not tested on Flatpak Steam yet)."))
        if not roots:
            steps.append(Step("Steam", False, "Steam was not found. Start Steam once, then try again."))
        if done:
            (core.data_dir() / MARK).write_text("Game Mode tiles installed\n")
            legacy_dir = home_old_data()
            if legacy_dir.is_symlink():
                try:
                    legacy_dir.unlink()
                except OSError:
                    pass
            try:
                (core.data_dir() / LEGACY_LAUNCHER).unlink()
            except OSError:
                pass
            msg = "Switch to Game Mode: Library > Non-Steam > " + " and ".join(TILES[t][0] for t in want) + "."
            if "ddg" in want:
                msg += " The touchscreen works; for buttons, pick Steam's 'Web Browser' controller layout for the tile."
            if "airlock" in want:
                msg += " For Airlock, set its controller layout to 'Gamepad with Mouse Trackpad' (Desktop)."
            steps.append(Step("Next", True, msg))
        return steps
    return _with_steam_closed("Add to Game Mode", work)


def home_old_data() -> Path:
    return core.home() / ".local" / "share" / core.OLD_DIR


def remove(tiles: Tuple[str, ...] = ("ddg", "airlock")) -> List[Step]:
    def work() -> List[Step]:
        steps: List[Step] = []
        want = [t for t in tiles if t in TILES]
        for root, _flat in steam.steam_roots():
            for u in steam_users(root):
                f = u / "config" / "shortcuts.vdf"
                try:
                    data = _read_shortcuts(f)
                except ValueError as e:
                    steps.append(Step("Steam shortcuts (account %s)" % u.name, False, str(e)))
                    continue
                items = data["shortcuts"]
                grid = u / "config" / "grid"
                keep = [e for e in items.values()
                        if not (any(_ours(e, t) for t in want) or _legacy(e))]
                if len(keep) != len(items):
                    for e in items.values():
                        if _legacy(e):
                            _drop_legacy({"x": e}, grid)
                    data["shortcuts"] = {str(i): e for i, e in enumerate(keep)}
                    _write_shortcuts(f, data)
                    steps.append(Step("Steam shortcut (account %s)" % u.name, True, "removed"))
                for t in want:
                    appid = shortcut_appid(exe_field(t), TILES[t][0])
                    for suffix in TILES[t][2]:
                        try:
                            (grid / ("%d%s" % (appid, suffix))).unlink()
                        except OSError:
                            pass
        for t in want:
            try:
                launcher_path(t).unlink()
            except OSError:
                pass
        if not any(launcher_path(t).exists() for t in TILES):
            try:
                (core.data_dir() / MARK).unlink()
            except OSError:
                pass
        steps.append(Step("Game Mode tiles", True, "removed: " + ", ".join(TILES[t][0] for t in want)))
        return steps
    return _with_steam_closed("Remove from Game Mode", work)
