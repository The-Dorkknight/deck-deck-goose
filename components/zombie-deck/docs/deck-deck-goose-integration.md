# Zombie-Deck in Deck Deck Goose: integration plan

Written 2026-11-07 for Zombie-Deck v0.3.1 and Deck Deck Goose / Airlock v0.1.0-wip.

## Status this plan starts from

- **Confirmed on hardware (Matthew, 2026-11-07, Bazzite ROG Ally):** after hibernation the brain flashes green, and the wake fix seems to be working.
- Not yet reported: plain suspend from the power button, Nobara Handheld, and the red/failure path on a real device.
- 35 tests pass (fake-sysfs Ally, unit wiring, `systemd-analyze verify`, overlay under Xvfb).

## Principle: Deck Deck Goose bundles Zombie-Deck, it doesn't absorb it

Zombie-Deck stays its own repo, release and installer. It runs as root from systemd units, and it must keep working when DDG isn't running or isn't installed. DDG only does three things:

1. **Ships it.** A copy of the Zombie-Deck release folder sits in DDG under `components/zombie-deck/`, updated by copying in a new release. DDG never edits these files.
2. **Installs, updates and removes it** by calling Zombie-Deck's own installer. That already removes any previous version first, keeps the config, and checks its own work.
3. **Shows its status** and offers on/off, using the commands below.

## The interface (all stable from v0.3.1)

| What DDG wants | Call | Root? |
|---|---|---|
| Is it installed, and which version? | `zombie-deck --version` → `Zombie-Deck 0.3.1 (built …)`; missing binary = not installed | no |
| Status for the web UI | `zombie-deck json` (one line of JSON, fields below) | no |
| Install / update | `pkexec sh components/zombie-deck/install.sh install` | yes (one prompt) |
| Remove | `pkexec sh components/zombie-deck/install.sh uninstall` (asks about the config) | yes |
| Turn on / off (stock behaviour) | `pkexec zombie-deck enable` / `pkexec zombie-deck disable` | yes |
| Last log lines | `zombie-deck log` (or read `/var/log/zombie-deck.log`) | no |
| Run something before sleep / after wake | drop an executable into `/etc/zombie-deck/sleep.d/` (gets `pre` or `post`) | yes, to install it |

`zombie-deck json` example:

```json
{"app":"zombie-deck","version":"0.3.1","build":"2026-11-07 20:45",
 "armed":true,"disabled":false,"zombie_now":false,
 "sleeps":12,"wakes":12,"last_sleep":"2026-11-07 20:10:02","last_wake":"2026-11-07 20:14:40","last_result":"ok",
 "crash_streak":0,"overlay":"on","guard":"on","tdp_watts":25,"profile":"performance","log":"/var/log/zombie-deck.log"}
```

- `armed`: `sleep.target` really pulls in `zombie-deck-sleep.service`. This is the check that would have caught the v0.1–0.2 bug.
- `sleeps` / `wakes`: counters (`null` before the first sleep). If `wakes` is behind `sleeps` after a wake, the revive didn't run. Show a warning.
- `last_result`: `ok` / `fail` from the last wake (green / red brain).
- `zombie_now`: the same verdict as `zombie-deck status` right now.

## Suggested DDG UI (web panel)

A small card in DDG's main web UI, polling `zombie-deck json` every 10 s:

- **Header:** a 24 px brain icon (copy `src/icons/zombie-brain-green-128.png` / `-red-128.png`), "Zombie-Deck", version.
- **Band:** green "Wake fix armed · last wake OK (20:14)"; red "Last wake failed – open log"; grey "Off (stock sleep)"; amber "Installed but not armed – Repair".
- **Buttons:** Install / Update (when the bundled version is newer than the one installed), On/Off, Sleep test (runs installer option 9 in a terminal, since it needs the power button), Show log.
- Compare versions with a plain dotted-number sort. Don't install over a newer Zombie-Deck than the one bundled.

## Airlock: fix its sleep hook by using Zombie-Deck's

Airlock's README step 4 and its research notes put `ddg-portal-sleep.sh` in `/etc/systemd/system-sleep/40-ddg-portal`. **systemd never runs scripts there.** It only reads its compiled-in `/usr/lib/systemd/system-sleep`, which is read-only on Bazzite. Zombie-Deck v0.1–0.2 made the same mistake (see research-notes.md). So:

1. **If Zombie-Deck is installed** (preferred): DDG installs Airlock's hook as `/etc/zombie-deck/sleep.d/40-ddg-airlock`. Zombie-Deck runs it as root with `pre` before every suspend/hibernate (5 s limit), and `post` after wake. Because the hook runs as root, it must reach the user's Airlock:
   ```sh
   #!/bin/sh
   # /etc/zombie-deck/sleep.d/40-ddg-airlock  - close Airlock before sleep
   [ "$1" = pre ] || exit 0
   u="$(loginctl list-sessions --no-legend | awk '$3!="root"{print $3; exit}')"
   [ -n "$u" ] || exit 0
   exec runuser -u "$u" -- /path/to/deck-deck-goose/scripts/ddg-portal.sh stop
   ```
2. **Without Zombie-Deck:** Airlock needs its own unit, using the same pattern as `zombie-deck-sleep.service`: `WantedBy=sleep.target`, `Before=sleep.target`, `StopWhenUnneeded=yes`, `Type=oneshot`, `RemainAfterExit=yes`, with ExecStart running the hook above with `pre`.
3. Update Airlock's RESEARCH_NOTES ("the sleep hook goes in /etc/systemd/system-sleep/" is wrong) and PLAN item "installer offers the optional sleep hook".

Order on sleep: Airlock closes (sleep.d `pre`), then Zombie-Deck takes its snapshot. On wake: Zombie-Deck revives, and the brain shows over gamescope (it's on Steam's Xwayland, not inside the closed nested desktop).

## Mode indicators and the overlay

- Zombie-Deck's brain is drawn on gamescope's Xwayland with `GAMESCOPE_EXTERNAL_OVERLAY`. If Airlock is open during a wake (only possible without the hook), the brain still appears over everything.
- Optional: a DDG `hooks/mode-change.d` script isn't needed. Zombie-Deck isn't tied to Game/Desktop/Airlock mode, and it works in all three.

## DDG to-do list (to merge into Airlock's PLAN.md, step 4)

- [ ] `components/zombie-deck/`: copy of the Zombie-Deck release (no `.git`); note its version in DDG's CHANGELOG
- [ ] web UI card: `zombie-deck json` → band + buttons (above)
- [ ] Install/Update/Remove/On/Off through `pkexec`, one prompt each, explained first
- [ ] Airlock sleep hook → `/etc/zombie-deck/sleep.d/40-ddg-airlock` (with the unit fallback), and fix Airlock's docs
- [ ] test on both Allys: power-button suspend, hibernate, sleep with Airlock open
- [ ] release zip + PDF write-up, as usual

## What Zombie-Deck promises not to change without a major version bump

The `json` field names and types, the `sleep.d` folder and its `pre`/`post` arguments, the `enable`/`disable`/`--version` commands, `install.sh install|uninstall|check|sleeptest`, and the icon file names.
