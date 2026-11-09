# Changelog

## 0.6.3-wip - 2026-11-08
- **Fix (real ROG Ally, Game Mode): the Deck Deck Goose tile opened the browser, which said the page needed the internet.** The page is served on the handheld itself (127.0.0.1), but Firefox (a Flatpak) found no network-status service in Game Mode, switched itself to offline mode and refused even local pages. The tile's Firefox profile now turns automatic offline mode, connectivity checks and proxies off, and the launcher rewrites these settings every time (old profiles kept the old ones). Chromium-type browsers start with `--no-proxy-server`. Browser output goes to `~/.local/share/deck-deck-goose/gamemode-browser.log`. Not yet confirmed on the Ally.
- **One-step install:** new `DeckDeckGoose-Install-Linux.sh`, an **Install / Update / Repair** card at the top of the window, and `ddg install-app` / `uninstall-app`. They copy the program (and the bundled Zombie-Deck) to `~/.local/share/deck-deck-goose`, add **Deck Deck Goose** to the app menu and add both Game Mode tiles. No password; Desktop Mode only.
- Job progress now shows above the tabs, so it is visible from any tab.

## 0.6.2-wip - 2026-11-08
- **Smaller download (75 MB to well under 30 MB).** The PDF manual now stores screenshots as 1000-pixel JPEGs and is packed with qpdf (14 MB to about 1.3 MB). Game Mode tile artwork is saved as 256-colour PNGs (about a quarter of the size, same look on the handheld). The git history in the release zip keeps only the last three builds (0.6.0, 0.6.1, 0.6.2); every build, with its main code changes, is logged in [docs/HISTORY.md](docs/HISTORY.md) by the new `tools/history_log.py`.

## 0.6.1-wip - 2026-11-08
- **Fix (first real test on a ROG Ally): Airlock left a second desktop running that could only be closed by logging out.** When the full desktop counted as failed, programs it had started (Plasma shell, window manager) kept running on Game Mode's display, outside Airlock's control, and the simple desktop then opened on top. Airlock now snapshots desktop programs and Plasma user services before starting and closes anything new afterwards: between tries, on close, on *Back to Game* and on Steam's *Exit game*. In Game Mode a leftover Plasma shell from an earlier run is closed when Airlock next starts. In Desktop Mode only what Airlock started is touched.
- New **Close leftovers** button on the Airlock tab and `ddg airlock cleanup`. New log code `ADST`.

## 0.6.0-wip - 2026-11-07
- **Steam library manager on Windows and macOS.** The Games tab (find Steam, show games, move between drives, clear junk) now runs on all three systems; Linux-only features (This PC folder, Game Mode tiles, keyboard, Sound, Airlock, Wake) are hidden there. Windows: registry Steam discovery, drives, NTFS compression with a choice of XPRESS4K / 8K / 16K / LZX and Uncompress, DirectStorage warning, long paths. macOS: Steam in Library/Application Support, volumes from `mount`, exFAT/FAT/NTFS/network/read-only targets refused with reasons, compression off with an explanation. Engine ported from GameShelf (`src/ddg/shelf/`), chosen by `src/ddg/library.py`. `DeckDeckGoose-Windows.bat` and `DeckDeckGoose-Mac.command` now start the app. Simulated test modes: `DDG_PLATFORM`, `DDG_FAKE_WIN`, `DDG_FAKE_MAC`, `DDG_MOUNTS_FILE`. New `ddg drives`, `--algo`, `uncompress-game`.
- **Zombie-Deck bundled** (v0.3.1, `components/zombie-deck/`, unedited) with a new **Wake tab**: status band from `zombie-deck json` (green / red / amber / grey), Install / Update / Repair, Turn on/off, Show log, Remove (keeps its settings file). Never installs over a newer version.
- **Fix: Airlock's sleep hook never ran.** systemd does not read `/etc/systemd/system-sleep/`. It is now installed as `/etc/zombie-deck/sleep.d/40-ddg-airlock` when Zombie-Deck is present, otherwise as a small `sleep.target` unit; turning it off also removes the old file.
- Window and Airlock/Steam modules import without `fcntl` (Windows).
- 70 new tests for Windows/Mac/no-Linux-modules and Wake; the bundled Zombie-Deck's own 35 pass.

## 0.5.0-wip - 2026-11-07
- **Renamed to Deck Deck Goose** (was *This PC for Linux*). Command: `ddg` / `python3 -m ddg`. Launchers: `DeckDeckGoose-Linux.sh` (and the Mac/Windows notices). Repo and folder: `deck-deck-goose`.
- **Carries your old install over** automatically: settings and data move from `this-pc-linux` to `deck-deck-goose` (the old folder stays as a link), the refresh timer and sound service are replaced by `ddg-refresh` / `ddg-sound`, a `thispc` compatibility shim keeps old launchers working, and the old *This PC Games* tile is replaced when you press *Add to Game Mode* again. Backup file names ending `.thispc-backup` are unchanged on purpose.
- **New look:** the neon cyborg-goose artwork and palette (navy, orange, cyan) across the window, Steam tiles, icons, README and website. Pictures are generated from one source image by `tools/make_art.py`.
- **Airlock** (merged from the separate Airlock workbench): a second Game Mode tile that opens a KDE desktop in a window inside Game Mode while your game keeps running. Fallback ladder full > lite > real Desktop Mode (asks first) > instructions; never reboots. Watchdog, single-instance lock, RAM and battery checks, logged short codes (A0OK to ACDP), colour bar (amber / blue / green) with mode-change hooks, optional sleep hook, *Back to Game* menu entry, read-only `probe`. New **Airlock tab** and `ddg airlock ...` commands. **Not tested on real hardware.**
- *Add to Game Mode* now adds both tiles; *Remove tiles* removes both.
- PDF manual (`docs/DeckDeckGoose-manual.pdf`, built by `tools/make_pdf.py`) attached to every release.
- Fix: Airlock start works from a background thread (signal handlers are only set on the main thread).
- 128 tests (22 new for Airlock, migration and tiles).

## 0.4.0-wip - 2026-11-06
- **New Sound tab** (also shown in Game Mode, under the games). It fixes the quiet ROG Ally speakers on Bazzite. Bazzite's "ROG Ally" output is sound processing (an EQ) that plays through the "Family 17h/19h/1ah" speakers, and the two volumes multiply. **Fix quiet sound** sets the speakers underneath to 100% and unmutes them, and keeps your slider and the EQ.
- **Volume boost** 100 / 115 / 130 / 150%, with KDE's slider allowed to 150%. **Use this** switches the sound output. Optional **re-apply at every login** service. **Back to normal** undoes it. Also available as `thispc sound ...` on the command line.
- *Remove everything* keeps the program copy while the sound service is installed.
- 8 new tests (106 in total).

## 0.3.1-wip - 2026-11-04
- **Fix: "keyboard not installed" on Nobara KDE** (reported on a ROG Ally running Nobara Handheld). Fedora-based systems now ship KDE's new Plasma Keyboard (`plasma-keyboard`) instead of Maliit; 0.3.0 only looked for Maliit. Plasma Keyboard is now found first (Maliit still works), including Flatpak installs.
- **Install keyboard** button (and `thispc osk install`) when no touch keyboard is installed: uses `pkexec` with dnf / rpm-ostree / apt / zypper / pacman, so KDE asks for your password. It never stores or sees the password. Bazzite needs a restart afterwards. Refused in Game Mode.
- *Turn on* also sets `VirtualKeyboardEnabled=true` and uses `kwriteconfig6 --notify` so KWin notices straight away.
- 4 new tests (98 in total); the Plasma Keyboard test fails on 0.3.0, reproducing the report.

## 0.3.0-wip - 2026-11-04
- **Game Mode tile:** *Add to Game Mode* (Games tab, or `thispc gamemode add`) adds "This PC Games" to Steam's library as a non-Steam shortcut with its own artwork, for every Steam account on the PC (Steam is closed and restarted for it; backups of `shortcuts.vdf` kept). In Game Mode it opens the Games tab full screen in a browser in kiosk mode, with bigger touch-friendly buttons and *Back to Steam*. Moving is left to Steam's own Settings > Storage there.
- **Works with Steam open:** Estimate and Compress (Compress refuses a game that is running or has files open), and the junk items that are safe while Steam runs (orphaned shader caches, unfinished copies, and the unticked leftovers). Other junk asks for Steam to be closed.
- **On-screen keyboard for KDE Desktop Mode:** *Turn on / Turn off* (This PC tab, or `thispc osk on|off`) sets KDE's touch keyboard (Maliit) as the virtual keyboard in `kwinrc`, reloads KWin, and adds a *Show on-screen keyboard* launcher.
- **Safety fixes ported from GameShelf's independent review:** per-thread move lock (two copies could both be deleted), no new move while an earlier one is unresolved, apps sharing one folder are not moved, crash recovery tidies up properly and updates Steam's list, the old copy is only deleted after re-checking the new one, Steam started mid-move undoes the move, honest cancelling, strict library-list reads with three dated backups, clean Ctrl+C on the command line, frame protection and safer token checks in the window. A copied game's compression record is dropped.
- The window waits 15 minutes (not 90 seconds) before stopping when its tab is idle, and says so if you come back later.
- The program copy in `~/.local/share/this-pc-linux` is kept while the Game Mode tile is installed, even if *Remove everything* is pressed on the This PC tab.
- 25 new tests (94 in total), including a real end-to-end run of the Game Mode launcher.

## 0.2.1-wip - 2026-11-03
- **Estimate** button (and `estimate-game`): a read-only check of how much btrfs compression would save, from small samples of each file (64 MB cap per game), following btrfs's 128 KiB / 4 KiB rule. It skips media and archive formats and detects parts the drive has already compressed (FIEMAP). Result shown on the game card with a verdict.
- **Compress** now compresses only the files that would shrink by 3% or more; videos, audio, images, archives and already-compressed files are skipped, which is much faster. Compress unlocks after an Estimate that isn't "not worth it". Refused for games that need an update.
- Edge-case hardening:
  - Manifests with unsafe or empty folder names can no longer point a move, compress or clean outside the game (an empty name used to mean the whole `common` folder).
  - Manifests whose file name and id disagree are ignored; duplicate installs are refused.
  - Targets on RAM disks (e.g. an unmounted drive's empty folder), read-only, network or system mounts, missing parents, `steamapps` folders and unwritable places are refused.
  - Read-only game folders are now removed after a copy.
  - A broken library list stops moves with a clear message; a missing one is created with Steam's own folder as entry 0.
  - Junk that is a symlink only has the link removed.
  - A lock stops two windows from changing Steam files at once, and recovery never touches another window's move in progress.
- Fixed: pressing Install or Remove everything on the This PC tab erased the Games tab's saved estimates and compression records. Settings are now merged and written atomically.
- 29 new tests (69 in total).

## 0.2.0-wip - 2026-11-03
- New **Games** tab: a Steam game manager.
  - Finds native and Flatpak Steam, every library, and every installed game; Proton and runtimes hidden by default.
  - Shows the game's artwork from Steam's local cache (new per-game folders, old flat files, and custom grid art). Nothing is downloaded.
  - Moves a game with its Proton prefix (saves), shader cache and workshop items to another library or drive: copy, compare every file, register the library in both `libraryfolders.vdf` copies if needed, then delete the old copy. Same-drive moves are an instant rename. Journal-based recovery after a crash. Refuses while Steam runs, for games not fully installed, and for exFAT/FAT/NTFS targets.
  - Flatpak Steam gets access to the new drive automatically.
  - Junk scan and clean (orphaned shader caches, download leftovers, web cache, logs, unfinished copies; leftover game folders and Proton prefixes listed but not ticked). Prefixes of non-Steam shortcuts are never listed.
  - btrfs zstd compression per game, plus compression for future updates.
  - Close Steam button; progress bar and Cancel for long jobs.
- Command line: `games`, `targets`, `move-game`, `junk [--clean]`, `compress-game`, `close-steam`.
- Window: tabs, shorter labels, folder column hidden on narrow screens.
- Tested on simulated data, plus one real cross-file-system move onto tmpfs. Not yet tested with a real Steam client or real btrfs.

## 0.1.0-wip - 2026-10-31
- First work-in-progress release.
- Builds a "This PC" folder of shortcuts: Disks (by label), Users, Documents, Downloads, Pictures, Music, Videos, Desktop.
- Refreshes it every 30 seconds with a systemd user timer.
- Adds a sidebar entry for GTK file managers (Nemo, Thunar, Nautilus) and Dolphin.
- Optional Flatpak access for chosen file managers (`flatpak override --user`).
- Browser-based window, plus `install`, `check`, `refresh`, `uninstall`, `disks` on the command line.
- Tested with simulated disks and a stand-in for flatpak/systemctl only; not yet run on a real Bazzite or Mint machine.
