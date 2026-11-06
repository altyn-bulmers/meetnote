"""Names of the current default input and output, read with pactl."""

from __future__ import annotations

import json
import os
import subprocess

_ENV = {**os.environ, "LC_ALL": "C"}
_descriptions: dict[tuple[str, str], str] = {}


def _pactl(*args: str) -> str:
    return subprocess.run(["pactl", *args], capture_output=True, text=True,
                          timeout=3, env=_ENV).stdout.strip()


def _describe(kind: str, name: str) -> str:
    if not name:
        return ""
    if (kind, name) not in _descriptions:
        try:
            for item in json.loads(_pactl("--format=json", "list", kind) or "[]"):
                _descriptions[(kind, item["name"])] = item.get("description") or item["name"]
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            pass
    return _descriptions.get((kind, name), name)


def default_names() -> dict[str, str]:
    """{"me": default input, "others": default output}, as human-readable names."""
    try:
        source = _pactl("get-default-source")
        sink = _pactl("get-default-sink")
    except (OSError, subprocess.SubprocessError):
        return {"me": "", "others": ""}
    return {"me": _describe("sources", source), "others": _describe("sinks", sink)}
