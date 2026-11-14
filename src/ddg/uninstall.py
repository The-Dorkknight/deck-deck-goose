# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Deleting games.

Two cases, kept apart on purpose:
* **A duplicate copy** (the same game installed in two Steam libraries, which blocks Move and Compress).
  Deck Deck Goose deletes the copy you pick itself: its manifest, its game folder, its shader cache and its
  workshop files in that library. Steam must be closed. Its Proton prefix (compatdata, where saves can
  live) is kept. The other copy is never touched.
* **The only copy.** Deck Deck Goose does not delete it itself: it opens Steam's own uninstaller
  (steam://uninstall/<appid>), which asks you to confirm and handles everything Steam knows about.
Works with either backend (ddg.steam on Linux, ddg.shelf.steam on Windows and macOS).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import List, Optional

from . import core, library
from .core import Step


def _b():
    return library.backend()


def copies(appid: str) -> list:
    return [g for g in _b().list_games() if g.appid == str(appid)]


def find_copy(appid: str, lib: str) -> Optional[object]:
    want = os.path.realpath(str(lib))
    return next((g for g in copies(appid) if os.path.realpath(str(g.library.path)) == want), None)


def _manifest_backup_dir() -> Path:
    d = core.config_dir() / "deleted-manifests"
    d.mkdir(parents=True, exist_ok=True)
    return d


def check_delete_copy(game) -> Optional[str]:
    """None if this copy may be deleted, otherwise the reason in plain words."""
    b = _b()
    others = [g for g in copies(game.appid) if os.path.realpath(str(g.manifest)) != os.path.realpath(str(game.manifest))]
    if not others:
        return "This is the only copy. Use Uninstall, which opens Steam's own uninstaller."
    if b.steam_running():
        return "Close Steam first (Steam keeps its game list in memory and would bring the copy back)."
    in_use = getattr(b, "game_in_use", None)
    if in_use and in_use(game):
        return "Something is running from this copy's folder. Quit the game first."
    mine = os.path.realpath(str(game.dir))
    if any(os.path.realpath(str(g.dir)) == mine for g in others):
        return "Both copies point at the same folder (a link). Deleting would remove the other copy too; refused."
    if not b._within(game.manifest, game.library.steamapps):
        return "The manifest is outside this library's steamapps folder; refused."
    return None


def delete_copy(game, job) -> List[Step]:
    b = _b()
    with b.job_lock() as held:
        if not held:
            return [Step("Delete copy", False, getattr(b, "BUSY", "Another job is running."))]
        why = check_delete_copy(game)
        if why:
            return [Step("Delete copy", False, why)]
        sa = game.library.steamapps
        steps: List[Step] = []
        # 1. the manifest first: from then on Steam no longer sees this copy, even if the rest is interrupted
        backup = _manifest_backup_dir() / ("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), game.manifest.name))
        try:
            shutil.copy2(str(game.manifest), str(backup))
            os.unlink(str(game.manifest))
            steps.append(Step("Steam's record of this copy", True, "removed (a copy is kept in %s)" % backup))
        except OSError as e:
            return [Step("Steam's record of this copy", False, "could not remove it: %s. Nothing else was changed." % e)]
        # 2. the library list (Steam rebuilds it anyway; this keeps it tidy)
        try:
            data = b.read_libraryfolders(game.library.root, strict=True)
            here = os.path.realpath(str(game.library.path))
            for entry in data.get("libraryfolders", {}).values():
                if isinstance(entry, dict) and os.path.realpath(str(entry.get("path", ""))) == here:
                    (entry.get("apps") or {}).pop(game.appid, None)
            b.write_libraryfolders(game.library.root, data)
            steps.append(Step("Steam library list", True, "updated"))
        except Exception as e:  # noqa: BLE001 - Steam fixes this list itself on next start
            steps.append(Step("Steam library list", True, "left as it is (%s); Steam corrects it on next start" % e))
        # 3. the files of this copy, in this library only
        shared = b.shares_folder(game)
        rels = []
        if b.safe_name(game.installdir) and not shared:
            rels.append("common/" + game.installdir)
        rels += ["shadercache/" + game.appid, "workshop/content/" + game.appid, "workshop/appworkshop_%s.acf" % game.appid]
        targets = [sa / r for r in rels if os.path.lexists(str(sa / r))]
        total = sum(b.tree_size(p)[0] for p in targets)
        job.progress(0, total, "Deleting")
        freed = 0
        for p in targets:
            if job.cancel_event.is_set():
                steps.append(Step("Stopped", False, "Cancelled. Steam's record is already gone; delete the rest with Clean junk."))
                break
            if not b._within(p if not os.path.islink(str(p)) else p.parent, sa):
                steps.append(Step(str(p), False, "refused: outside this library"))
                continue
            size = b.tree_size(p)[0]
            try:
                b._rm(p)
                freed += size
                steps.append(Step(p.relative_to(sa).as_posix(), True, "removed %s" % core.human_size(size)))
            except OSError as e:
                steps.append(Step(p.relative_to(sa).as_posix(), False, str(e)))
            job.progress(freed)
        if shared:
            steps.append(Step("Game folder", True, "kept: %s also use it" % ", ".join(shared)))
        if os.path.lexists(str(sa / "compatdata" / game.appid)):
            steps.append(Step("Proton prefix (compatdata/%s)" % game.appid, True, "kept: it can hold save games"))
        steps.append(Step("Total", True, "freed about %s. The other copy is untouched." % core.human_size(freed)))
        return steps


def open_steam_uninstaller(appid: str) -> Step:
    """Hands the uninstall to Steam (it asks to confirm). Never deletes anything itself."""
    if not str(appid).isdigit():
        return Step("Uninstall", False, "bad game id")
    url = "steam://uninstall/%s" % appid
    fake = os.environ.get("DDG_FAKE_OPEN")
    if fake:
        with open(fake, "a") as f:
            f.write(url + "\n")
        return Step("Uninstall", True, "asked Steam to uninstall (test)")
    if library.is_linux():
        for cmd in (["steam", url], ["xdg-open", url], ["flatpak", "run", "com.valvesoftware.Steam", url]):
            if shutil.which(cmd[0]):
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                    return Step("Uninstall", True, "Steam's uninstaller should appear (confirm there). If Steam was closed it starts first.")
                except OSError:
                    continue
        return Step("Uninstall", False, "Could not reach Steam. In Steam: right-click the game > Manage > Uninstall.")
    try:
        webbrowser.open(url)
        return Step("Uninstall", True, "Steam's uninstaller should appear (confirm there).")
    except Exception:  # noqa: BLE001
        return Step("Uninstall", False, "Could not reach Steam. In Steam: right-click the game > Manage > Uninstall.")
