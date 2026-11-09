#!/bin/sh
# Zombie-Deck installer / checker / uninstaller (text mode, runs in a terminal).
# Double-click Zombie-Deck-Linux.sh to start it, or:  sh install.sh [install|check|test|uninstall]
# Works on Bazzite, Nobara, SteamOS (with a read-write root) and any systemd
# distro: only /usr/local and /etc are written, which stay writable on
# image-based systems like Bazzite.

# shellcheck disable=SC2015  # ok/bad/say always succeed, so A && B || C is safe here
ZD_VERSION="0.3.1"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/src"
BIN=/usr/local/bin/zombie-deck
OLD_HOOK=/etc/systemd/system-sleep/zombie-deck   # v0.1-0.2 location: never run by systemd, removed on install
SLEEP_UNIT=/etc/systemd/system/zombie-deck-sleep.service
REVIVE_UNIT=/etc/systemd/system/zombie-deck-revive.service
CONF=/etc/zombie-deck/zombie-deck.conf
LIBEXEC=/usr/local/libexec/zombie-deck
UNIT=/etc/systemd/system/zombie-deck-guard.service

say() { printf '%s\n' "$*"; }
ok()  { printf '  [ OK ] %s\n' "$*"; }
bad() { printf '  [ !! ] %s\n' "$*"; FAIL=1; }

need_root() {
    [ "$(id -u)" = "0" ] && return 0
    say "Zombie-Deck needs administrator rights to install into /usr/local and /etc."
    if command -v sudo >/dev/null 2>&1; then
        exec sudo -- sh "$0" "$@"
    elif command -v pkexec >/dev/null 2>&1; then
        exec pkexec sh "$0" "$@"
    fi
    say "Neither sudo nor pkexec found. Run as root:  sh $0 $*"
    exit 1
}

same_file() { [ -f "$2" ] && cmp -s "$1" "$2"; }

# remove_previous - take out any earlier Zombie-Deck completely before
# installing, so nothing from an old version is left behind (v0.1-0.2 hook in
# /etc/systemd/system-sleep, old SVG icons, old units, a revive or overlay that
# is still running). Keeps only your config file and the log.
remove_previous() {
    old=""
    [ -x "$BIN" ] && old="$("$BIN" --version 2>/dev/null | sed -n 's/^Zombie-Deck \([0-9.]*\).*/\1/p')"
    if [ -z "$old" ] && [ ! -e "$OLD_HOOK" ] && [ ! -e "$SLEEP_UNIT" ] && [ ! -e "$UNIT" ] && [ ! -d "$LIBEXEC" ]; then
        return 0
    fi
    say "Removing the previous version${old:+ ($old)} first ..."
    systemctl stop zombie-deck-revive.service >/dev/null 2>&1
    systemctl disable --now zombie-deck-guard.service >/dev/null 2>&1
    systemctl disable zombie-deck-sleep.service >/dev/null 2>&1
    for u in $(systemctl list-units --all --plain --no-legend 'zombie-deck-revive-*' 'zombie-deck-overlay-*' 2>/dev/null | awk '{print $1}'); do
        systemctl stop "$u" >/dev/null 2>&1      # v0.1-0.2 transient units
    done
    pkill -f "$LIBEXEC/zombie-deck-overlay" >/dev/null 2>&1
    rm -f "$OLD_HOOK"; rmdir /etc/systemd/system-sleep 2>/dev/null
    rm -f "$SLEEP_UNIT" "$REVIVE_UNIT" "$UNIT" "$BIN" /etc/zombie-deck/disabled
    rm -rf "$LIBEXEC" /var/lib/zombie-deck /run/zombie-deck
    systemctl daemon-reload 2>/dev/null
    ok "previous version${old:+ $old} removed (config $CONF kept)"
}

do_install() {
    FAIL=0
    remove_previous
    say "Installing Zombie-Deck $ZD_VERSION ..."
    install -D -m 0755 "$SRC/zombie-deck" "$BIN"            && ok "$BIN"           || bad "could not write $BIN"
    install -D -m 0644 "$SRC/zombie-deck-sleep.service" "$SLEEP_UNIT"   && ok "$SLEEP_UNIT"  || bad "could not write $SLEEP_UNIT"
    install -D -m 0644 "$SRC/zombie-deck-revive.service" "$REVIVE_UNIT" && ok "$REVIVE_UNIT" || bad "could not write $REVIVE_UNIT"
    install -D -m 0755 "$SRC/zombie-deck-overlay" "$LIBEXEC/zombie-deck-overlay" && ok "$LIBEXEC/zombie-deck-overlay" || bad "could not write the overlay helper"
    mkdir -p "$LIBEXEC/icons" && install -m 0644 "$SRC"/icons/*.png "$SRC"/icons/*.bgra "$LIBEXEC/icons/" && ok "$LIBEXEC/icons (brain icons)" || bad "could not write the icons"
    install -D -m 0644 "$SRC/zombie-deck-guard.service" "$UNIT" && ok "$UNIT" || bad "could not write the guard service"
    if [ -f "$CONF" ]; then
        ok "$CONF kept (already exists)"
    else
        install -D -m 0644 "$SRC/zombie-deck.conf" "$CONF"  && ok "$CONF"          || bad "could not write $CONF"
    fi
    mkdir -p /etc/zombie-deck/sleep.d /var/log /var/lib/zombie-deck && touch /var/log/zombie-deck.log 2>/dev/null
    rm -f /etc/zombie-deck/disabled
    command -v restorecon >/dev/null 2>&1 && restorecon -RF "$BIN" "$SLEEP_UNIT" "$REVIVE_UNIT" "$CONF" "$LIBEXEC" "$UNIT" 2>/dev/null
    systemctl daemon-reload 2>/dev/null
    if systemctl enable zombie-deck-sleep.service >/dev/null 2>&1; then
        ok "sleep hook enabled (runs on every suspend / hibernate)"
    else
        bad "could not enable zombie-deck-sleep.service (systemctl)"
    fi
    if systemctl enable --now zombie-deck-guard.service >/dev/null 2>&1; then
        ok "crash guard service enabled and started"
    else
        bad "could not enable the crash guard service (systemctl)"
    fi
    say
    do_check
}

do_check() {
    FAIL=0
    say "Checking the installation ..."
    same_file "$SRC/zombie-deck" "$BIN" && [ -x "$BIN" ]   && ok "program installed and executable"   || bad "program missing or different from this copy"
    same_file "$SRC/zombie-deck-sleep.service" "$SLEEP_UNIT" && same_file "$SRC/zombie-deck-revive.service" "$REVIVE_UNIT" && ok "sleep and revive units installed" || bad "sleep/revive units missing or different from this copy"
    if systemctl is-enabled zombie-deck-sleep.service >/dev/null 2>&1 && systemctl show -p Wants --value sleep.target 2>/dev/null | grep -qw zombie-deck-sleep.service; then
        ok "sleep.target pulls in zombie-deck-sleep.service - it will run on every sleep and wake"
    else
        bad "zombie-deck-sleep.service is not wired to sleep.target (menu option 6 re-enables it)"
    fi
    [ -e "$OLD_HOOK" ] && bad "old hook $OLD_HOOK still present - run Install again to remove it"
    [ -r "$CONF" ] && ok "config present"                   || bad "config missing"
    [ -x "$LIBEXEC/zombie-deck-overlay" ] && [ -r "$LIBEXEC/icons/zombie-brain-green-256.png" ] && [ -r "$LIBEXEC/icons/zombie-brain-red-256.png" ] && [ -r "$LIBEXEC/icons/zombie-brain-green-128.bgra" ] && ok "overlay helper and icons present" || bad "overlay helper or icons missing"
    if systemctl is-enabled zombie-deck-guard.service >/dev/null 2>&1; then
        ok "crash guard service enabled ($(systemctl is-active zombie-deck-guard.service 2>/dev/null))"
    else
        bad "crash guard service not enabled"
    fi
    [ -e /etc/zombie-deck/disabled ] && bad "Zombie-Deck is currently DISABLED ($(cat /etc/zombie-deck/disabled)); menu option 6 re-arms it"
    if command -v systemctl >/dev/null 2>&1 && systemctl cat sleep.target >/dev/null 2>&1; then
        ok "systemd with sleep.target present"
    else
        bad "systemd / sleep.target not found - Zombie-Deck needs systemd"
    fi
    [ -r /sys/firmware/acpi/platform_profile ] && ok "platform profile: $(cat /sys/firmware/acpi/platform_profile)" || say "  [ -- ] no platform profile on this machine"
    [ -d /sys/class/firmware-attributes/asus-armoury ] && ok "asus-armoury TDP attributes found (ROG Ally)" || say "  [ -- ] no asus-armoury attributes (not an Ally, or older kernel) - ryzenadj will be used if installed"
    command -v ryzenadj >/dev/null 2>&1 && ok "ryzenadj available" || say "  [ -- ] ryzenadj not installed (only needed without asus-armoury)"
    say
    if [ "$FAIL" = "0" ] && [ -x "$BIN" ]; then
        say "Dry run - this is what the next wake would change right now:"
        "$BIN" test 2>&1 | sed 's/^/    /'
        say
        "$BIN" status
        say
        say "Installed. Test it: menu option 9 sleeps the device, waits for you to wake it"
        say "and reports whether the revive ran. Or sleep it yourself and run:  zombie-deck status"
        say "('sleeps seen' and 'revives run' should both go up by one each time)."
        say "Log:  zombie-deck log    or    journalctl -t zombie-deck"
    else
        say "Installation is NOT complete - see the lines marked [ !! ] above."
    fi
}

do_uninstall() {
    say "Removing Zombie-Deck ..."
    systemctl disable --now zombie-deck-guard.service >/dev/null 2>&1
    systemctl disable zombie-deck-sleep.service >/dev/null 2>&1
    systemctl stop zombie-deck-revive.service >/dev/null 2>&1
    rm -f "$UNIT" "$SLEEP_UNIT" "$REVIVE_UNIT" && systemctl daemon-reload 2>/dev/null
    rm -rf "$LIBEXEC" /var/lib/zombie-deck
    rm -f "$BIN" "$OLD_HOOK" /etc/zombie-deck/disabled && ok "program, sleep hook, overlay and guard service removed"
    printf 'Remove the config file %s too? [y/N] ' "$CONF"; read -r a
    case "$a" in y|Y) rm -f "$CONF"; rmdir /etc/zombie-deck 2>/dev/null; ok "config removed";; *) ok "config kept";; esac
    rm -rf /run/zombie-deck
    say "Done. Nothing else was touched."
}

menu() {
    while :; do
        say
        say "Zombie-Deck $ZD_VERSION - wake your handheld properly"
        say "  1) Install (or update)"
        say "  2) Check installation and show a dry run"
        say "  3) Show current power state (zombie-deck status)"
        say "  4) Show the log"
        say "  5) Uninstall"
        say "  6) Enable / re-arm Zombie-Deck (after the crash guard disabled it)"
        say "  7) Disable Zombie-Deck (back to stock, keeps the files)"
        say "  8) Preview the brain overlay (green 3 s, then red 5 s)"
        say "  9) Sleep test: suspend now, wake with the power button, report"
        say "  q) Quit"
        printf 'Choose: '; read -r c
        case "$c" in
            1) need_root install; do_install ;;
            2) do_check ;;
            3) [ -x "$BIN" ] && "$BIN" status || say "not installed yet" ;;
            4) [ -x "$BIN" ] && "$BIN" log || say "not installed yet" ;;
            5) need_root uninstall; do_uninstall ;;
            6) need_root enable; "$BIN" enable ;;
            7) need_root disable; "$BIN" disable ;;
            8) preview ;;
            9) need_root sleeptest; sleep_test ;;
            q|Q) exit 0 ;;
        esac
    done
}

sleep_test() {
    [ -x "$BIN" ] || { say "not installed yet"; return 1; }
    before="$(cat /var/lib/zombie-deck/wakes 2>/dev/null || echo 0)"
    say "Sleeping in 3 s. Wake the device with the power button; this window will report back."
    sleep 3
    systemctl suspend || { say "systemctl suspend failed"; return 1; }
    say "Woke up - waiting up to 40 s for the revive to finish ..."
    t=0
    while [ "$t" -lt 40 ]; do
        now="$(cat /var/lib/zombie-deck/wakes 2>/dev/null || echo 0)"
        res="$(cat /var/lib/zombie-deck/wakes.result 2>/dev/null)"
        if [ "$now" -gt "$before" ] && ! systemctl is-active --quiet zombie-deck-revive.service; then break; fi
        sleep 1; t=$((t + 1))
    done
    say
    if [ "$now" -gt "$before" ]; then
        ok "revive ran after this wake (result: ${res:-unknown})"
    else
        bad "no revive ran after this wake - see: journalctl -b -u zombie-deck-sleep -u zombie-deck-revive"
    fi
    say "Last log lines:"; tail -n 15 /var/log/zombie-deck.log 2>/dev/null | sed 's/^/    /'
}

preview() {
    ov="$SRC/zombie-deck-overlay"
    say "Green brain flashing for 3 s, then a red one for 5 s. Close any notification that appears."
    ZD_PREVIEW=ok python3 "$ov" show; ZD_PREVIEW=fail python3 "$ov" show
}

case "${1:-}" in
    install)   need_root install;   do_install ;;
    enable)    need_root enable;    "$BIN" enable ;;
    disable)   need_root disable;   "$BIN" disable ;;
    preview)   preview ;;
    sleeptest) need_root sleeptest; sleep_test ;;
    uninstall) need_root uninstall; do_uninstall ;;
    check)     do_check ;;
    test)      [ -x "$BIN" ] && "$BIN" test || say "not installed yet" ;;
    "")        menu ;;
    *)         say "usage: sh install.sh [install|check|test|enable|disable|preview|sleeptest|uninstall]"; exit 1 ;;
esac
