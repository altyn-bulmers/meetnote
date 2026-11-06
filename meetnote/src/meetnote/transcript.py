"""Read a session's transcript for display and for Claude."""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from .durable import read_jsonl

LABEL = {"me": "Me", "others": "Others"}


def lines(path: Path) -> list[dict]:
    """Transcript lines in time order, with echo lines flagged."""
    out = [r for r in read_jsonl(path) if r.get("type") == "line"]
    out.sort(key=lambda r: r["w0"])
    return mark_echo(out)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def mark_echo(items: list[dict], threshold: float = 0.6, slack: float = 1.0) -> list[dict]:
    """Flag "me" lines that repeat an overlapping "others" line.

    With laptop speakers, the microphone also hears the meeting. Such a line is
    flagged with "echo": True; it stays in the file but is hidden in the UI and
    left out of what Claude reads.
    """
    others = [r for r in items if r["src"] == "others"]
    for r in items:
        r["echo"] = False
        if r["src"] != "me":
            continue
        mine = _norm(r["text"])
        for o in others:
            if o["w0"] - slack <= r["w1"] and r["w0"] <= o["w1"] + slack:
                if SequenceMatcher(None, mine, _norm(o["text"])).ratio() >= threshold:
                    r["echo"] = True
                    break
    return items


def clock(w: float) -> str:
    return datetime.fromtimestamp(w).strftime("%H:%M:%S")


def as_text(items: list[dict], since: float | None = None) -> str:
    """Plain text for Claude, one "[14:03:12] Others: …" line each; echo lines are left out."""
    return "\n".join(f"[{clock(r['w0'])}] {LABEL.get(r['src'], r['src'])}: {r['text']}"
                     for r in items if not r.get("echo") and (since is None or r["w1"] >= since))
