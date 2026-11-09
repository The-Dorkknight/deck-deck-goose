# Zombie-Deck — design

## Timeline of a sleep/wake with Zombie-Deck installed

```
you press power ─┐
                 ▼
 sleep.target starts ──► zombie-deck-sleep.service ExecStart ──► zombie-deck snapshot
                                         (profile, TDP, GPU DPM, CPU gov/EPP/
                                          clocks/online mask, MCU USB path,
                                          WiFi iface/driver/connection)
                                         → /run/zombie-deck/state
         ... s2idle / hibernate ...
 sleep.target stops ──► ExecStop ──► systemctl --no-block restart zombie-deck-revive.service
                                              │
   asusd / EC flip profile  ◄── 0..3 s ──►    │ sleep ZD_SETTLE_SECONDS (3)
   Steam re-sends TDP, fails, falls to 17 W   │
                                              ▼
                                   pass 1: profile → TDP → GPU DPM → CPU
                                           controller check (re-auth hub if MCU gone)
                                           verdict → brain flashes green / red
                                           WiFi check (wait 15 s, toggle radio, [reload])
                                              │
                                     +8 s ──► pass 2: profile → TDP → GPU → CPU
                                    +20 s ──► pass 3: profile → TDP → GPU → CPU
                                              ▼
                                           "revive done" in the log
```

Every pass is idempotent: a value already correct is not written, so a healthy wake produces no `set` lines in the log at all.

## Why these choices

**One file, POSIX sh.** Bazzite is an immutable image; adding Python packages or a compiled helper means layering or a container. `/bin/sh` is always there, starts in milliseconds and the whole thing is 450 lines. tkinter on Bazzite has been a disaster for Matthew's other projects, so the installer is a plain terminal menu.

**Snapshot before, restore after — not fixed values.** Whatever the user set (TDP slider, profile, Decky tools) is what comes back. Fixed values are still available (`ZD_TDP=25`, `ZD_PROFILE=performance`) for people who always want the same thing.

**Double tap.** The failure is a race: the EC, asusd and Steam all write the same knobs in the first seconds after wake. One write at +3 s usually wins; two more at +8 s and +20 s cover the cases where it doesn't. Cheap, because passes that find everything correct do nothing.

**Sanity pass without a snapshot.** If `pre` never ran (first install, hook failure) `revive` still unparks cores, lifts a stuck `scaling_max_freq`, replaces EPP 0 and resets GPU DPM to auto — the things that are wrong in only one direction — but leaves profile and TDP alone, since there's nothing to restore them *to*.

**`ZD_ROOT` prefix everywhere.** The script never hard-codes `/sys`; `tests/fake_sysfs.py` builds a fake Ally and the unit tests run the real script against it in ~10 s. Dry run (`zombie-deck test`) is the same code path with writes replaced by log lines.

**Settings precedence: environment > conf file > defaults.** Lets the tests and one-off experiments (`ZD_TDP=20 sudo zombie-deck test`) work without editing the conf.

## Overlay and crash guard (v0.2.0)

The revive runs as root with no display, so `overlay_launch` finds the logged-in user (`loginctl`), borrows `DISPLAY`/`WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR` from one of their desktop processes (gamescope first, then plasmashell/gnome-shell) and starts `zombie-deck-overlay show` as that user. The helper polls `/run/zombie-deck/overlay`: `init` → pulsing green, `ok` → flashing green 3 s, `fail` → red 5 s. Under gamescope it sets `GAMESCOPE_EXTERNAL_OVERLAY` on its window so it is composited over the game.

`zombie-deck-guard.service` runs `guard` at boot and `shutdown` on a clean stop. A boot marker that survives into the next boot means an unclean shutdown; it counts only if that boot had a revive (`revived=1`) and the battery isn't flat. Two in a row → `zombie-deck-overlay prompt 20 N` as the user → keep, or (default) `disable`. Disable = hook loses its executable bit + `/etc/zombie-deck/disabled`; `enable` reverses it. Full table in safety-review.md.

## What it deliberately does not do

- Touch `mcu_powersave`, fan curves, charge limits or anything Bazzite/asusd owns beyond the resume race.
- Restart Steam, gamescope or the user session.
- Run continuously. Nothing runs between sleeps.
- Talk to steamos-manager over D-Bus (yet). That's the planned way to make Steam's slider *display* the restored value.

## File map

| File | Role |
|---|---|
| `src/zombie-deck` | the program: snapshot / revive / status / test / log |
| `src/zombie-deck-sleep.service` | wanted by `sleep.target`: ExecStart = snapshot, ExecStop = start the revive |
| `src/zombie-deck-revive.service` | runs `zombie-deck revive` after each wake |
| `src/zombie-deck.conf` | default config, installed to `/etc/zombie-deck/` once |
| `install.sh` | terminal installer: install → read back → dry run → status; check; uninstall |
| `Zombie-Deck-Linux.sh` | double-click launcher: opens `install.sh` in a terminal |
| `tests/fake_sysfs.py` | builds a healthy or zombie fake Ally sysfs tree |
| `src/zombie-deck-overlay`, `src/icons/` (zombie-brain PNGs, artwork by Matthew) | brain overlay + crash prompt helper (Python, GTK/cairo with notification fallbacks) |
| `src/zombie-deck-guard.service` | boot-time crash guard unit |
| `tests/test_zombie_deck.py` | 31 unittest cases |
