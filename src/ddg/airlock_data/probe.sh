#!/usr/bin/env bash
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Deck Deck Goose – Airlock probe. Run once on each handheld (Bazzite AND Nobara).
# Safe: reads only. With --live it also opens a 10-second nested test window.
# Report: ~/deck-deck-goose-probe.txt  (send this back so the real build targets what works)
out="$HOME/deck-deck-goose-probe.txt"
have() { command -v "$1" >/dev/null && echo "yes ($(command -v "$1"))" || echo "NO"; }
{
  echo "Deck Deck Goose – Airlock probe  $(date '+%Y-%m-%d %H:%M')"
  echo "== System";  grep -E '^(PRETTY_NAME|ID|VARIANT_ID)=' /etc/os-release
  echo "kernel: $(uname -r)"
  echo "== Session"
  echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-}"
  echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-}"
  echo "DISPLAY=${DISPLAY:-}  WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}"
  echo "GAMESCOPE_WAYLAND_DISPLAY=${GAMESCOPE_WAYLAND_DISPLAY:-}"
  echo "== Tools"
  for t in kwin_wayland plasmashell dbus-run-session steamos-session-select gamescope \
           notify-send kdialog zenity xrandr dolphin konsole steam; do
    printf '%-24s %s\n' "$t" "$(have "$t")"
  done
  echo "== kwin_wayland"
  if command -v kwin_wayland >/dev/null; then
    kwin_wayland --version 2>&1 | head -1
    h="$(kwin_wayland --help 2>&1)"
    for f in --x11-display --wayland-display --exit-with-session --socket --no-lockscreen --width --xwayland; do
      grep -q -- "$f" <<<"$h" && echo "  $f: yes" || echo "  $f: NO"
    done
  fi
  command -v gamescope >/dev/null && gamescope --version 2>&1 | head -1
  echo "== Resources"
  grep -E 'MemTotal|MemAvailable' /proc/meminfo
  for b in /sys/class/power_supply/BAT*; do
    [ -d "$b" ] && echo "battery: $(cat "$b/capacity")% $(cat "$b/status")"
  done
  echo "== Session select"
  ls -l /usr/bin/steamos-session-select 2>&1
  ls /usr/share/wayland-sessions/ /usr/share/xsessions/ 2>/dev/null
  echo "== Sleep hook dirs (Zombie-Deck shares these)"
  ls -ld /etc/systemd/system-sleep 2>&1
} | tee "$out"

if [ "${1:-}" = "--live" ]; then
  echo "== Live test: nested KWin + konsole for 10 s" | tee -a "$out"
  if [ -n "${DISPLAY:-}" ] && command -v kwin_wayland >/dev/null; then
    timeout 10 dbus-run-session -- kwin_wayland --x11-display "$DISPLAY" --socket wayland-ddgtest \
      --no-lockscreen --width 1280 --height 800 konsole >>"$out" 2>&1
    echo "live test exit code: $? (124 = ran the full 10 s = GOOD)" | tee -a "$out"
  else
    echo "live test skipped: no DISPLAY or no kwin_wayland" | tee -a "$out"
  fi
fi
echo; echo "Saved: $out"
