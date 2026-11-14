#!/usr/bin/env bash
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Deck Deck Goose – Airlock. Runs INSIDE the nested KWin.
# When this script exits, KWin exits (--exit-with-session) and you're back in Game Mode.
#   DDG_NESTED_KIND = full | lite
#   DDG_LITE_APPS   = space-separated apps to try for lite mode
#   DDG_READY       = file to touch once something is on screen (the watchdog waits for it)
set -u
kind="${DDG_NESTED_KIND:-lite}"
ready="${DDG_READY:-${XDG_RUNTIME_DIR:-/tmp}/deck-deck-goose-ready}"

export XDG_CURRENT_DESKTOP=KDE
export XDG_SESSION_DESKTOP=KDE
export KDE_FULL_SESSION=true
export XDG_SESSION_TYPE=wayland
export QT_QPA_PLATFORM=wayland
export DDG_AIRLOCK=1                  # lets the web UI / hooks know they're inside the portal
unset GAMESCOPE_WAYLAND_DISPLAY       # apps must talk to the nested KWin, not to gamescope

child=""
back_button() {
  # "Back to Game" button on a notification (libnotify 0.8+). Pressing it closes the portal.
  command -v notify-send >/dev/null || return
  local answer
  answer=$(notify-send -a "Deck Deck Goose" -u critical -A back="Back to Game" \
    "AIRLOCK – game still running" "Tap Back to Game, or Steam button → Exit game." 2>/dev/null)
  [ "$answer" = "back" ] && [ -n "$child" ] && kill "$child" 2>/dev/null
}

case "$kind" in
  full)
    command -v plasmashell >/dev/null || exit 3
    plasmashell --no-respawn &
    child=$!
    # plasmashell must survive 3 s or we treat it as failed (watchdog then tries lite)
    for _ in 1 2 3 4 5 6; do
      sleep 0.5
      kill -0 "$child" 2>/dev/null || exit 4
    done
    ;;
  lite)
    for app in ${DDG_LITE_APPS:-dolphin konsole}; do
      if command -v "$app" >/dev/null; then
        "$app" &
        child=$!
        break
      fi
    done
    [ -n "$child" ] || exit 5
    sleep 1
    kill -0 "$child" 2>/dev/null || exit 6
    ;;
  *) exit 2 ;;
esac

touch "$ready"
back_button &
# Touch-mode button (top-left). Only on the nested desktop's own Xwayland: if KWin started without one, DISPLAY is
# still gamescope's, and the button must never appear there (it would land in Game Mode itself).
if [ "${DDG_TOUCH_BUTTON:-}" = 1 ] && [ -n "${DISPLAY:-}" ] && [ "${DISPLAY}" != "${DDG_OUTER_DISPLAY:-}" ] && [ -n "${DDG_PY:-}" ]; then
  PYTHONPATH="${DDG_SRC:-}${PYTHONPATH:+:$PYTHONPATH}" "$DDG_PY" -m ddg.touchbutton &
fi
wait "$child"
# ready file is left for the watchdog; ddg/airlock.py removes it
