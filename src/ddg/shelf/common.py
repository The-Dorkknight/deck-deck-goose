# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Small shared helpers: result steps, sizes, settings file, long paths."""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

IS_WINDOWS = os.name == "nt"


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def human_size(n: int) -> str:
    n = int(n)
    if n >= 10 ** 12:
        return "%.1f TB" % (n / 1e12)
    if n >= 10 ** 10:
        return "%d GB" % round(n / 1e9)
    if n >= 10 ** 9:
        return "%.1f GB" % (n / 1e9)
    if n >= 10 ** 6:
        return "%d MB" % round(n / 1e6)
    return "%d kB" % max(0, round(n / 1e3))


def config_dir() -> Path:
    """Where settings live: %APPDATA%\\Deck Deck Goose on Windows, Application Support on a Mac, the
    normal config folder elsewhere (and in tests)."""
    if os.environ.get("DDG_FAKE_WIN") or os.environ.get("DDG_FAKE_MAC") or os.environ.get("DDG_PLATFORM"):
        return Path(os.environ.get("HOME") or str(Path.home())) / ".config" / "deck-deck-goose"
    if IS_WINDOWS and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "Deck Deck Goose"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Deck Deck Goose"
    return Path(os.environ.get("HOME") or str(Path.home())) / ".config" / "deck-deck-goose"


_path = os.path       # swapped for ntpath in tests to check the Windows rules


def lp(path, windows: bool = IS_WINDOWS) -> str:
    """Long-path form for Windows (paths over 260 characters are common in
    games); unchanged elsewhere."""
    p = str(path)
    if not windows or p.startswith("\\\\?\\"):
        return p
    p = _path.abspath(p)
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def unlp(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def norm(path) -> str:
    """For comparing paths: absolute, real, and case-folded on Windows."""
    return os.path.normcase(os.path.realpath(unlp(str(path))))


_CONFIG_LOCK = threading.Lock()


def load_config() -> dict:
    try:
        return json.loads((config_dir() / "shelf.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    tmp = config_dir() / "shelf.json.tmp"
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(config_dir() / "shelf.json"))


def update_config(changes: Optional[dict] = None, remove: Tuple[str, ...] = (),
                  merge_key: str = "", merge: Optional[dict] = None,
                  drop_from: Tuple[str, str] = ("", "")) -> dict:
    with _CONFIG_LOCK:
        cfg = load_config()
        cfg.update(changes or {})
        for k in remove:
            cfg.pop(k, None)
        if merge_key:
            cfg.setdefault(merge_key, {}).update(merge or {})
        if drop_from[0]:
            cfg.get(drop_from[0], {}).pop(drop_from[1], None)
        save_config(cfg)
        return cfg
