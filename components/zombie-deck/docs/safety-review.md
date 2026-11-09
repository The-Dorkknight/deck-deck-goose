# Zombie-Deck — safety and edge-case review (v0.2.0, 2026-11-04; updated v0.3.0, 2026-11-05)

Scope: can Zombie-Deck brick, boot-loop, or wedge a Bazzite/Nobara handheld, and what happens in the odd cases. Each item says what was *verified* (test or reasoning) and what still needs a real device.

## Can it cause a boot loop? No, by construction.

| Component | When it runs | Worst case | Why boot is safe |
|---|---|---|---|
| `zombie-deck-sleep.service` (wanted by `sleep.target`) | Starts when the system goes to sleep (snapshot), stops when it wakes (ExecStop queues the revive). Never at boot: `sleep.target` is not part of booting. | Snapshot errors or hangs | `ExecStart=-` ignores failure; `TimeoutStartSec=15` caps a hang, so sleep is delayed at most 15 s, never blocked. ExecStop only queues the revive (`--no-block`) and returns at once. |
| `zombie-deck-revive.service` | After each wake, own unit, no `[Install]` | Revive errors | Runs detached from the sleep transaction; `ExecStart=-`; `restart` replaces a still-running revive on a quick second sleep. |
| Revive (transient `zombie-deck-revive-*.service`) | Seconds after wake, in the background | Writes a bad value to sysfs; kernel returns EINVAL | Every write is a plain sysfs write the user could do with `echo`; none persists across a reboot. A write the kernel refuses is logged as FAILED and skipped. Nothing is written to firmware/NVRAM, boot config, or `/usr`. |
| Guard `zombie-deck-guard.service` | Once at boot, after `multi-user.target` | Script hangs or crashes | `Type=simple`, nothing `Requires=` it, it is not `Before=` any target, `ExecStart=-` means failure is ignored. A hang would leave one idle service; the desktop still starts (verified by `test_guard_unit_cannot_block_boot`). |
| Guard's "disable" action | After two suspect crashes | Disables Zombie-Deck | That is the *safe* direction: it only removes the hook's executable bit and writes `/etc/zombie-deck/disabled`. Stock behaviour returns. It cannot disable anything else. |

Uninstall removes every file it installed; no `rpm-ostree` layering, no kernel args, no initramfs changes.

## The sysfs writes themselves

- `platform_profile`: writing a value the firmware supports; `asusd`/steamos-manager write the same file. Reversible instantly.
- `asus-armoury` `ppt_*`: now clamped to the kernel-published `min_value`/`max_value` (test: `test_fixed_tdp_is_clamped_to_kernel_limits`). The kernel rejects anything outside its DMI table anyway.
- `power_dpm_force_performance_level`: `auto` is the amdgpu default.
- CPU `online`: only CPUs that were online before sleep are re-onlined; with no snapshot, all possible CPUs. Onlining a CPU is never destructive. We never offline anything.
- `scaling_*`, EPP, `boost`: restored to pre-sleep values; a saved max below ¼ of hardware max is treated as the bug itself and replaced with hardware max (`test_stuck_saved_max_clock_is_ignored`).
- USB `authorized`: only on the specific hub the Ally MCU was on before sleep, only if the MCU is missing after wake. The v0.1 fallback that kicked *every* root hub is gone (`test_revive_restores_everything` asserts no USB write when the parent is unknown).
- WiFi driver reload: opt-in only (`ZD_WIFI=reload`).

## Edge cases found and handled

| Case | Behaviour | Test |
|---|---|---|
| Kernel names the fast limit `ppt_pl3_fppt`, v0.1 used `ppt_fppt` | Both names handled; fixed watts write whichever exists | zombie tests |
| Sleeping twice while already a zombie (snapshot captures 17 W) | Snapshot at firmware default + previous snapshot elsewhere → previous values carried forward | `test_zombie_snapshot_carries_previous_tdp_forward` |
| Snapshot captured the stuck 544 MHz max | Treated as corrupt, hardware max used | `test_stuck_saved_max_clock_is_ignored` |
| Two sleeps a few seconds apart (first revive still running) | Each revive records its pid; an older revive stops before its next pass | `test_newer_revive_takes_over` |
| Hook `pre` never ran (first wake after install) | Sanity pass: unpark, lift max clock, EPP, GPU auto; profile/TDP untouched | `test_sanity_pass_without_snapshot` |
| Garbage in the conf (`ZD_TDP=lots`, non-numeric seconds) | Falls back to built-in defaults | `test_garbage_config_falls_back_to_defaults` |
| `zombie-deck revive` run as a normal user | Refuses with a hint instead of logging 40 FAILED lines | `test_revive_needs_root_outside_fake_tree` (+ manual run as `nobody`) |
| Not an Ally (Deck, Legion Go, laptop) | Profile/armoury/MCU steps skip when the files don't exist; CPU/GPU/WiFi still run | healthy tests use the same code paths |
| No `systemd-run` | Hook does nothing rather than start a job the suspend cgroup would kill | by design, commented in the hook |
| No GUI toolkit for the overlay | notify-send → kdialog → stderr; prompt: zenity → kdialog → "disable" | `test_overlay_fallbacks_without_a_display` |

## Crash guard logic

`/var/lib/zombie-deck/boot-marker` is written at boot and deleted by a clean shutdown (`ExecStop`). A marker found at boot means the last shutdown was unclean. It counts only if the marker says `revived=1` (a revive happened in that boot) **and** the battery is not flat (≤ 5 % at boot, or the previous boot's journal mentions a critical battery action). Two counted crashes in a row → prompt for 20 s → default disable. A clean shutdown resets the streak. Tests: all seven `TestCrashGuard` cases.

Known limitation: "crash" here means "the previous boot did not shut down cleanly" — a kernel panic, a forced power-off, or a hard freeze all look the same. That is the intended conservative reading: if Zombie-Deck was involved in the boot that died twice, step aside and ask.

## v0.3.0: the hook never ran (found on a real Ally)

Matthew pressed the power button to sleep and again to wake; no overlay. Cause: systemd-sleep runs hook executables only from its compiled-in `SYSTEM_SLEEP_PATH` (`/usr/lib/systemd/system-sleep`, checked in the v257, v258 and main sources). `/etc/systemd/system-sleep` is not read, so v0.1–0.2 never snapshotted, revived or showed anything on any machine. The fake-sysfs tests ran the script directly and could not catch it. v0.3.0 uses `sleep.target` units (`test_sleep_unit_is_wired_to_sleep_target`, `systemd-analyze verify` in `test_units_verify`), the installer removes the old file and checks that `sleep.target` wants the unit, and `status` shows sleep/wake counters so a silent miss is visible.

Overlay problems found while tracing the same path, all fixed:
- In game mode, gamescope only composites windows that carry `GAMESCOPE_EXTERNAL_OVERLAY`. v0.2 set it *after* mapping, through `xprop` (often not installed). It now sets it with Xlib before mapping (`TestOverlayX11`).
- notify-send (the old fallback) shows nothing in game mode. The X11 backend needs only Python and libX11, so it is now the first choice.
- Session detection used `pgrep -x gamescope-session`; process names are cut to 15 characters, so that never matched. It now reads `/proc/PID/comm` and falls back to any of the user's processes that have a `DISPLAY`.
- The result came after the WiFi wait and both double taps (~20 s or more). Now the first verdict shows right after pass 1 (~3–4 s after wake), with a second overlay only if a later pass changes the result.
- The result compared against "ideal" values, so the `balanced` profile or WiFi left off counted as failures (red brain). It now compares with the snapshot and config, and WiFi is not part of it.

## Still not verified on hardware

- That the EC race timing (3 / 8 / 20 s) is right for the Ally; adjust `ZD_SETTLE_SECONDS` / `ZD_DOUBLE_TAP_SECONDS` from the log if a pass "loses".
- That gamescope composites the X11 overlay window in game mode (same property MangoHud's mangoapp uses; window and property verified under Xvfb, not under gamescope).
- That the sleep.target unit pattern fires on Bazzite's power-button path (Steam / hhd → `systemctl suspend`). It is systemd's own mechanism, but installer option 9 is the real check.
- That re-authorising the MCU's hub brings the controller back (it is what the kernel's own suspend patch achieves by other means).
- The guard's session detection (`loginctl` + reading `/proc/<pid>/environ` of gamescope/plasmashell).
