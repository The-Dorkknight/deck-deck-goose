# Build history

Every build of this project, newest first, with the main code changes in each. Release zips keep
only the last three builds in git history, so older builds live on here (and in CHANGELOG.md).
Written by `tools/history_log.py`; entries are never removed.

## 0.6.7-wip: Airlock touch-mode button (Mouse / Native touch / Trackpad / Right-click) (2026-11-14)

- 14 files changed, 876 insertions(+), 14 deletions(-)
- Main code changes:
  - `tests/test_touchmode.py` +271 -0
  - `src/ddg/touchmode.py` +251 -0
  - `src/ddg/touchbutton.py` +251 -0
  - `src/ddg/airlock.py` +59 -7
  - `src/ddg/webui.py` +10 -2
  - `tests/test_airlock.py` +6 -1
  - `src/ddg/airlock_data/nested-session.sh` +5 -0
  - `src/ddg/__init__.py` +1 -1
  - `setup.py` +1 -1

## 0.6.6-wip: Delete this copy / Uninstall, visible reasons on greyed-out game buttons (2026-11-13)

- 12 files changed, 364 insertions(+), 13 deletions(-)
- Main code changes:
  - `src/ddg/uninstall.py` +151 -0
  - `tests/test_delete_game.py` +150 -0
  - `src/ddg/webui.py` +43 -5
  - `src/ddg/steam.py` +1 -2
  - `src/ddg/shelf/steam.py` +1 -2
  - `src/ddg/__init__.py` +1 -1
  - `setup.py` +1 -1

## 0.6.5-wip: Airlock controller support (pad drives mouse and keyboard through uinput), Controller and Pointer speed options (2026-11-10)

- 13 files changed, 846 insertions(+), 18 deletions(-)
- Main code changes:
  - `src/ddg/padbridge.py` +498 -0
  - `tests/test_padbridge.py` +245 -0
  - `src/ddg/airlock.py` +38 -6
  - `src/ddg/webui.py` +15 -2
  - `src/ddg/__main__.py` +8 -2
  - `tests/test_airlock.py` +3 -2
  - `src/ddg/__init__.py` +2 -2
  - `setup.py` +1 -1

## 0.6.4-wip: Airlock at about 720p from the real panel size, Wayland connection with X11 fallback, size/connection choices (2026-11-10)

- 11 files changed, 253 insertions(+), 22 deletions(-)
- Main code changes:
  - `src/ddg/airlock.py` +147 -15
  - `tests/test_airlock.py` +63 -0
  - `src/ddg/webui.py` +9 -1
  - `src/ddg/__init__.py` +2 -2
  - `setup.py` +1 -1

## 0.6.3-wip: one-step install (app menu + Game Mode tiles), fix Game Mode browser going offline (2026-11-08)

- 13 files changed, 246 insertions(+), 16 deletions(-)
- Main code changes:
  - `src/ddg/appinstall.py` +87 -0
  - `tests/test_gamemode.py` +64 -0
  - `src/ddg/webui.py` +29 -4
  - `DeckDeckGoose-Install-Linux.sh` +23 -0
  - `src/ddg/gamemode.py` +15 -6
  - `src/ddg/__main__.py` +8 -0
  - `src/ddg/core.py` +4 -0
  - `src/ddg/__init__.py` +2 -2
  - `setup.py` +1 -1

## 0.6.2-wip: smaller package (compressed PDF manual and tile artwork, history trimmed to last 3 builds) (2026-11-08)

- 8 files changed, 57 insertions(+), 12 deletions(-)
- Main code changes:
  - `tools/make_pdf.py` +34 -1
  - `tools/make_art.py` +15 -6
  - `src/ddg/__init__.py` +2 -2
  - `setup.py` +1 -1

## 0.6.1-wip: Airlock closes leftover desktops (first real ROG Ally test) (2026-11-08)

- 11 files changed, 226 insertions(+), 14 deletions(-)
- Main code changes:
  - `src/ddg/airlock.py` +141 -3
  - `tests/test_airlock.py` +59 -2
  - `src/ddg/webui.py` +4 -1
  - `src/ddg/__main__.py` +2 -2
  - `src/ddg/__init__.py` +2 -2
  - `setup.py` +1 -1

## 0.6.0-wip: Steam library manager on Windows/macOS, bundled Zombie-Deck with Wake tab, Airlock sleep-hook fix (2026-11-07)

- 63 files changed, 6929 insertions(+), 77 deletions(-)
- Main code changes:
  - `src/ddg/shelf/steam.py` +1172 -0
  - `components/zombie-deck/src/zombie-deck` +818 -0
  - `components/zombie-deck/tests/test_zombie_deck.py` +520 -0
  - `tests/test_shelf_edges.py` +489 -0
  - `components/zombie-deck/src/zombie-deck-overlay` +463 -0
  - `tests/test_shelf_windows.py` +453 -0
  - `src/ddg/shelf/winplat.py` +386 -0
  - `components/zombie-deck/install.sh` +223 -0
  - `tests/test_shelf_mac.py` +222 -0
  - `src/ddg/shelf/macplat.py` +204 -0
  - ... and 34 more code files

## 0.5.0-wip: rename to Deck Deck Goose, neon artwork theme, Airlock (2026-11-07)

- 76 files changed, 1940 insertions(+), 241 deletions(-)
- Main code changes:
  - `src/ddg/airlock.py` +647 -0
  - `tests/test_airlock.py` +294 -0
  - `src/{thispc => ddg}/gamemode.py` +170 -73
  - `tools/make_art.py` +180 -0
  - `src/{thispc => ddg}/webui.py` +97 -23
  - `src/{thispc => ddg}/core.py` +85 -13
  - `tools/make_pdf.py` +78 -0
  - `src/ddg/airlock_data/nested-session.sh` +60 -0
  - `src/ddg/airlock_data/probe.sh` +54 -0
  - `tests/test_gamemode.py` +32 -18
  - ... and 20 more code files

## 0.4.0-wip: Sound tab - fix quiet ROG Ally speakers, volume boost (2026-11-06)

- 13 files changed, 659 insertions(+), 11 deletions(-)
- Main code changes:
  - `src/thispc/sound.py` +344 -0
  - `tests/test_sound.py` +175 -0
  - `src/thispc/webui.py` +49 -3
  - `src/thispc/__main__.py` +28 -0
  - `src/thispc/core.py` +11 -1
  - `src/thispc/__init__.py` +2 -2
  - `setup.py` +1 -1

## 0.3.1-wip: find Plasma Keyboard on Nobara/Fedora KDE, add Install keyboard (2026-11-04)

- 11 files changed, 215 insertions(+), 34 deletions(-)
- Main code changes:
  - `src/thispc/osk.py` +121 -15
  - `tests/test_gamemode.py` +58 -1
  - `src/thispc/webui.py` +8 -4
  - `src/thispc/__main__.py` +3 -1
  - `src/thispc/__init__.py` +2 -2
  - `setup.py` +1 -1

## This PC for Linux 0.3.0-wip: Game Mode tile, on-screen keyboard, safety fixes (2026-11-04)

- 32 files changed, 1405 insertions(+), 80 deletions(-)
- Main code changes:
  - `src/thispc/gamemode.py` +315 -0
  - `tests/test_review_fixes.py` +222 -0
  - `tests/test_gamemode.py` +205 -0
  - `src/thispc/osk.py` +194 -0
  - `src/thispc/steam.py` +147 -33
  - `src/thispc/vdf.py` +105 -3
  - `src/thispc/webui.py` +72 -16
  - `src/thispc/__main__.py` +32 -5
  - `src/thispc/core.py` +25 -8
  - `tests/test_steam.py` +5 -2
  - ... and 3 more code files

## This PC for Linux 0.2.1-wip: compression estimate and edge-case fixes (2026-11-03)

- 24 files changed, 859 insertions(+), 57 deletions(-)
- Main code changes:
  - `tests/test_edges.py` +243 -0
  - `src/thispc/steam.py` +210 -22
  - `src/thispc/compress.py` +191 -0
  - `tests/test_compress.py` +85 -0
  - `src/thispc/core.py` +25 -6
  - `tests/test_steam.py` +25 -2
  - `src/thispc/webui.py` +13 -6
  - `tests/fake_steam.py` +5 -1
  - `src/thispc/__main__.py` +5 -1
  - `src/thispc/__init__.py` +2 -2
  - ... and 2 more code files

## This PC for Linux 0.2.0-wip: Steam game manager (2026-11-03)

- 27 files changed, 2115 insertions(+), 98 deletions(-)
- Main code changes:
  - `src/thispc/steam.py` +948 -0
  - `tests/test_steam.py` +380 -0
  - `src/thispc/webui.py` +189 -22
  - `src/thispc/vdf.py` +125 -0
  - `tests/fake_steam.py` +104 -0
  - `src/thispc/jobs.py` +102 -0
  - `src/thispc/__main__.py` +74 -2
  - `src/thispc/core.py` +21 -2
  - `src/thispc/__init__.py` +3 -3
  - `setup.py` +2 -2
  - ... and 1 more code files

## This PC for Linux 0.1.0-wip: first work-in-progress release (2026-10-31)

- 37 files changed, 1529 insertions(+)
- Main code changes:
  - `src/thispc/core.py` +598 -0
  - `src/thispc/webui.py` +193 -0
  - `tests/test_core.py` +192 -0
  - `src/thispc/__main__.py` +75 -0
  - `ThisPC-Linux.sh` +18 -0
  - `setup.py` +17 -0
  - `src/thispc/__init__.py` +7 -0
