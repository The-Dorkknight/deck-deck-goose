# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Picks the Windows or macOS platform module when used (so tests can switch with DDG_PLATFORM)."""
from __future__ import annotations

import os
import sys


def name() -> str:
    forced = os.environ.get("DDG_PLATFORM", "").lower()
    if forced in ("windows", "mac", "linux"):
        return forced
    return {"win32": "windows", "cygwin": "windows", "darwin": "mac"}.get(sys.platform, "linux")


def impl():
    from . import macplat, winplat
    return macplat if name() == "mac" else winplat


def __getattr__(attr: str):
    if attr.startswith("__"):
        raise AttributeError(attr)
    return getattr(impl(), attr)
