# Zombie-Deck

> **Work in progress (v0.3.1).** The logic is fully tested against a simulated ROG Ally, but it has **not yet been run on a real handheld**. Read [Status](#status) before relying on it.

**Wakes your Bazzite / Nobara handheld up properly.** After sleep or hibernation a ROG Ally (and sometimes a Steam Deck) often comes back as a zombie: stuck on 17 W no matter what the TDP slider says, half its cores parked, CPU pinned at 544 MHz, GPU left in a manual power state, the built-in controller gone, WiFi asleep. Zombie-Deck snapshots the power state before sleep and puts it all back after wake — then checks again a few seconds later, because the ASUS firmware likes to have the last word.

Project started 2026-11-04 18:10. Licence: [PolyForm Noncommercial 1.0.0](LICENSE.md) (source-available; free for personal use, credit required). Author: Matthew Armstrong ([The-Dorkknight](https://github.com/The-Dorkknight)).

## What goes wrong, and what Zombie-Deck does about it

| Symptom after wake | Cause (see [docs/research-notes.md](docs/research-notes.md)) | Zombie-Deck's fix |
|---|---|---|
| TDP stuck at 17 W, slider does nothing | The ASUS EC / `asusd` flips the platform profile to *quiet/balanced* on resume. Steam re-sends your TDP straight away, `steamos-manager` refuses it ("TDP limiting not active") and Steam falls back to 17 W. | Waits 3 s for the firmware to settle, restores the platform profile and the exact SPL/SPPT/FPPT limits from before sleep, then does it again at +8 s and +20 s ("double tap"). |
| Feels slow, cores parked, 544 MHz | CPUs that were offlined for s2idle stay offline; amd-pstate regression leaves the energy-performance preference at 0 (deep power-save) and `scaling_max_freq` at the floor. | Brings every core back online, restores governor, min/max clocks, EPP and boost. With no snapshot it still applies sane defaults. |
| GPU slow / odd | `power_dpm_force_performance_level` left on `manual`. | Restores it (default `auto`). |
| Built-in controller dead, touchscreen fine | The Ally's MCU (USB 0b05:1abe / 1b4c) doesn't re-enumerate after the hub is powered off for sleep. | If the MCU is missing after wake, re-authorises the USB hub it lived on. |
| No WiFi | MediaTek `mt7921e` doesn't always come back. | Gives NetworkManager 15 s, then toggles the radio; optionally reloads the driver (`ZD_WIFI=reload`). |

**On screen:** a small zombie-brain icon top-right (artwork by Matthew Armstrong) pulses while the revive runs, flashes green for 3 s when it's done, or turns red for 5 s if something failed (`zombie-deck status` then tells you what). **Crash guard:** if the device shuts down uncleanly twice in a row after a revive (not a flat battery), you're asked whether to keep Zombie-Deck; no answer in 20 s and it disables itself and goes back to stock.

It is a **system service, not a Decky plugin**: a pair of systemd units tied to `sleep.target`, so it runs as root on every suspend and hibernate (power button, Steam menu, idle timeout), works whether or not Decky Loader is installed, in game mode and desktop mode alike, and has no dependencies beyond `/bin/sh`.

## Quick start

1. Copy the folder to the handheld (desktop mode).
2. Double-click **`Zombie-Deck-Linux.sh`** (choose *Run in terminal* if asked). It opens a small menu; pick **1) Install**. You'll be asked for your password (sudo).
3. The installer copies its files, enables the units, reads everything back, shows a **dry run** of exactly what the next wake would change, and prints the current power state.
4. Pick **9) Sleep test**: the Ally suspends, you wake it with the power button, and the window reports whether the revive ran. Any time later, `zombie-deck status` shows *sleeps seen* and *revives run*, which should both go up by one on every sleep/wake. Log: `zombie-deck log` or `journalctl -u zombie-deck-revive`.

That's it. Nothing runs until the device sleeps. Uninstall from the same menu.

**Updating:** run Install from the new version. It removes the previous version completely first (units, the old v0.1–0.2 hook, overlay helper, icons, anything still running), keeps your `/etc/zombie-deck/zombie-deck.conf`, then installs fresh.

No terminal? In desktop mode: `sh install.sh install`. In game mode you can't run it; switch to desktop mode once.

## Commands

```
zombie-deck status     current power state, verdict "alive" or "ZOMBIE" (exit code 2 when zombie)
zombie-deck revive     run the full revive pass now (needs root)
zombie-deck test       dry run: print every change a revive would make, change nothing
zombie-deck snapshot   save the current state (the sleep hook does this automatically)
zombie-deck log        last 60 log lines
zombie-deck json       one-line JSON status for other apps (Deck Deck Goose); no root needed
zombie-deck disable    switch the sleep hook off (stock behaviour; files stay)
zombie-deck enable     switch it back on, reset the crash counter
zombie-deck --version  "Zombie-Deck 0.3.1 (built 2026-11-07 20:45)"
```

## Configuration

`/etc/zombie-deck/zombie-deck.conf` — plain `KEY=VALUE`, commented. The defaults restore whatever you had before sleep. Useful changes:

- `ZD_TDP=25` — always come back at 25 W sustained (27 slow / 30 fast) instead of restoring.
- `ZD_PROFILE=performance` — always force the performance profile (Steam's TDP slider only works on it).
- `ZD_WIFI=reload` — allow a driver reload if toggling the radio isn't enough.
- `ZD_CONTROLLER=off` — never touch USB.
- `ZD_DOUBLE_TAP_SECONDS="8 20 45"` — more or later extra passes.
- `ZD_OVERLAY=off` — no brain on screen. `ZD_GUARD=off` — no crash counter. `ZD_GUARD_PROMPT_SECONDS=20` — the countdown.

After editing run `sudo zombie-deck test` to see the effect without changing anything.

## Files it installs

| Path | What |
|---|---|
| `/usr/local/bin/zombie-deck` | the whole program (one POSIX sh script) |
| `/etc/systemd/system/zombie-deck-sleep.service` | wanted by `sleep.target`: snapshot on the way down, starts the revive on the way up |
| `/etc/systemd/system/zombie-deck-revive.service` | the revive itself (restarted on each wake) |
| `/etc/zombie-deck/zombie-deck.conf` | your settings (kept on update) |
| `/usr/local/libexec/zombie-deck/` | the overlay helper (`zombie-deck-overlay`) and the two brain icons |
| `/etc/systemd/system/zombie-deck-guard.service` | boot-time crash guard (can't block boot: see docs/safety-review.md) |
| `/var/lib/zombie-deck/` | crash counter and boot marker |
| `/var/log/zombie-deck.log`, `/run/zombie-deck/state` | log and the pre-sleep snapshot |

Only `/usr/local` and `/etc` are written, which stay writable on image-based Bazzite. Nothing is layered with `rpm-ostree`, nothing survives an uninstall.

## Supported hardware

- **ROG Ally / Ally X on Bazzite** — the main target; uses the kernel's `asus-armoury` firmware attributes for TDP (Bazzite 42+ kernels), falls back to `ryzenadj` if present.
- **Steam Deck on Bazzite / SteamOS** — the CPU/GPU/WiFi parts apply; the Ally-only parts (profile, armoury, MCU) skip themselves when the files aren't there.
- **Legion Go, other AMD handhelds, laptops** — CPU/GPU/WiFi parts; TDP only via `ryzenadj`.
- Needs systemd. Runs on anything, including a 7th-gen i3: it's a shell script that sleeps most of the time.

## Status

- [x] **v0.1–0.2 never ran on a real device**: they put the hook in `/etc/systemd/system-sleep`, a folder systemd never reads (it only reads its compiled-in `/usr/lib/systemd/system-sleep`, read-only on Bazzite). Found when the Ally showed no overlay after a power-button wake. v0.3.0 hooks `sleep.target` with units instead.
- [x] Overlay tested against a real X server (Xvfb): window appears top-right with `GAMESCOPE_EXTERNAL_OVERLAY=1` set before mapping, draws the icons, exits on time.
- [x] Logic covered by 35 tests against a simulated Ally: healthy, zombie, no-snapshot, config variants, dry run, clamping, zombie-snapshot carry-forward, overlapping revives, crash-guard state machine, overlay fallbacks, packaging.
- [x] Safety review done — see [docs/safety-review.md](docs/safety-review.md): no path can boot-loop; the guard can only ever disable Zombie-Deck, never anything else.
- [x] `shellcheck` clean, POSIX sh (dash) — no bashisms.
- [x] **Confirmed on a real Bazzite ROG Ally (2026-11-07):** after hibernation the brain flashes green, and the wake fix seems to be working.
- [ ] Not yet reported: plain power-button suspend, Nobara Handheld, the red/failure path on a real device. The sysfs paths come from the kernel sources and Bazzite's own issue tracker (see research notes), but the kernel may refuse some writes or the EC may win a race we haven't timed. First real-device run: install, sleep, wake, `zombie-deck log`, and open an issue with the log.
- [ ] The GTK brain overlay was rendered here with cairo (looks right) but not shown inside a live gamescope session; the notify-send fallback covers desktop mode if GTK isn't there.
- [ ] Steam's own TDP slider may still *display* 17 W after Zombie-Deck has put the hardware back to 25 W; nudging `steamos-manager` over D-Bus is on the to-do list.
- [ ] Windows and Mac launchers only explain that this is a Linux tool; they were not run.

## Development

```
python3 -m unittest discover tests          # 35 tests, ~25 s (overlay test needs Xvfb, else skipped)
ZD_PREVIEW=ok python3 src/zombie-deck-overlay show   # preview the brain (menu option 8 does both colours)
python3 tests/fake_sysfs.py /tmp/ally --zombie
ZD_ROOT=/tmp/ally ZD_STATE_DIR=/tmp/ally/run/zombie-deck sh src/zombie-deck status
```

`ZD_ROOT` prefixes every `/sys`, `/run` and `/var` path so the real script runs unchanged against a fake tree. Environment variables override the conf file, which overrides the built-in defaults.

**Other apps:** `/etc/zombie-deck/sleep.d/` takes drop-in scripts that run as root with `pre` before every sleep and `post` after wake (5 s each). Deck Deck Goose uses this and `zombie-deck json`; see [docs/deck-deck-goose-integration.md](docs/deck-deck-goose-integration.md).

See [docs/design.md](docs/design.md) for how the pieces fit, [docs/safety-review.md](docs/safety-review.md) for the boot-loop and edge-case review, [docs/research-notes.md](docs/research-notes.md) for the evidence behind each fix, and [CHANGELOG.md](CHANGELOG.md).
