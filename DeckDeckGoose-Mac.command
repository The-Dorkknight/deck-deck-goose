#!/bin/sh
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Deck Deck Goose - double-click launcher for macOS. Opens the Steam library manager (Games tab) in your browser.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3)
if [ -z "$PY" ]; then
  echo "Python 3 was not found. Install it from https://www.python.org/downloads/ (or run: xcode-select --install)."
  printf "Press Enter to close..."; read -r _; exit 1
fi
PYTHONPATH=src "$PY" -m ddg gui
status=$?
if [ "$status" -ne 0 ]; then
  echo "Deck Deck Goose stopped with an error (code $status)."
  printf "Press Enter to close..."; read -r _
fi
exit "$status"
