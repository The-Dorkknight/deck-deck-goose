#!/bin/sh
# Zombie-Deck - double-click launcher for Linux (choose "Run in terminal" / "Run"
# in the file manager). Opens the installer menu in a terminal window.
cd "$(dirname "$0")" || exit 1
if [ -t 0 ]; then
    exec sh ./install.sh "$@"
fi
for t in konsole gnome-terminal xfce4-terminal ptyxis kgx xterm; do
    if command -v "$t" >/dev/null 2>&1; then
        case "$t" in
            konsole)        exec konsole --hold -e sh ./install.sh "$@" ;;
            gnome-terminal) exec gnome-terminal -- sh -c "sh ./install.sh $*; echo; echo 'Press Enter to close'; read x" ;;
            xfce4-terminal) exec xfce4-terminal --hold -e "sh ./install.sh $*" ;;
            ptyxis|kgx)     exec "$t" -- sh -c "sh ./install.sh $*; echo; echo 'Press Enter to close'; read x" ;;
            xterm)          exec xterm -hold -e sh ./install.sh "$@" ;;
        esac
    fi
done
echo "No terminal emulator found. Open a terminal here and run:  sh install.sh" >&2
exit 1
