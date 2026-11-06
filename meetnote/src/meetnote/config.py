"""User settings, read from ~/.config/meetnote/config.toml when that file exists."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields

from . import paths


@dataclass(frozen=True)
class Config:
    asr_threads: int = 1  # CPU threads for the speech model; 1 is the most CPU-efficient (experiment 01)
    ask_model: str = "sonnet"  # Claude model for questions during a meeting
    summary_model: str = "opus"  # Claude model for the summary at Stop
    context_minutes: int = 10  # minutes of transcript sent with a question
    max_hours: float = 6.0  # a recorder left behind by a crashed app stops after this


def load() -> Config:
    path = paths.config_dir() / "config.toml"
    if not path.is_file():
        return Config()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in data.items() if k in known})
