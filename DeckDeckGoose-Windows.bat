@echo off
rem Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
rem Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
rem Deck Deck Goose - double-click launcher for Windows. Opens the Steam library manager (Games tab) in your browser.
cd /d "%~dp0"
set PYTHONPATH=src
where py >nul 2>nul && (py -3 -m ddg gui & goto done)
where python >nul 2>nul && (python -m ddg gui & goto done)
echo Python 3 was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
:done
if errorlevel 1 echo Deck Deck Goose stopped with an error.
pause
