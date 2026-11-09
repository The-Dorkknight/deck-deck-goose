# Research notes (2026-10-31; Steam 2026-11-03; Game Mode and keyboard 2026-11-04; Plasma Keyboard 2026-11-04 evening; Sound 2026-11-06)

What was looked up before building, what it said, and what is still unverified. Written from search results on the day; nothing here was tested on a real Bazzite machine.

## The problem
* Bazzite is built on Fedora Atomic. The operating system is mostly read-only. User files live under `/var/home/<user>`, and `/home` is a convenience path to it. A Bazzite forum thread shows `findmnt` output with `/var/home` mounted from the btrfs `/var` subvolume, and a user's reply that "most of the locations outside of `/home` and `/etc` are read-only and not meant to be changed". Linux has no drive letters; there is one tree under `/`.
* Removable and automounted disks normally appear under `/run/media/<user>/<label>`.
* Reported (a third-party description of Bazzite's `ujust` helper, **not checked**): `ujust enable-automounting` mounts labelled BTRFS/EXT4 partitions at `/run/media/$USER/<label>`.

## Why Flatpak file managers miss disks
* Flatpak docs, "Sandbox Permissions": `--filesystem=host` gives *normal files on the host, not including host OS or system internals*. The blacklist includes `/lib`, `/bin`, `/sbin`, `/usr`, `/boot`, `/root`, `/tmp`, `/etc`, `/app`, `/run`, `/proc`, `/sys`, `/dev`, `/var`. The documented exception is `/run/media`.
* Consequence: a disk mounted at `/var/mnt/...` or `/mnt/...` (a symlink into `/var/mnt` on Atomic systems, **not verified here**) is not covered by `host`. This tool therefore also adds `--filesystem=/var/mnt` when that folder exists. **Unverified:** whether Flatpak accepts that explicit path on a real system.
* Users on forums fix this either with Flatseal or with `flatpak override --filesystem=/run/media <app>`. Flatseal (`com.github.tchx84.Flatseal`) is a graphical front end for the same overrides, per the Bazzite/Framework knowledge base.
* A user-level override is stored under `$XDG_DATA_HOME/flatpak/overrides` (man page `flatpak-override`). `--reset` clears all of an app's overrides.

## File managers considered
* **Dolphin**: split view with F3. Available on Flathub as `org.kde.dolphin`; people report that the Flatpak copy only sees `/home` unless permissions are widened. Bazzite KDE ships Dolphin natively as far as we know (not checked).
* **Double Commander**: two panes, Total Commander style. A Universal Blue forum thread says it is *not published on Flathub*, and describes running it in a Distrobox Fedora container. Extension points (Lua scripts, WCX/WFX/WDX/WLX plug-ins) are from our own prior knowledge, not re-checked today.
* **Krusader**: two panes by default. A KDE discussion thread says it is not on stable Flathub (maybe on Flathub Beta).
* **Nemo**: F3 second pane; Python extensions. GTK, no KDE dependencies. Default on Linux Mint.
* Decision: instead of building a plug-in for one file manager, build a **file-manager-independent logical folder**. Any file manager, split-pane or not, can open `~/This PC`. Plug-ins for Nemo/Double Commander can come later and would just point at the same folder.

## Design decisions and rejected alternatives
| Decision | Rejected | Why |
|---|---|---|
| Real folder of symlinks | FUSE virtual file system | No kernel/FUSE dependency, works in every file manager and in Flatpak apps that can reach the targets, nothing to crash. |
| Browser-based window using only the standard library | tkinter | `python3-tkinter` is a separate package on Fedora-based systems and may not be installed on an Atomic system (not checked on Bazzite); a browser is always there. It also made the window testable here. |
| systemd user timer every 30 s | udev rule or udisks D-Bus listener | No root, no extra dependency, very cheap (one `lsblk`). Cost: up to 30 s delay. |
| Hide everything not under media/mount prefixes | Show all mounts | The aim is "disks, Users, Documents" without the immutable OS parts. |
| Text insertion into `user-places.xbel` with backup | Rewriting the XML | Keeps Dolphin's own entries byte-for-byte. |
| `--reset` on uninstall | Surgical removal | Flatpak has no "remove just this one grant" that restores the manifest default without writing a negative override. Documented in the README. |

## Steam game manager (added 2026-11-03)

### Your question: "a manifest needs editing and a link generated"
Close, but simpler. Each installed game has `steamapps/appmanifest_<appid>.acf` in its library. It stores the folder *name* (`installdir`), not a path, so it needs no editing when moved. Steam finds a game by finding its manifest in one of the libraries it knows about. Moving a game therefore means: move the folders and the manifest into the other library's `steamapps/`, and make sure that library is in Steam's list (`libraryfolders.vdf`). No symlink is needed. (A symlink trick also exists, but it breaks if the drive is missing and confuses Steam's space accounting, so it was rejected.)

### What we found
* **Where the library list lives.** A ProtonUp-Qt issue reports identical copies at `~/.steam/steam/config/libraryfolders.vdf` and `~/.steam/steam/steamapps/libraryfolders.vdf`; the tool there prefers `config/`. We read `config/` first and write both.
* **Adding a library by editing the file.** LostDragonist's *steam-library-setup-tool* (open source, used to add libraries Steam's UI refuses) adds a numbered entry with `path`, `label`, `contentid` (a random number not already used), `totalsize` "0" and an empty `apps` block, writes it to both `libraryfolders.vdf` files after backing them up, and creates `steamapps/` in the new folder. It reads but does not write `libraryfolder.vdf` in the library. We copy this approach (including its `mounted` "1" key) and additionally write `libraryfolder.vdf` with the same `contentid`, as Steam does for libraries it creates. **Unverified with a real Steam client.**
* **Proton prefixes live with the game.** Valve's Proton FAQ: prefixes "are located in the same library as the game" at `steamapps/compatdata/<appid>/pfx/`, and "saved games are stored in the game's WINE prefix". So the prefix must move with the game, or saves appear lost.
* **StateFlags.** Lutris documents the bit values: 4 = fully installed, 2 = update required, 1048576 = downloading, and so on. We only move games whose value is exactly 4.
* **NTFS.** The Proton FAQ links "prefix not owned by you" errors to libraries on NTFS and points to a separate NTFS guide. We block NTFS targets by default. exFAT and FAT are blocked outright because they cannot store symlinks (prefixes contain `dosdevices/` symlinks) or Linux permissions.
* **Artwork.** Older Steam stored `librarycache/<appid>_library_600x900.jpg`. A September 2026 bug report (sunshine-apps-ui #21) shows newer Steam using `librarycache/<appid>/<hash>...jpg`, and a Vibepollo issue shows the portrait at `librarycache/<appid>/<40-char hash>/library_600x900.jpg` and the header at `.../library_header.jpg`. Another report mentions localised names like `*_<language>.jpg`. We look for all of these, plus custom art in `userdata/<id>/config/grid/<appid>p.png` (the layout Steam uses for artwork you set yourself; from our own knowledge, not re-checked).
* **Non-Steam shortcuts** get Proton prefixes too, under ids with the top bit set (2147483648 and above; from our own knowledge). The junk scan never lists those as orphans.
* **btrfs compression.** Oracle's btrfs guide: compression can be a mount option or a property, "only the new writes are compressed unless compression is applied when defragmentation is run", example `btrfs filesystem defragment -czlib`, and the warning that defragmenting data shared with a snapshot "breaks the link", so disk use goes up. We use zstd and `btrfs property set <dir> compression zstd`. Whether a normal user may defragment their own files: our reading of the kernel source (from memory, not re-checked) is that regular files need write permission, not root. If it fails, the window says so and the README gives the `sudo` command. Fedora-based systems usually mount btrfs with `compress=zstd:1` already (from our knowledge; the window shows the real mount option), which compresses new writes but not files written before.
* **Closing Steam.** `steam -shutdown` asks a running client to exit (a long-standing Steam command line option; from our knowledge).

### Compressing selectively (added 2026-11-03, 0.2.1)
Matthew asked whether it is better to compress only the bulky media and leave the program uncompressed. Findings, from our own knowledge unless a source is named:
* Videos, audio and most packed archives are already compressed; zstd cannot shrink them. btrfs keeps a compressed piece only when it is smaller (the Oracle guide describes compression as applied per write; the "at least one 4 KiB block saved" rule is from our knowledge of btrfs), so they end up stored unchanged. Compressing them wastes time, not space.
* Executables and libraries usually compress well but are small and read once at launch. zstd decompression is far faster than a drive can read, so the cost is small.
* The real savings are in uncompressed assets, which vary by game. Hence an **estimate first** rather than a fixed rule.
* Measured here on sample data (64 x 128 KiB pieces): zlib level 3 came out at 0.183 vs zstd level 3 at 0.184 of original size on text-like data, 0.312 vs 0.232 on structured binary, and both at 1.000 on random data. So zlib as a stand-in errs on the low side.
* FIEMAP: the `FIEMAP_EXTENT_ENCODED` flag marks extents stored in a transformed (e.g. compressed) form. btrfs sets it for compressed extents (from our knowledge; checked here only on ext4, where it is never set). This lets a normal user see what the drive already compressed without `compsize` or root.

### Decisions
| Decision | Rejected | Why |
|---|---|---|
| Estimate from samples, then compress only files that shrink | Compress everything; or compress only by file type | Sampling is fast and adapts to each game; file types alone miss uncompressed `.pak`/`.dat` files. |
| Copy, compare, then delete; rename on the same drive | Move file by file | The old copy stays intact until the new one is proven complete. |
| Journal with roll back / finish on next start | No recovery | A power cut mid-move must not leave a game in neither library. |
| Compare names, types, sizes and symlink targets | Hash every file | Hashing 100 GB takes far longer than copying it; size plus structure catches truncated or missing files. |
| Only move fully installed games, only with Steam closed | Try anyway | Steam rewrites manifests and library lists while running. |
| Leftover game folders and prefixes listed but unticked | Tick everything | They can hold saves and mods. |
| Measure compression by free-space change | `compsize` | `compsize` needs root and is not always installed. |

### Not verified (needs a real machine)
1. A real Steam client picking up the moved game and the new library without a "Install" prompt.
2. Flatpak Steam reading a library outside home after our override.
3. `btrfs filesystem defragment -czstd` as a normal user on Bazzite, and the space it saves.
4. Steam rewriting `libraryfolders.vdf` without complaint after our edit.

## Not verified for the This PC tab (needs a real machine)
1. Real `flatpak override --user --show` output format (the tool expects a `filesystems=` line containing `host`).
2. That Dolphin picks up the Places entry after a restart, and what it shows on entering a symlink.
3. That `/var/mnt` as an explicit Flatpak path is accepted.
4. `systemctl --user` timers on Bazzite and Mint sessions.
5. The Mount button against real `udisksctl`.

## Game Mode tile (added 2026-11-04)
Matthew asked for the tool to work in, and install itself into, Game Mode. Choices and findings:
* **Where it appears:** chosen with Matthew: a tile in Steam's library (works on stock Bazzite) rather than a Decky Loader panel (needs Decky; harder to test).
* **Moving games in Game Mode:** in Game Mode, Steam is the session and cannot be closed, and this tool only moves games with Steam closed (Steam rewrites the files it would change). So the Game Mode view leaves moving to Steam's own Settings > Storage. Estimate, Compress and the safe junk items were made to work with Steam open.
* **Non-Steam shortcuts:** stored per account in `userdata/<id>/config/shortcuts.vdf`, a binary VDF file. A shortcut has AppName, Exe and StartDir (quoted paths), LaunchOptions, icon, appid and tags (decky-romm-sync architecture notes). A community format write-up gives the header `\x00shortcuts\x00`, `\x00` for blocks, `\x01` for strings, `\x02` for 4-byte values and a file ending of four `\x08`; it describes the id as a CRC-32 of Exe + AppName with the top bit set. Our writer reproduces that layout (tested). The 32-bit id with the top bit set is also what names the artwork files `<id>p.png` (portrait), `<id>.png` (wide), `<id>_hero.png`, `<id>_logo.png`, `<id>_icon.png` (decky-romm-sync; the wide/portrait meaning from our knowledge).
* **Steam must be closed** while `shortcuts.vdf` is written, or it writes its own copy back on exit (*from our knowledge; the same reason the library list is only edited with Steam closed*).
* **Browser:** Bazzite's comparison page says Firefox is "no longer part of the image in favor of the Flatpak version". Kiosk-mode switches (`--kiosk` for Firefox; `--kiosk --app=` for Chromium browsers) are *from our knowledge*.
* **Game Mode detection:** the gamescope session docs found don't list its environment variables, so the launcher sets `THISPC_GAMEMODE=1` itself; `XDG_CURRENT_DESKTOP=gamescope` is also checked (*unverified*).
* **Not verified:** a real Steam client picking up the shortcut and artwork, Firefox kiosk mode under gamescope, controller behaviour, Flatpak Steam.

## On-screen keyboard (added 2026-11-04)
* KDE's touch keyboard is Maliit. KDE developer Martin Gräßlin describes enabling it with `[Wayland] InputMethod=/usr/share/applications/com.github.maliit.keyboard.desktop` in `kwinrc`, then turning it on from the system tray.
* **Update (0.3.1, after a report from Nobara KDE on a ROG Ally saying "not installed"):** Fedora replaced maliit-keyboard with KDE's new **plasma-keyboard** as the default for Plasma 6.6 and later, because maliit-keyboard crashes with Qt 6.10. Nobara is Fedora-based, so it follows. Its desktop file is `/usr/share/applications/org.kde.plasma.keyboard.desktop` (Flatpak: `~/.local/share/flatpak/exports/share/applications/org.kde.plasma.keyboard.desktop`). KDE's README enables it with `kwriteconfig6 --notify --file kwinrc --group Wayland --key InputMethod <path>`. An openSUSE fix also sets `[Wayland] VirtualKeyboardEnabled=true`, and reports that the Flatpak build did not work for them where the native package did. Package names: Fedora/Nobara `plasma-keyboard`, openSUSE `plasma6-keyboard`. A Fedora bug report says choosing "None" in System Settings can revert to Plasma Keyboard. Version 0.3.0 only looked for Maliit, hence the wrong message.
* Fedora and Universal Blue forum threads report Maliit not appearing automatically on KDE, and that showing it manually without a touch device wasn't possible at the time (KDE bug 466948 asks for that). Hence: set the input method, reload KWin, rely on touchscreen taps, and offer a best-effort "Show keyboard" launcher.
* Not verified on a real Bazzite KDE session.

## Sources
* Flatpak docs, Sandbox Permissions: https://docs.flatpak.org/en/latest/sandbox-permissions.html
* `flatpak-override(1)`: https://man.archlinux.org/man/flatpak-override.1.en.txt
* Universal Blue forum, "Can someone explain Bazzite's file structure?": https://universal-blue.discourse.group/t/can-someone-explain-bazzites-file-structure/12253
* Universal Blue forum, Double Commander in a Distrobox container: https://universal-blue.discourse.group/t/howto-install-double-commander-in-distrobox-container-and-auto-upgrade/8709
* Framework knowledge base, "How to Manage Flatpaks on Bazzite" (Flatseal): https://knowledgebase.frame.work/how-to-manage-flatpaks-in-bazzite-H1Z3HcO3Jx
* pixls.us forum, Flatpak not seeing a USB SSD: https://discuss.pixls.us/t/flatpak-version-doesnt-recognize-images-on-my-usb-ssd/41973
* KDE discussion, Krusader not on stable Flathub: https://discuss.kde.org/t/cant-find-flatpak-solutions-for-these-packages-id-like-to-install/48965
* Fedora discussion, Dolphin Flatpak only working in `/home`: https://discussion.fedoraproject.org/t/dolphin-with-fedora-42-gnome/150541
* Proton FAQ (prefix location, saves, NTFS): https://github.com/ValveSoftware/Proton/wiki/Proton-FAQ
* ProtonUp-Qt issue #126 (libraryfolders.vdf locations): https://github.com/DavidoTek/ProtonUp-Qt/issues/126
* steam-library-setup-tool (adding libraries): https://github.com/LostDragonist/steam-library-setup-tool
* Lutris Steam notes (StateFlags): https://github.com/lutris/lutris/blob/master/docs/steam.rst
* sunshine-apps-ui issue #21 (per-appid artwork folders): https://github.com/4o66/sunshine-apps-ui/issues/21
* Vibepollo issue #554 (hashed artwork paths): https://github.com/Nonary/Vibepollo/issues/554
* Steam-Dock-Extension issue #1 (localised artwork names): https://github.com/ScymicX/Steam-Dock-Extension/issues/1
* Oracle Linux btrfs guide (compression, defragment, snapshots): https://docs.oracle.com/en/operating-systems/oracle-linux/9/btrfs/btrfs-CompressingandDefragmentingaBtrfsFileSystem.html
* decky-romm-sync, non-Steam shortcut notes: https://danielcopper.github.io/decky-romm-sync/architecture/steam-non-steam-shortcuts/
* shortcuts.vdf format write-up: https://gist.github.com/gablm/2a79355026bde51ac4f516d347fa1cd0
* Bazzite, Fedora Atomic comparison (Firefox Flatpak): https://docs.bazzite.gg/General/Fedora_Atomic_Comparison/
* gamescope-session: https://github.com/KyleGospo/gamescope-session
* Martin Gräßlin, using maliit-keyboard in a Plasma Wayland session: https://blog.martin-graesslin.com/blog/2021/03/using-maliit-keyboard-in-a-plasma-wayland-session/
* Fedora discussion, showing KDE's virtual keyboard without a touch device: https://discussion.fedoraproject.org/t/kde-plasma-show-hide-the-virtual-keyboard-manually-without-a-touch-device/161313
* Universal Blue forum, Maliit keyboard not launching: https://universal-blue.discourse.group/t/maliit-keyboard-not-launching/4153
* Fedora discussion, "Regarding maliit-keyboard on Fedora 42/43" (switch to plasma-keyboard): https://discussion.fedoraproject.org/
* KDE plasma-keyboard source and README: https://invent.kde.org/plasma/plasma-keyboard
* openSUSE forums, enabling plasma-keyboard (`VirtualKeyboardEnabled`, Flatpak vs native): https://forums.opensuse.org/

## Sound on the ROG Ally (added 2026-11-06)
* Bazzite's ROG Ally wiki page lists "Audio is soft" as a known issue. It says the two outputs, "Family 17h/19h/1ah HD Audio Controller" and "ROG Ally", affect each other's volume, and recommends setting both to the same level.
* The "ROG Ally" output is a PipeWire **filter-chain** (a virtual sink with an EQ) that plays into the real Family speakers. Its volume and the hardware volume multiply. This explains why picking the Family device directly sounds louder, and why setting the hardware level to 100% fixes the quietness while keeping the EQ. A CachyOS handheld thread shows the same layout ("ROG Ally/X" filter-chain over "Family 17h/19h/1ah HD Audio Controller Analog Stereo"). Its fix was to delete the filter-chain file as root. We don't do that: it is a system file on Bazzite and loses the EQ. Commenters there also found that switching to the Family output by hand did not survive a reboot. Hence the optional re-apply-at-login service.
* KDE's "Raise maximum volume" is stored as `RaiseMaximumVolume` in group `General` of `plasmaparc` (from plasma-pa's `src/globalconfig.kcfg`). KDE's design review for that setting gives 150% as the raised limit. We use the same limit for the boost.
* Above 100%, PipeWire applies software gain, so peaks can clip. That is why the boost goes in steps and the page says so.
* Volume percentages from `pactl` and `wpctl` are both on PipeWire's cubic "user" scale, so they are shown as-is.

Sources (sound):
* Bazzite docs, ASUS ROG Ally handheld wiki (Known issues: audio is soft): https://docs.bazzite.gg/Handheld_and_HTPC_edition/Handheld_Wiki/ASUS_ROG_Ally/
* Universal Blue forum, "ROG Ally audio option" (only a "Family" option shown): https://universal-blue.discourse.group/t/rog-ally-audio-option/5186
* Universal Blue forum, "Audio is super low": https://universal-blue.discourse.group/t/audio-is-super-low/4467
* r/LinuxOnAlly, "Fix CachyOS handheld low sound volume" (filter-chain over the Family output): https://www.reddit.com/r/LinuxOnAlly/comments/1m8ww80/fix_cachyos_handheld_low_sound_volume
* KDE plasma-pa source, `src/globalconfig.kcfg` (`plasmaparc`, `RaiseMaximumVolume`): https://github.com/KDE/plasma-pa
* KDE Phabricator, "[Applet] Make 'raise maximum volume' global" (150%): https://phabricator.kde.org/differential/diff/72751

## Airlock and the rename (added 2026-11-07, 0.5.0)

Airlock began as a separate workbench (plan, research notes and a prototype; see that project's notes). Its research is merged here. Items marked **(verify)** are what `ddg airlock probe` is for.

**How the sessions are built.** Game Mode: `gamescope-session` starts `gamescope`, which runs Steam in Big Picture. Apps launched from Steam get Xwayland (`DISPLAY`); gamescope's own Wayland socket (`GAMESCOPE_WAYLAND_DISPLAY`) is for its protocols, so Airlock nests with `--x11-display` first and `--wayland-display` only as a fallback. Desktop Mode is a normal Plasma Wayland session. `steamos-session-select plasma|gamescope` changes the autologin session and ends the current one (a logout, not a reboot). Bazzite ships it **(verify path)**; Nobara Handheld is believed to provide a compatible command **(verify)**; if it is missing Airlock skips the switch step (code A7SS). `/etc` is writable on Bazzite/Nobara, so the sleep hook goes in `/etc/systemd/system-sleep/`.

**Nested KWin.** Verified in KWin's documented options this session: `--x11-display`, `--wayland-display`, `--socket`, `--width/--height`, `--no-lockscreen`, `--xwayland` and `--exit-with-session`. The launcher reads `kwin_wayland --help` and only passes what exists; older builds that take the program as a positional argument are handled. A nested KWin under KWin without Xwayland has been reported as problematic upstream (KDE bug 371861), which is why `--xwayland` is added when available **(verify on the Ally)**.

**Checked on SteamOS-like systems:** `steamos-session-select` calls `steam-set-session` and `steam -shutdown` on SteamOS and CachyOS handheld images. How Bazzite and Nobara implement it is **not verified**.

**Known catches.** The nested window is driven like a game, so it needs Steam Input's desktop/mouse layout on the tile (shipping one is a to-do). Whether Steam's on-screen keyboard types into it is unverified; the KDE keyboard enabler is the backup. Nested Plasma costs roughly 0.5 to 1 GB RAM (estimate). A nested compositor waking from suspend can come back frozen, hence the optional sleep hook (number 40, so it runs before Zombie-Deck's). The nested Plasma reads the same `~/.config` as Desktop Mode, so its panel changes show there too.

**Decisions.** Airlock lives in Deck Deck Goose, not on its own: the app already installs a Game Mode tile and the keyboard. The fallback ladder ends in instructions, never a reboot. The session switch always asks and treats no answer as No. A mode colour bar plus hook folder instead of hard-wiring any RGB tool (the example hook for Crateless uses a placeholder command). Error codes follow the 4-character ErrCode style.

**The rename.** On-disk markers kept for compatibility: backup file names `*.thispc-backup`, the `THISPC_*` test/override environment variables, and the Steam grid art names. Everything a user sees or types is Deck Deck Goose / `ddg`. The old config and data folders are moved, not copied, and left as a link so an old launcher still works until it is re-added.

**Artwork.** One user-supplied image (tools/art/deck-deck-goose.jpg) feeds `tools/make_art.py`: palette (navy #0a0d1f, orange #ff8a1f, cyan #25c8ff, amber #ffcf66), tile art, icons. The console in the image carries a manufacturer's lettering; replace the image if you publish widely.

Sources: [kwin_wayland options](https://linuxcommandlibrary.com/man/kwin_wayland), [KDE bug 371861: kwin_wayland under kwin_wayland](https://bugs.kde.org/show_bug.cgi?id=371861), plus the KWin and SteamOS pages read earlier in this project (see the Sources list above).


## Windows, macOS and Zombie-Deck (added 2026-11-07, 0.6.0)
- **Why the library port was small:** the Windows engine from GameShelf already separated platform facts (`winplat`) from Steam logic. A macOS twin (`macplat`) supplies the same few functions: Steam folder (`~/Library/Application Support/Steam`), running check (`pgrep -x steam_osx`), quit (osascript), volumes (`/sbin/mount` output; APFS Data volume shown as *Macintosh HD*), filesystem limits. Compression is Windows-only (`compact /EXE` with XPRESS4K/8K/16K/LZX); macOS has APFS compression but no supported per-folder tool for Steam games, so it is off with a stated reason.
- **Honest limits:** only simulated Windows/Mac set-ups were run (no real Windows or macOS machine, no real `compact`, no real Steam there). Mount parsing was checked against a typical `mount` listing written from documentation.
- **Zombie-Deck integration** follows the plan in `components/zombie-deck/docs/deck-deck-goose-integration.md`: bundle, call its own installer, read `zombie-deck json`, never edit it.
- **The Airlock sleep hook bug:** systemd-sleep runs scripts only from its compiled-in `/usr/lib/systemd/system-sleep` (read-only on image-based Bazzite); `/etc/systemd/system-sleep/` is not searched. Replaced by Zombie-Deck's `sleep.d` drop-in or a `sleep.target` unit. Neither has been through a real suspend yet.


## First real Airlock run (added 2026-11-08, 0.6.1)
- **What the owner saw** (photo, ROG Ally, Bazzite, Game Mode): a full KDE desktop with his real icons and *two* panels, and on top of it a window titled *KDE Wayland Compositor - Press right control key to grab input* containing Dolphin. Closing the Dolphin one returned quickly; the full one had no way out except logging out.
- **Reading of it:** the window with that title is a nested `kwin_wayland` on the X11 backend, i.e. Airlock's *lite* rung (Dolphin). Lite only runs after *full* has counted as failed, so the full desktop behind it was a leftover of the failed full rung that had escaped Airlock's process group (Steam's *Exit game* kills only that group). It has a window manager (decorations are drawn), so more than a lone plasmashell survived. Two panels fit Plasma loading the user's real desktop layout (made for another screen arrangement) on a different-sized screen.
- **Not yet known:** why full counted as failed (plasmashell exiting within 3 s, or the watchdog), and exactly which process started the escaped desktop (a Plasma user service started through systemd would also escape `dbus-run-session`). Waiting for `portal.log` and `nested-full.log` from the device.
- **Fix in 0.6.1:** snapshot-and-close of desktop programs (by `/proc/<pid>/comm`, this user only) and of `plasma-*.service` user units that became active, run between rungs and on every way out. In Game Mode, Plasma shell / session processes are always treated as leftovers.
