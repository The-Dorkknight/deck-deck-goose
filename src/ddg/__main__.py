# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Command line: python3 -m ddg [command]  (no command opens the window)"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __build_date__, __version__, core
from .jobs import Job, run_now


def _print_steps(steps: List[core.Step]) -> int:
    bad = 0
    for s in steps:
        print("  [%s] %s: %s" % ("ok" if s.ok else "!!", s.name, s.detail))
        bad += 0 if s.ok else 1
    return 1 if bad else 0


def _progress(job: Job) -> None:
    pct = " %3d%%" % (100 * job.done // job.total) if job.total else ""
    print("\r  %s%s   " % (job.message[:70], pct), end="", flush=True)


def _run_job(title: str, fn) -> int:
    """Run in a worker thread so Ctrl+C asks the job to stop cleanly (and roll
    back) instead of killing it half-way."""
    import threading
    job = Job(title, on_progress=_progress)
    print(title)
    t = threading.Thread(target=run_now, args=(job, fn))
    t.start()
    while t.is_alive():
        try:
            t.join(0.5)
        except KeyboardInterrupt:
            job.cancel_event.set()
            print("\n  Stopping safely, please wait...")
    print()
    if job.state == "cancelled":
        print("  Cancelled. Nothing was changed.")
        return 1
    return _print_steps(job.steps)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="ddg",
        description="Deck Deck Goose: a logical, Windows-style This PC folder "
                    "(disks, Users, Documents...) for Linux file managers, plus a Steam game manager.")
    p.add_argument("--version", action="version",
                   version="Deck Deck Goose %s (Built %s)" % (__version__, __build_date__))
    sub = p.add_subparsers(dest="cmd")
    g = sub.add_parser("gui", help="open the window in your browser (default)")
    g.add_argument("--no-browser", action="store_true", help="only print the address")
    g.add_argument("--url-file", default="", help=argparse.SUPPRESS)
    sub.add_parser("install-app", help="install on this computer: program copy, app-menu entry and Game Mode tiles")
    sub.add_parser("uninstall-app", help="remove the app-menu entry and the Game Mode tiles")
    gmp = sub.add_parser("gamemode", help="add or remove the Deck Deck Goose and Airlock tiles in Steam's Game Mode")
    gmp.add_argument("action", choices=["add", "remove", "status"])
    al = sub.add_parser("airlock", help="open a desktop in a window inside Game Mode (game keeps running)")
    al.add_argument("action", choices=["start", "stop", "status", "plan", "probe", "sleep-hook", "back-entry", "cleanup", "pad"])
    al.add_argument("value", nargs="?", default="", help="sleep-hook: on|off; probe: --live; pad: --watch")
    al.add_argument("--force", action="store_true", help="start: allow it from Desktop Mode (to try it out)")
    al.add_argument("--live", action="store_true", help="probe: also open a 10-second test window")
    kb = sub.add_parser("osk", help="KDE on-screen keyboard for Desktop Mode")
    kb.add_argument("action", choices=["on", "off", "status", "install"])
    sp = sub.add_parser("sound", help="fix quiet speakers (ROG Ally on Bazzite) and boost the volume")
    sp.add_argument("action", choices=["status", "fix", "boost", "use", "keep", "apply", "reset"])
    sp.add_argument("value", nargs="?", default="",
                    help="boost: percent (10-150); use: output name; keep: on or off")
    i = sub.add_parser("install", help="create the folder, timer, bookmarks, Flatpak access")
    i.add_argument("--name", default=core.DEFAULT_ROOT_NAME, help="folder name in your home")
    i.add_argument("--app", action="append", default=[], metavar="FLATPAK_ID",
                   help="Flatpak file manager to give disk access (repeatable)")
    i.add_argument("--all-file-managers", action="store_true",
                   help="give access to every Flatpak file manager found")
    sub.add_parser("check", help="verify everything is in place")
    r = sub.add_parser("refresh", help="re-scan disks and update the folder")
    r.add_argument("--quiet", action="store_true")
    sub.add_parser("uninstall", help="remove everything this tool added")
    sub.add_parser("disks", help="list the disks it can see")
    gm = sub.add_parser("games", help="list Steam games")
    gm.add_argument("--tools", action="store_true", help="include Proton and runtimes")
    t = sub.add_parser("targets", help="where a game can be moved to")
    t.add_argument("appid")
    m = sub.add_parser("move-game", help="move a Steam game (and its saves) to another library or drive")
    m.add_argument("appid")
    m.add_argument("target", help="library folder, e.g. /run/media/you/Games/SteamLibrary")
    m.add_argument("--allow-ntfs", action="store_true", help="allow an NTFS target (not recommended)")
    j = sub.add_parser("junk", help="list Steam junk; --clean removes the safe (ticked) items")
    j.add_argument("--clean", action="store_true")
    from . import library
    algos = library.algorithms()
    sub.add_parser("drives", help="list drives and whether they can hold a Steam library (Windows and Mac)")
    for name, helptext in (("estimate-game", "read-only: how much would compressing a game save?"),
                           ("compress-game", "compress a game (btrfs zstd on Linux, Windows compression on Windows)"),
                           ("uncompress-game", "store a game uncompressed again (Windows)")):
        c = sub.add_parser(name, help=helptext)
        c.add_argument("appid")
        if algos and name != "uncompress-game":
            c.add_argument("--algo", default=algos[1] if len(algos) > 1 else algos[0], choices=algos)
    sub.add_parser("close-steam", help="ask Steam to exit")
    a = p.parse_args(argv)
    core.MIGRATION = core.migrate_old() if (a.cmd != "version" and library.is_linux()) else []
    if core.MIGRATION and a.cmd not in (None, "gui"):
        print("Moved over from the old name (This PC for Linux):")
        _print_steps(core.MIGRATION)

    if a.cmd in (None, "gui"):
        from . import webui
        return webui.serve(open_browser=not getattr(a, "no_browser", False),
                           url_file=getattr(a, "url_file", ""))
    if a.cmd in ("install-app", "uninstall-app"):
        if not library.is_linux():
            print("Installing is for Linux (Bazzite, Nobara, Mint). On Windows and Mac just double-click the launcher.")
            return 1
        from . import appinstall
        return _print_steps(appinstall.install() if a.cmd == "install-app" else appinstall.uninstall())
    if a.cmd == "gamemode":
        from . import gamemode
        if a.action == "status":
            for k, v in gamemode.status().items():
                print("%-16s %s" % (k, v))
            return 0
        return _print_steps(gamemode.install() if a.action == "add" else gamemode.remove())
    if a.cmd == "airlock":
        import json as _json
        from . import airlock
        if a.action == "start":
            r = airlock.start(force=a.force)
            print(_json.dumps(r))
            return 0 if r["code"] in ("A0OK", "A6LK") else 1
        if a.action in ("stop", "status", "plan", "cleanup"):
            print(_json.dumps(getattr(airlock, a.action)(), indent=1))
            return 0
        if a.action == "probe":
            ok, text = airlock.probe(live=a.live or a.value == "--live")
            print(text)
            return 0 if ok else 1
        if a.action == "back-entry":
            return _print_steps([airlock.write_back_launcher()])
        if a.action == "pad":
            from . import padbridge
            if a.value == "--watch":
                return padbridge.watch()
            print(_json.dumps(padbridge.check(), indent=1))
            return 0
        if a.value not in ("on", "off"):
            p.error("airlock sleep-hook needs on or off")
        return _print_steps(airlock.sleep_hook(a.value == "on"))
    if a.cmd == "osk":
        from . import osk
        if a.action == "status":
            for k, v in osk.status().items():
                print("%-10s %s" % (k, v))
            return 0
        if a.action == "install":
            return _print_steps(osk.install_keyboard())
        return _print_steps(osk.enable() if a.action == "on" else osk.disable())
    if a.cmd == "sound":
        from . import sound
        if a.action == "status":
            st = sound.status()
            if st["error"]:
                print("  " + st["error"])
                return 1
            for s in st["sinks"]:
                print("  %s %-60s %4d%%%s" % ("*" if s["name"] == st["default"] else " ", s["desc"][:60], s["volume"],
                                             "  [sound processing]" if s["processing"] else ""))
                print("      %s" % s["name"])
            print("  fix needed: %s" % ("yes" if st["can_fix"] and not st["fixed"] else "no"))
            return 0
        if a.action == "boost":
            if not a.value.isdigit():
                p.error("sound boost needs a percent, e.g. ddg sound boost 130")
            return _print_steps(sound.boost(int(a.value)))
        if a.action == "use":
            return _print_steps(sound.use_output(a.value))
        if a.action == "keep":
            if a.value not in ("on", "off"):
                p.error("sound keep needs on or off")
            return _print_steps(sound.keep(a.value == "on"))
        return _print_steps({"fix": sound.fix_quiet, "apply": sound.apply_saved, "reset": sound.reset}[a.action]())
    if a.cmd == "disks":
        names = core.display_names(core.list_disks())
        for d in core.list_disks():
            print("%-34s %-8s %-6s %s" % (names.get(d.dev, d.dev), core.human_size(d.size),
                                         d.fstype, d.mountpoint or "(not mounted)"))
        return 0
    if a.cmd == "refresh":
        try:
            stats, _ = core.refresh()
        except (RuntimeError, OSError) as e:
            print("refresh failed: %s" % e, file=sys.stderr)
            return 1
        if not a.quiet:
            print(stats)
        return 0
    if a.cmd == "install":
        apps = list(a.app)
        if a.all_file_managers:
            apps += [m for m in core.find_file_managers() if m not in apps]
        return _print_steps(core.install(a.name, apps))
    if a.cmd == "check":
        return _print_steps(core.check())
    if a.cmd == "uninstall":
        return _print_steps(core.uninstall())

    steam = library
    if a.cmd == "drives":
        if library.is_linux():
            print("Use: ddg disks")
            return 0
        from .shelf import plat
        for d in plat.drives():
            ok, why = steam.fs_ok_for_games(plat.volume_of(d.root))
            print("%-28s %-6s %-9s %s" % (d.name, d.fstype, d.kind, "ok" if ok else "-- " + why))
        return 0
    if a.cmd == "close-steam":
        return _print_steps([steam.close_steam()])
    if a.cmd == "games":
        _print_steps(steam.recover_moves())
        games = [x for x in steam.list_games() if a.tools or not x.tool]
        if not games:
            print("No Steam games found.")
        for x in games:
            state = "" if x.state == 4 else "  [%s]" % ", ".join(steam.decode_state(x.state))
            print("%-10s %-40s %9s  %s%s" % (x.appid, x.name[:40], core.human_size(x.size),
                                            x.library.path, state))
        return 0
    if a.cmd == "junk":
        items = steam.scan_junk()
        for d in items:
            print("[%s] %-9s %s\n      %s%s" % ("x" if d["default"] else " ", d["human"], d["label"],
                                             d["path"], ("\n      ! " + str(d["warning"])) if d["warning"] else ""))
        if not items:
            print("Nothing to clean.")
        if a.clean:
            ids = [str(d["id"]) for d in items if d["default"]]
            return _run_job("Cleaning junk", lambda job: steam.clean_junk(ids, job))
        print("\nRun with --clean to remove the [x] items. Unticked items are only removed from the window.")
        return 0
    game = steam.find_game(a.appid)
    if not game:
        print("No installed Steam game with id %s (see: ddg games)" % a.appid, file=sys.stderr)
        return 1
    if a.cmd == "targets":
        for t in steam.move_targets(game):
            print("%s %s%s" % ("ok" if t["ok"] else "--", t["label"], "" if t["ok"] else "  (%s)" % t["why"]))
        return 0
    if a.cmd == "move-game":
        if not library.is_linux():
            return _run_job("Moving %s" % game.name, lambda job: steam.move_game(game, a.target, job))
        return _run_job("Moving %s" % game.name,
                        lambda job: steam.move_game(game, a.target, job, a.allow_ntfs))
    extra = (a.algo,) if getattr(a, "algo", None) else ()
    if a.cmd == "estimate-game":
        return _run_job("Estimating %s" % game.name, lambda job: steam.estimate_game(game, job, *extra))
    if a.cmd == "compress-game":
        return _run_job("Compressing %s" % game.name, lambda job: steam.compress_game(game, job, *extra))
    if a.cmd == "uncompress-game":
        if library.is_linux():
            print("Not available on Linux (btrfs compression is undone by defragmenting without -c).")
            return 1
        return _run_job("Uncompressing %s" % game.name, lambda job: steam.uncompress_game(game, job))
    return 2


if __name__ == "__main__":
    sys.exit(main())
