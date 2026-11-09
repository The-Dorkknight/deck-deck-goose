#!/usr/bin/env bash
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Deck Deck Goose – Airlock sleep hook (OPTIONAL; Deck Deck Goose installs it for you, password once).
# Runs as root with "pre" before sleep. Either as /etc/zombie-deck/sleep.d/40-ddg-airlock (Zombie-Deck
# runs it) or from a small unit on sleep.target. NOT from /etc/systemd/system-sleep: systemd never reads that.
# Before sleep: closes an open Airlock so you wake up in plain Game Mode, not a frozen nested desktop.
case "$1" in
  pre)
    for st in /home/*/.local/state/deck-deck-goose/portal.json; do
      [ -f "$st" ] || continue
      grep -q '"status": "open"' "$st" || continue
      pid=$(sed -n 's/.*"pid": \([0-9]*\).*/\1/p' "$st")
      [ -n "$pid" ] && kill -TERM -- "-$pid" 2>/dev/null
    done
    sleep 1
    ;;
  post) : ;;   # nothing: Zombie-Deck restores TDP/controller after wake
esac
exit 0
