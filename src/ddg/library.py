# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The Steam library manager for whichever system this is: ddg.steam on Linux,
ddg.shelf.steam on Windows and macOS. Same function names either way; the window
and command line talk only to this module."""
from __future__ import annotations

import importlib


def platform() -> str:
    from .shelf import plat
    return plat.name()


def is_linux() -> bool:
    return platform() == "linux"


def backend():
    return importlib.import_module(".steam" if is_linux() else ".shelf.steam", __package__)


def __getattr__(attr: str):
    if attr.startswith("__"):
        raise AttributeError(attr)
    return getattr(backend(), attr)


def algorithms() -> tuple:
    if is_linux():
        return ()
    from .shelf import plat
    return tuple(plat.ALGORITHMS)
