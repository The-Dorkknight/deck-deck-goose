# Changelog

All notable changes to Zombie-Deck. Semantic versioning.

## [0.3.1] - 2026-11-07 20:45

Confirmed on a real Bazzite ROG Ally: hibernate → wake flashes the green brain.

### Added
- `zombie-deck json`: one-line JSON status (armed, disabled, sleeps/wakes counters, last result, crash streak, TDP, profile) for Deck Deck Goose and other front ends. Runs without root.
- `/etc/zombie-deck/sleep.d/`: drop-in scripts run as root with `pre` before sleep and `post` after wake, 5 s each; failures are logged and ignored. Deck Deck Goose's Airlock uses it to close itself before sleep.
- `docs/deck-deck-goose-integration.md`: how DDG bundles, installs, shows and controls Zombie-Deck, plus the fix for Airlock's sleep hook (same `/etc/systemd/system-sleep` problem).
- 4 tests (35 in total).

## [0.3.0] - 2026-11-05 22:50

### Fixed
- **The sleep hook never ran.** It lived in `/etc/systemd/system-sleep`, which systemd never reads (only its compiled-in `/usr/lib/systemd/system-sleep`, read-only on Bazzite). Replaced with `zombie-deck-sleep.service` (wanted by `sleep.target`: snapshot on sleep, revive queued on wake) and `zombie-deck-revive.service`. The installer removes the old file. Found on a real ROG Ally: no overlay after a power-button wake.
- Overlay in game mode: new X11 backend (ctypes + libX11, no Python packages) sets `GAMESCOPE_EXTERNAL_OVERLAY` before mapping, so gamescope composites it over the game. Desktop mode: override-redirect, click-through.
- Session detection: no longer depends on `pgrep -x` matching names cut to 15 characters.
- The result shows a few seconds after wake (after pass 1) instead of after the WiFi wait and double taps.
- The result now compares with the snapshot and config instead of "ideal" values, so the `balanced` profile or WiFi left off no longer shows a red brain.
- An installer check claimed `/etc/systemd/system-sleep` hooks run; it now checks for `sleep.target`.

### Added
- `status` shows the wiring (`sleep.target` wants the unit), sleeps seen / revives run counters, and the last result.
- Install removes any previous version completely first (v0.1–0.2 hook, units, overlay helper, icons, leftover transient units, running overlay), keeping only the config and log. Tested by installing v0.2.1 and upgrading over it.
- Installer option 9: sleep test (suspend, wake with the power button, report whether the revive ran).
- Raw BGRA icons for the X11 backend, plus `tools/make_raw_icons.py`.
- Tests: unit wiring, `systemd-analyze verify`, overlay window and gamescope property under Xvfb, wake counters, disabled-mode skips (31 in total).

## [0.2.1] - 2026-11-04 19:40

### Changed
- Overlay uses new zombie-brain artwork (green and red tiles, 128 and 256 px PNGs with transparent rounded corners) for both the GTK overlay and the notification fallback. The drawn cairo brain stays only as a fallback if the PNGs are missing. Old SVG icons removed.

## [0.2.0] - 2026-11-04 19:30

### Added
- On-screen brain overlay: green pulsing while reviving, green flashing 3 s on success, red 5 s on failure (GTK/cairo, falls back to notify-send / kdialog with bundled icons).
- Crash guard service: counts unclean shutdowns that followed a revive (flat battery excluded); after two in a row asks whether to keep Zombie-Deck, default after 20 s is to disable and return to stock. `zombie-deck enable` / `disable`.
- Revive now ends with a verdict (exit 0 ok / 1 still zombie) and `status` shows the armed/disabled state and crash streak.
- Installer menu: enable, disable, overlay preview; installs the guard service.
- 15 new tests; `docs/safety-review.md`.

### Fixed
- Fast TDP limit used the wrong attribute name (`ppt_fppt`); the kernel's name is `ppt_pl3_fppt`. Both are now handled.
- Fixed-watt mode is clamped to the kernel's `min_value`/`max_value`.
- A missing controller with an unknown parent hub no longer kicks every USB root hub.
- Sleeping while already a zombie no longer snapshots and restores 17 W / 544 MHz.
- A second sleep during a running revive no longer leaves two revives fighting.
- Non-numeric config values fall back to defaults; `revive` refuses to run without root.

## [0.1.0] - 2026-11-04 18:10

First release (work in progress — not yet run on real hardware).

### Added
- Systemd sleep hook that snapshots power state before suspend/hibernate and restores it after wake, with extra passes at +8 s and +20 s.
- Restores platform profile and ROG Ally TDP (asus-armoury SPL/SPPT/FPPT, ryzenadj fallback), GPU DPM level, CPU online mask, governor, clocks, EPP and boost.
- Re-authorises the USB hub if the Ally's controller MCU is missing after wake.
- WiFi recovery: wait, toggle radio, optional driver reload.
- `zombie-deck status` with a zombie/alive verdict, `test` dry run, `log`.
- Terminal installer with read-back check and dry run; Linux double-click launcher.
- 11 tests against a simulated ROG Ally; shellcheck-clean POSIX sh.
