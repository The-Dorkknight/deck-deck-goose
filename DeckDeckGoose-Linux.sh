#!/bin/sh
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Deck Deck Goose 0.3.0-wip - double-click launcher (choose "Run" / "Run in Terminal").
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "Python 3 was not found."
  echo "Mint/Ubuntu: sudo apt install python3     Bazzite/Fedora: it is already there, try a new terminal."
  printf "Press Enter to close..."; read -r _; exit 1
fi
PYTHONPATH=src "$PY" -m ddg gui
status=$?
if [ "$status" -ne 0 ]; then
  echo "Deck Deck Goose stopped with an error (code $status)."
  printf "Press Enter to close..."; read -r _
fi
exit "$status"
