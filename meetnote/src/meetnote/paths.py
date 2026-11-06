"""Where meetnote keeps its files, following the XDG base directories.

Setting MEETNOTE_HOME puts everything under that one folder; the tests use it.
"""

from __future__ import annotations

import os
from pathlib import Path

APP = "meetnote"


def _root(xdg_var: str, default: str, sub: str) -> Path:
    home = os.environ.get("MEETNOTE_HOME")
    if home:
        return Path(home) / sub
    base = os.environ.get(xdg_var)
    return (Path(base) if base else Path.home() / default) / APP


def data_dir() -> Path:
    return _root("XDG_DATA_HOME", ".local/share", "data")


def state_dir() -> Path:
    return _root("XDG_STATE_HOME", ".local/state", "state")


def config_dir() -> Path:
    return _root("XDG_CONFIG_HOME", ".config", "config")


def sessions_dir() -> Path:
    return data_dir() / "sessions"


def models_dir() -> Path:
    """MEETNOTE_MODELS_DIR overrides this, for example to share models between homes."""
    override = os.environ.get("MEETNOTE_MODELS_DIR")
    return Path(override) if override else data_dir() / "models"
