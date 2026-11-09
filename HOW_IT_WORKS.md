# How it works (plain English)

## The This PC tab

**What it reads**
* The list of disks, by running `lsblk` (read only).
* Your standard folders from `~/.config/user-dirs.dirs`.
* The folder that holds home directories (for the *Users* list).
* Which Flatpak apps are installed (`flatpak list`, and the `.desktop` files Flatpak exports).

**What the This PC tab changes** (and only this)
1. Creates one folder in your home, `This PC` by default, containing **symlinks** (shortcuts). A marker file `.thispc-managed` says the folder is ours. If a folder with that name already exists and is not ours, it stops and changes nothing.
2. Adds one line to `~/.config/gtk-3.0/bookmarks`, and, if the file exists, one entry to `~/.local/share/user-places.xbel` (Dolphin), after saving a backup copy.
3. Copies its own small program to `~/.local/share/deck-deck-goose/` and installs a user-level systemd timer (`ddg-refresh.timer`) that re-scans disks every 30 seconds.
4. For Flatpak apps you tick: `flatpak override --user ...` (per-user permission override).
5. Saves your choices in `~/.config/deck-deck-goose/config.json`.

**What it never does**
* It never writes to the system part of the disk, never uses `sudo`, and never sends anything over the network. On the This PC tab it never deletes real files (it only removes symlinks it can see are symlinks). The Games tab deletes files only when you ask it to (the old copy after a checked move, junk you ticked).
* The window is a local web page bound to `127.0.0.1` only. Each request needs a random token and a local `Host` header, so other websites cannot drive it. The window closes itself about 90 seconds after the tab is closed.

**Which volumes count as disks**
Only mounted volumes whose mount point is under `/run/media/`, `/media/`, `/mnt/` or `/var/mnt/`. Everything else (`/`, `/boot`, `/var`, `/sysroot`...) is treated as the operating system and hidden. Unmounted disks with a normal file system (ext4, btrfs, xfs, ntfs, exfat, vfat...) of 1 GiB or more, or removable ones, are listed with a Mount button.

**Disk names**
The disk's label if it has one; otherwise `Unlabelled <size> (<device>)`. Duplicate names get `(2)`, `(3)`. Slashes are replaced by dashes.

**Remove everything**
Deletes the symlinks and marker, the bookmarks it added, the timer and the program copy, and runs `flatpak override --user --reset <app>` for apps you granted. If you had your own overrides for such an app, they are reset too.

**Test and demo switches** (environment variables, not needed in normal use):
`THISPC_LSBLK_JSON=<file>` reads disks from a file instead of `lsblk`; `THISPC_FAKE_TOOLS=1` replaces `flatpak`, `systemctl` and `udisksctl` with an in-memory stand-in.

## The Games tab (Steam)

**What it reads**: Steam's `libraryfolders.vdf` (to find libraries), each library's `steamapps/appmanifest_<id>.acf` (name, folder name, size, state), and artwork in `appcache/librarycache` and `userdata/*/config/grid`. Steam is looked for in `~/.local/share/Steam`, `~/.steam/...`, Flatpak (`~/.var/app/com.valvesoftware.Steam/...`) and Snap.

**Moving a game** (Steam closed, game fully installed, i.e. `StateFlags` is 4):
1. Works out what belongs to the game, inside `steamapps/`: `common/<installdir>`, `compatdata/<id>` (Proton prefix, holds Windows saves), `shadercache/<id>`, `workshop/content/<id>`, `workshop/appworkshop_<id>.acf`.
2. Same drive: renames them into the target library (instant). Other drive: copies each to `<name>.thispc-partial`, keeping permissions and symlinks, then compares the full file list (names, types, sizes, symlink targets) with the original. Any difference: the copy is deleted and the game is left alone.
3. Commit: adds the target library to Steam's list if it is new (both `config/libraryfolders.vdf` and `steamapps/libraryfolders.vdf`, with a one-time `.thispc-backup`; a `libraryfolder.vdf` marker is written in the new library), moves the appmanifest to the new `steamapps/`, and updates the `apps` lists.
4. Deletes the old copy.
5. A journal (`~/.config/deck-deck-goose/move-journal.json`) records each phase. If interrupted, the next start rolls back (if the new manifest was not yet written) or finishes (if it was). The old copy is never deleted while the new one is incomplete.

**Junk** is only ever deleted from inside Steam's own folders, only from the list the scan just produced, and only while Steam is closed. Prefixes with ids of 2147483648 and above belong to non-Steam shortcuts and are never listed.

**Estimate** (read-only): lists the game's files; skips files under 64 KiB (btrfs often stores those inline) and files whose extension is a compressed format (video, audio, images, archives); asks the kernel (FIEMAP) how much of each file the drive already stores compressed; then reads up to 16 pieces of 128 KiB from each remaining file, spread evenly, at most 64 MB in total per game. Each piece is compressed in memory (zstd on Python 3.14+, zlib as a stand-in before that), rounded up to whole 4 KiB blocks, and counted only if it saves at least one block, which is how btrfs decides whether to keep a piece compressed. Files sampled at under 3% saving are marked "not worth it". The verdict: *not worth it* below 200 MB or 3%, *small gain* below 10%, otherwise *worth it*. The result is saved in `config.json` and shown on the card.

**Compression** first repeats that check, then runs `btrfs filesystem defragment -czstd -- <files>` on the worthwhile files only, in batches, then `btrfs property set <game folder> compression zstd` so new files are compressed too. The space saved is estimated from the drive's free space before and after. Warning: on a drive with btrfs snapshots, compressing un-shares data with the snapshots and can use more space until old snapshots go.

**Safety checks** (also see the README): one job at a time across all windows (a lock file in `~/.config/deck-deck-goose/`); a manifest's folder name must be a single plain name; targets must be on a real, writable, non-RAM, non-network, non-read-only file system whose parent folder exists.

**Test switches** (not needed in normal use): `THISPC_MOUNTS_FILE` (fake mount table), `THISPC_ALLOW_ANY_FS=1` (allow e.g. tmpfs targets in tests), `THISPC_FORCE_COPY=1` (copy even on the same drive), `THISPC_FAKE_FREE_BYTES`, `THISPC_FAKE_STEAM_RUNNING=1`, `THISPC_FAKE_BTRFS_EPERM=1`.

## Game Mode tiles
* **Add to Game Mode** refuses to run inside Game Mode itself (Steam can't be closed there). From Desktop Mode it closes Steam if needed, keeps a copy of the program in `~/.local/share/deck-deck-goose/lib`, writes `~/.local/share/deck-deck-goose/ddg-gamemode.sh`, and for each Steam account folder (`userdata/<number>`, not `0`) adds one entry to `config/shortcuts.vdf` (Steam's binary format; the file is only rewritten if every part of it was understood; three dated backups kept). The tile artwork goes to `config/grid/` under the shortcut's id (CRC-32 of the quoted launcher path plus the name, with the top bit set). Steam is started again afterwards.
* **The launcher** starts the program with `--url-file`, waits for the address, then opens it with the browser found at setup time: Firefox (`--new-instance --profile <own profile> --kiosk`) or a Chromium-family browser (`--user-data-dir=<own folder> --kiosk --app=`). When either the browser or the program ends, the launcher ends the other. If Steam itself is a Flatpak, the launcher re-runs itself outside Steam's sandbox with `flatpak-spawn --host` (untested).
* **In Game Mode** (`THISPC_GAMEMODE=1`, set by the launcher, or a gamescope desktop) the page hides the This PC tab, the first-run notice and Move, and shows Back to Steam.
* **With Steam running**, Compress first checks that no program is running from, or has files open in, the game folder (it looks through `/proc` for your own programs).

## On-screen keyboard
* Looks for a KDE touch keyboard desktop file, preferring Plasma Keyboard (`org.kde.plasma.keyboard.desktop`) over Maliit (`com.github.maliit.keyboard.desktop`), in `/usr/share/applications`, `/usr/local/share/applications`, `~/.local/share/applications`, then the Flatpak export folders.
* **Install keyboard** (only shown when none is found; refused in Game Mode) runs `pkexec <package manager> install plasma-keyboard`, then `maliit-keyboard` if that fails. The package manager is `rpm-ostree` when `/run/ostree-booted` exists (Bazzite: restart needed), otherwise the first of `dnf` (Nobara, Fedora), `apt-get`, `zypper` (package `plasma6-keyboard`), `pacman`. Cancelling the password window stops it.
* Sets `[Wayland] InputMethod=<that file>` and `VirtualKeyboardEnabled=true` in `~/.config/kwinrc` (with `kwriteconfig6 --notify` if present, otherwise by editing just that line; a one-time backup `kwinrc.thispc-backup` is kept), then asks KWin to reload (`qdbus6`/`qdbus`/`gdbus ... reconfigure`).
* Adds `~/.local/share/applications/ddg-show-keyboard.desktop`, which asks KWin to show the keyboard over D-Bus (`org.kde.kwin.VirtualKeyboard`, property `active`); this may not work on every Plasma version.
* *Turn off* removes both settings and the launcher.
* Test switches: `THISPC_APPS_DIR`, `THISPC_FAKE_PKG=<dnf|rpm-ostree|...|empty>`, `THISPC_FAKE_PKG_RESULT=<exit code>[:<desktop file it creates>]`.

## Sound
* Reads the outputs with `pactl -f json list sinks` and `pactl get-default-sink` (PipeWire's PulseAudio layer). If `pactl` is missing it uses `pw-dump` for the list and `wpctl get-volume / set-volume / set-mute / set-default` by id.
* Sorts each output into: **hardware** (name starts `alsa_output.` or `device.api=alsa`), **built-in** (hardware on the PCI bus, `.pci-` in the name), **HDMI** (hdmi, displayport or "Radeon High Definition" in the name), **Bluetooth** (`bluez_output.`), and **sound processing** (anything else, e.g. Bazzite's ROG Ally filter-chain EQ).
* **Fix quiet sound** only does something when the output in use is a sound-processing one. Then it sets each built-in, non-HDMI hardware output to 100% and unmutes it. USB and Bluetooth devices are never touched, so a headset can't be made suddenly loud.
* **Volume** sets the output in use to 10-150% (`pactl set-sink-volume <name> 130%`). Above 100% it also writes `[General] RaiseMaximumVolume=true` to `~/.config/plasmaparc` (KDE's "Raise maximum volume", whose limit is 150%); at 100% or below it removes that line again. Other lines are kept.
* Choices are saved under `"sound"` in `config.json`. **Re-apply at every login** writes `~/.config/systemd/user/ddg-sound.service` (`WantedBy=default.target`, waits 8 s for PipeWire, then runs `python3 -m ddg sound apply` from the program copy) and enables it. **Back to normal** sets 100%, removes the KDE line, the saved choices and the service; the speaker level and the chosen output stay as they are.
* Test switch: `THISPC_FAKE_AUDIO=<json file>` replaces `pactl` with a stand-in that keeps outputs and volumes in that file.

## Airlock (desktop inside Game Mode)

In Game Mode, Steam runs inside *gamescope*, which owns the screen. Games and tools it starts get an X11 display (Xwayland) from it. The Airlock tile runs a launcher that starts a **second KWin compositor as a window on that X11 display** (`kwin_wayland --x11-display $DISPLAY --socket wayland-ddg ...`), wrapped in `dbus-run-session` so it has its own message bus and cannot disturb Steam's. KWin is told to run a small script when it starts (`--exit-with-session`), the script starts `plasmashell` (full) or Dolphin and Konsole (lite), and when that script ends KWin ends and the window disappears. Your game was never stopped; it was just hidden behind the window.

Around that: a lock stops a second Airlock; a 15-second timer kills a start that shows nothing; the script touches a "ready" file once something is on screen, so the timer can tell a slow start from a quick close; if Steam sends *Exit game* the whole process group is closed; free memory is checked first. If the nested desktop fails, the next, lighter option is tried. Switching to real Desktop Mode (`steamos-session-select plasma`) closes the game, so it asks first and treats silence as No. The last step only shows instructions. Nothing here ever reboots.

The colour bar in the window reads a small state file (`portal.json`) plus the detected session: amber while the Airlock is open, blue on the desktop, green in Game Mode. Executable scripts in `mode-change.d` get the new mode as their argument, so other tools can follow along.
