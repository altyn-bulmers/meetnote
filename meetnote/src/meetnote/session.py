"""A meeting session: one folder with its audio, transcript, notes, questions and summary.

Folder layout, inside ~/.local/share/meetnote/sessions/<id>/ (writer in brackets):
    session.json                       metadata, replaced atomically [app]
    audio/<src>-<time>.pcm             raw s16le 16 kHz mono [parec]
    audio/<src>-<time>.anchors.jsonl   {"bytes", "wall", "mono"} once a second [app]
    transcript.jsonl                   lines and resume checkpoints [transcriber]
    notes.md, qa.jsonl, summary.md     [app]
<src> is "me" (microphone) or "others" (what the laptop plays).
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import paths
from .durable import atomic_write, fsync_dir, read_json, write_json

ID_RE = re.compile(r"^\d{8}-\d{6}(-[a-z0-9-]{1,40})?$")
SOURCES = ("me", "others")

# One lock per session folder, shared by every Session object in this process.
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(folder: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(folder), threading.RLock())


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class Session:
    def __init__(self, folder: Path):
        self.folder = folder
        self._lock = _lock_for(folder)
        self.meta: dict[str, Any] = read_json(folder / "session.json")

    @classmethod
    def create(cls, title: str = "", when: datetime | None = None) -> "Session":
        when = (when or datetime.now()).astimezone()
        sid = when.strftime("%Y%m%d-%H%M%S")
        if slug(title):
            sid += "-" + slug(title)
        root = paths.sessions_dir()
        root.mkdir(parents=True, exist_ok=True)
        folder = root / sid
        folder.mkdir()
        (folder / "audio").mkdir()
        write_json(folder / "session.json", {
            "id": sid,
            "title": title.strip() or when.strftime("Meeting %Y-%m-%d %H:%M"),
            "created": when.isoformat(timespec="seconds"),
            "status": "stopped",  # recording | stopped | interrupted
            "segments": [],  # {"file", "src", "pid", "started", "closed"}
            "devices": [],  # {"t", "src", "device"}
            "summary": None,  # None | "pending" | "done"
        })
        fsync_dir(folder)
        fsync_dir(root)
        return cls(folder)

    # --- metadata -------------------------------------------------------------

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def title(self) -> str:
        return self.meta["title"]

    @property
    def status(self) -> str:
        return self.meta["status"]

    def reload(self) -> None:
        with self._lock:
            self.meta = read_json(self.folder / "session.json")

    def _change(self, edit: Callable[[dict[str, Any]], None]) -> None:
        """Re-read, edit and replace session.json, so no writer overwrites another's change."""
        with self._lock:
            try:
                self.meta = read_json(self.folder / "session.json")
            except (OSError, ValueError):
                pass  # keep the copy in memory; the write below repairs the file
            edit(self.meta)
            write_json(self.folder / "session.json", self.meta)

    def update(self, **changes: Any) -> None:
        self._change(lambda meta: meta.update(changes))

    def add_segment(self, file: str, src: str) -> None:
        segment = {"file": file, "src": src, "pid": None, "started": time.time(), "closed": False}
        self._change(lambda meta: meta["segments"].append(segment))

    def set_segment_pid(self, file: str, pid: int) -> None:
        self._edit_segment(file, pid=pid)

    def close_segment(self, file: str) -> None:
        self._edit_segment(file, closed=True)

    def _edit_segment(self, file: str, **changes: Any) -> None:
        def edit(meta: dict[str, Any]) -> None:
            for seg in meta["segments"]:
                if seg["file"] == file:
                    seg.update(changes)
        self._change(edit)

    def open_segments(self) -> list[dict]:
        return [s for s in self.meta["segments"] if not s["closed"]]

    def add_device(self, src: str, device: str) -> None:
        entry = {"t": now_iso(), "src": src, "device": device}
        self._change(lambda meta: meta["devices"].append(entry))

    # --- files ----------------------------------------------------------------

    @property
    def audio_dir(self) -> Path:
        return self.folder / "audio"

    @property
    def transcript_path(self) -> Path:
        return self.folder / "transcript.jsonl"

    @property
    def qa_path(self) -> Path:
        return self.folder / "qa.jsonl"

    @property
    def summary_path(self) -> Path:
        return self.folder / "summary.md"

    @property
    def notes_path(self) -> Path:
        return self.folder / "notes.md"

    def read_notes(self) -> str:
        return self.notes_path.read_text(encoding="utf-8") if self.notes_path.exists() else ""

    def write_notes(self, text: str) -> None:
        atomic_write(self.notes_path, text)


def load(session_id: str) -> Session:
    """Open a session by id; the id is validated so it cannot point outside the sessions folder."""
    if not ID_RE.match(session_id):
        raise ValueError(f"not a session id: {session_id!r}")
    folder = paths.sessions_dir() / session_id
    if not (folder / "session.json").is_file():
        raise FileNotFoundError(f"no session {session_id}")
    return Session(folder)


def list_sessions() -> list[Session]:
    """All sessions, newest first. Folders without a readable session.json are skipped."""
    root = paths.sessions_dir()
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(root.iterdir(), reverse=True):
        if ID_RE.match(folder.name) and (folder / "session.json").is_file():
            try:
                out.append(Session(folder))
            except (OSError, ValueError):
                continue
    return out
