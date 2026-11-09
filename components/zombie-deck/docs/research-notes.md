# Zombie-Deck — research notes

Written 2026-11-04 while designing v0.1.0. Everything the program does traces back to one of these; nothing here was verified on a device yet.

## 1. The TDP-stuck-at-17 W bug (the main target)

Matthew's own description: after waking from sleep, Bazzite on the ROG Ally sits in a low-TDP, cores-parked state and setting a manual TDP doesn't help.

Bazzite issue **#5877** "ROG Ally stuck on low TDP after wake up" (Z1 Extreme, Bazzite 44, Sept 2026) reports exactly that: slider has no effect, only a restart fixes it. No diagnosis in the thread.

Bazzite issue **#5907** "Custom TDP reset to default on sleep/wake and AC transitions on ASUS ROG Ally (steamos-manager 'TDP limiting not active')" supplies the mechanism:

1. On resume from s2idle (and on plug/unplug) the ASUS EC and `asusd` briefly switch the platform profile to quiet/balanced.
2. Steam GamepadUI re-applies the custom TDP immediately.
3. `steamos-manager` (`FirmwareAttributeLimitManager::set_tdp_limit` in `src/power.rs`) has an `ensure!(is_active())` guard: TDP limiting is only "active" on the **performance** profile. The call fails with "TDP limiting not active".
4. Steam sees the D-Bus error, drops the custom value and falls back to the 17 W default. From then on the slider is dead because every later call hits the same guard until the profile is performance again.

Device profile `devices/rog_ally.toml` in steamos-manager: `platform_profile_name = "asus-wmi"`, `suggested_default = "performance"`, TDP via `attribute = "asus-armoury"` with `performance_profile = "performance"`.

**Consequences for the design**
- Put the platform profile back *first*, then the TDP numbers. Order matters.
- Wait a few seconds before the first pass (`ZD_SETTLE_SECONDS=3`) so we're not fighting the EC mid-flip, then repeat at +8 s and +20 s (`ZD_DOUBLE_TAP_SECONDS`) in case asusd or Steam overwrite us.
- Write the same files steamos-manager writes: `/sys/class/firmware-attributes/asus-armoury/attributes/{ppt_pl1_spl,ppt_pl2_sppt,ppt_fppt}/current_value` (sustained / slow / fast, in watts). Older or non-Ally kernels: `ryzenadj --stapm-limit --slow-limit --fast-limit` in mW.
- Steam's slider UI may still show 17 after we fix the hardware. Future: ask steamos-manager over D-Bus to set it again once the profile is back, so Steam's state matches.

Diagnosis on a real device: `journalctl -b | grep -E 'Setting TDP limit|TDP limiting not active'` around the wake time.

## 2. Bazzite's own attempt — PR #5904

"fix(power): reset GPU DPM and restore TDP limits on resume" adds `/usr/libexec/bazzite-gpu-tdp-resume-reset` and a `bazzite-gpu-tdp-resume.service` wanted by the suspend/hibernate targets, gated by an `ExecCondition` hardware check. It resets `power_dpm_force_performance_level` from `manual` to `auto`. The TDP part was removed ("leave TDP selection to SteamOS Manager on resume"), so it is **GPU-only** now. Draft, closed and reopened Oct 2026, unmerged; the author says it'll be closed once upstream steamos-manager changes land, and a maintainer dislikes the service approach.

So: upstream is not restoring TDP after wake, and may not for a while. That's the gap. We keep the GPU DPM reset too (`ZD_GPU_DPM`).

## 3. CPU: parked cores and 544 MHz

Bazzite issue **#1725** "Rog Ally, CPU stuck at 544mhz after sleep". Separately, the kernel's amd-pstate driver had an "EPP 0 after resume" regression and an "EPP 0 after hibernate" regression (Mario Limonciello's patches, Aug/Sept 2025) — `energy_performance_preference` comes back as `0`/`power` so the CPU never leaves the power-save corner. Also a 2026 RFC about `bios_min_perf` not being reset correctly across suspend and CPU offlining.

Zombie-Deck restores per-CPU `scaling_governor`, `energy_performance_preference`, `scaling_min_freq`, `scaling_max_freq`, the global `cpufreq/boost`, and brings back online every `cpuN/online` that was online before sleep (from `/sys/devices/system/cpu/online`). Without a snapshot it still unparks, bumps `scaling_max_freq` to `cpuinfo_max_freq` when it's below it, and replaces an EPP of `0`/`power` with `balance_performance`.

## 4. Controller (Ally MCU) after sleep

Kernel patch series "platform/x86: asus-wmi: disable USB0 hub on ROG Ally before suspend" (Luke Jones, Nov 2023) and "Refactor Ally suspend/resume" (Feb/Mar 2025): ASUS firmware disconnects the USB0 hub when the screen goes off in s2idle, and the N-Key / MCU device must be handled around that. Bazzite issue **#2406** (Ally and Ally X, 2025): built-in controller permanently dead after sleep/hibernate, touchscreen fine; "the Steam controller was powered off"; a fix was "in testing".

The MCU is USB vendor `0b05`, product `1abe` (Ally) or `1b4c` (Ally X). Zombie-Deck records its sysfs path and parent hub before sleep; if it's absent after wake it writes `0` then `1` to the parent hub's `authorized` file, which forces re-enumeration. Fallback: all root hubs. `mcu_powersave` (`/sys/devices/platform/asus-nb-wmi/mcu_powersave`) is deliberately left alone — Bazzite manages it and the right value depends on MCU firmware version.

## 5. WiFi (mt7921e)

Framework 13 AMD thread (Arch, 2024): `mt7921e` needs a module reload after a long hibernate; a `/usr/lib/systemd/system-sleep/` script or a service `After=hibernate.target` with a ~15 s delay (to not race NetworkManager) fixes it; `rfkill block/unblock` around sleep also worked. Steam Deck users report black-screen/no-WiFi after resume occasionally (Realtek RTL8822CE there).

Zombie-Deck: wait `ZD_WIFI_WAIT_SECONDS` (15) for NM to reconnect on its own, then `nmcli radio wifi off/on`, then — opt-in only — `modprobe -r <driver>; modprobe <driver>`. Driver name is read from `/sys/class/net/<iface>/device/driver` before sleep.

## 6. Where to hook in

- **Correction (v0.3.0):** the v0.1 note claimed `/etc/systemd/system-sleep/<script>` is called with `pre`/`post`. Wrong. systemd-sleep only runs executables from `SYSTEM_SLEEP_PATH` = `libexecdir/system-sleep` = `/usr/lib/systemd/system-sleep` (`src/sleep/sleep.c`: `static const char* const dirs[] = { SYSTEM_SLEEP_PATH, NULL };`, same in v257, v258 and main). Upstream issue #4927 asked for `/etc` and `/usr/local` too; Poettering: "/etc is the place for system configuration, not for executable scripts", and it was never added. `/usr/lib` is read-only on Bazzite.
- The portable alternative upstream suggests is a unit wanted by `sleep.target`: `Before=sleep.target`, `StopWhenUnneeded=yes`, `Type=oneshot`, `RemainAfterExit=yes`. ExecStart runs on the way to sleep; when `sleep.target` is stopped after resume, the unit is no longer needed and its ExecStop runs. Zombie-Deck uses that, with ExecStop queueing a separate revive unit (`--no-block`) so the wake is never held up.
- The hook must return quickly (systemd waits for it), so `post` hands the real work to `systemd-run --no-block --collect`, which gives the revive its own transient unit and journal entries.
- Decky plugin was rejected: Decky runs as the user, isn't always installed, and only exists in game mode. A plain system hook covers both modes and both distros.

## 7. Steam Deck notes

Bazzite's Deck image mostly has working sleep (Valve's firmware). The gist "Steam Deck problems" lists black screen after resume when switching to desktop mode, and occasional controller loss. The Ally-only steps skip themselves on a Deck (no `platform_profile`, no `asus-armoury`, no `0b05` MCU), so installing Zombie-Deck there only adds the CPU/GPU/WiFi sanity checks.

## Sources

- https://github.com/ublue-os/bazzite/issues/5877
- https://github.com/ublue-os/bazzite/issues/5907
- https://github.com/ublue-os/bazzite/pull/5904
- https://github.com/ublue-os/bazzite/issues/1725
- https://github.com/ublue-os/bazzite/issues/2406
- https://github.com/ublue-os/bazzite/issues/1070
- https://lkml.iu.edu/hypermail/linux/kernel/2311.3/01994.html (asus-wmi: disable USB0 hub before suspend)
- https://lkml.iu.edu/hypermail/linux/kernel/2503.3/01505.html (asus-wmi: Refactor Ally suspend/resume v4)
- https://patchew.org/linux/20250826052747.2240670-1-superm1@kernel.org (amd-pstate EPP 0 after resume)
- https://patchew.org/linux/20250923152929.1306578-1-superm1@kernel.org (amd-pstate EPP 0 after hibernate)
- https://lkml.iu.edu/2607.2/01849.html (amd-pstate bios_min_perf across suspend/offlining)
- https://community.frame.work/t/framework-13-amd-on-arch-issues-with-wireless-after-resume/44597
- https://www.baeldung.com/linux/wifi-connection-fix-no-reboot (systemd-sleep hook pattern)
- https://github.com/systemd/systemd/issues/4927 (no /etc or /usr/local system-sleep directory; use sleep.target units)
- https://github.com/systemd/systemd/blob/main/src/sleep/sleep.c (only SYSTEM_SLEEP_PATH is scanned)
- https://gist.github.com/mikeeq/83b2d7b7d1512b3f56ae115a0e10ea53 (Steam Deck problems list)
- https://github.com/aarron-lee/SimpleDeckyTDP (how Decky TDP tools apply limits)
