#!/bin/sh
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Deck Deck Goose - one-step install for Bazzite / Nobara / Mint (double-click, choose "Run in Terminal").
# Puts the program in ~/.local/share/deck-deck-goose, adds it to the app menu and adds the
# Deck Deck Goose and Airlock tiles to Steam's Game Mode. No password. Run it from Desktop Mode.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "Python 3 was not found."; printf "Press Enter to close..."; read -r _; exit 1
fi
echo "Installing Deck Deck Goose (Steam will close for a moment)..."
echo
echo "  WARNING: AIRLOCK MAY BLOCK DESKTOP MODE. This installer does NOT turn Airlock on."
echo "  To use it later: Deck Deck Goose > Airlock tab > Enable Airlock (two sanity checks)."
echo
PYTHONPATH=src "$PY" -m ddg install-app
status=$?
echo
if [ "$status" -eq 0 ]; then
  echo "Done. Find Deck Deck Goose in the app menu, and its tile in Steam"
  echo "(Game Mode: Library > Non-Steam). Airlock stays OFF until you enable it in the Airlock tab."
  echo "You can delete this download folder now."
else
  echo "Some steps did not work (see the lines marked above)."
fi
printf "Press Enter to close..."; read -r _
exit "$status"
