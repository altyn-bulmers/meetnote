"""Helpers that make writes survive a crash or a power loss.

Rules used throughout meetnote:
- whole files are replaced atomically (temp file, fsync, rename, fsync of the folder);
- logs are JSON Lines, appended and fsynced line by line;
- readers ignore a last line without its newline, which is either still being
  written or was cut by a crash.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator


def fsync_dir(folder: Path) -> None:
    """Make new or renamed directory entries in `folder` durable."""
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: str | bytes) -> None:
    """Replace `path`; after a crash it holds either the old or the new content."""
    raw = data.encode() if isinstance(data, str) else data
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def write_json(path: Path, obj: Any) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def repair_tail(path: Path) -> None:
    """Cut a partial last line, which a crash during an append leaves behind."""
    if not path.exists():
        return
    with open(path, "rb+") as f:
        size = f.seek(0, os.SEEK_END)
        if size == 0:
            return
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return
        # Walk back in blocks to the last newline.
        pos = size
        cut = 0
        while pos > 0:
            start = max(0, pos - 65536)
            f.seek(start)
            block = f.read(pos - start)
            idx = block.rfind(b"\n")
            if idx >= 0:
                cut = start + idx + 1
                break
            pos = start
        f.truncate(cut)
        f.flush()
        os.fsync(f.fileno())


class JsonlWriter:
    """Appends one JSON object per line and fsyncs each line."""

    def __init__(self, path: Path):
        existed = path.exists()
        repair_tail(path)
        self.path = path
        self._f = open(path, "ab")
        if not existed:
            fsync_dir(path.parent)

    def append(self, obj: dict) -> None:
        self._f.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        self._f.flush()
        os.fsync(self._f.fileno())

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_jsonl(path: Path) -> Iterator[dict]:
    """Yield every complete line of a JSON Lines file."""
    if not path.exists():
        return
    with open(path, "rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


class JsonlTail:
    """Reads the complete lines appended to a JSON Lines file since the last call."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0

    def poll(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            for raw in f:
                if not raw.endswith(b"\n"):
                    break
                self.offset += len(raw)
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        return out
