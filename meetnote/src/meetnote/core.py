"""The app's core, shared by the window and the command line.

It allows one recording at a time, runs a transcriber process per session that
has audio left to transcribe, recovers sessions after a crash, and reports what
happens through `emit(kind, data)`.
"""

from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from . import config, paths, session, transcriber
from .durable import JsonlTail
from .recorder import Recorder
from .session import Session

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], None]


def acquire_app_lock() -> int | None:
    """Hold the single-instance lock for this process's lifetime; None if meetnote already runs."""
    folder = paths.state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / "app.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


class Controller:
    def __init__(self, cfg: config.Config | None = None, emit: Emit | None = None,
                 poll_devices: bool = True, transcriber_cmd: list[str] | None = None):
        self.cfg = cfg or config.load()
        self.emit: Emit = emit or (lambda kind, data: None)
        self.poll_devices = poll_devices
        # The command that transcribes one session folder; tests replace it.
        self.transcriber_cmd = transcriber_cmd or [
            sys.executable, "-m", "meetnote.transcriber", "--threads", str(self.cfg.asr_threads)]
        self.session: Session | None = None  # the session being recorded
        self.recorder: Recorder | None = None
        self._transcribers: dict[str, subprocess.Popen] = {}
        self._last_spawn: dict[str, float] = {}
        self._tails: dict[str, JsonlTail] = {}
        self._lock = threading.RLock()

    # --- recording ------------------------------------------------------------

    def recover(self) -> None:
        """Continue a recording whose app died, and finish transcripts left incomplete."""
        for s in session.list_sessions():
            if s.status == "recording":
                rec = Recorder.adopt(s, **self._recorder_options())
                if rec.streams and self.recorder is None:
                    self.session, self.recorder = s, rec
                    rec.start()
                    self.emit("adopted", {"id": s.id})
                else:
                    if rec.streams:
                        rec.stop()
                    s.update(status="interrupted")
                    self.emit("interrupted", {"id": s.id})
            if transcriber.needs_work(s):
                self.ensure_transcriber(s)

    def start(self, title: str = "") -> Session:
        with self._lock:
            if self.recorder is not None:
                raise RuntimeError("already recording")
            s = Session.create(title)
            self._begin(s)
            return s

    def resume(self, session_id: str) -> Session:
        """Record more into an existing session, for example after an interruption."""
        with self._lock:
            if self.recorder is not None:
                raise RuntimeError("already recording")
            s = session.load(session_id)
            self._begin(s)
            return s

    def _begin(self, s: Session) -> None:
        self.session = s
        self.recorder = Recorder(s, **self._recorder_options())
        self.recorder.start()
        self.ensure_transcriber(s)
        self.emit("recording", {"id": s.id, "title": s.title})

    def stop(self) -> Session | None:
        with self._lock:
            rec, s = self.recorder, self.session
            if rec is None or s is None:
                return None
            rec.stop()
            self.recorder = None
            self.emit("stopped", {"id": s.id})
            return s  # the transcriber finishes the rest and exits by itself

    def set_mic(self, on: bool) -> None:
        with self._lock:
            if self.recorder is not None:
                self.recorder.set_mic(on)
            self.emit("mic", {"on": on})

    def _recorder_options(self) -> dict[str, Any]:
        return {"max_hours": self.cfg.max_hours, "poll_devices": self.poll_devices,
                "on_levels": lambda levels: self.emit("levels", levels),
                "on_device": lambda src, name: self.emit("device", {"src": src, "name": name})}

    # --- transcription ----------------------------------------------------------

    def ensure_transcriber(self, s: Session) -> None:
        """Start a transcriber for `s` unless one runs.

        A transcriber left by a crashed app keeps working and holds the session's
        lock; then nothing is started. Attempts are at least 5 s apart.
        """
        proc = self._transcribers.get(s.id)
        if proc is not None and proc.poll() is None:
            return
        now = time.monotonic()
        if now - self._last_spawn.get(s.id, -1e9) < 5.0:
            return
        self._last_spawn[s.id] = now
        probe = transcriber.acquire_lock(s.folder)
        if probe is None:
            return  # another transcriber works on this session
        os.close(probe)
        logs = paths.state_dir()
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "transcriber.log", "ab") as log_file:
            self._transcribers[s.id] = subprocess.Popen(
                [*self.transcriber_cmd, str(s.folder)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log_file)

    def transcribing(self, session_id: str) -> bool:
        proc = self._transcribers.get(session_id)
        return proc is not None and proc.poll() is None

    def poll(self) -> None:
        """Call a few times a second: reports new lines and keeps transcribers running."""
        with self._lock:
            if self.recorder is not None and self.session is not None:
                if not self.transcribing(self.session.id):
                    self.ensure_transcriber(self.session)  # it crashed or gave up while we record
                self._tails.setdefault(self.session.id, JsonlTail(self.session.transcript_path))
            for sid in list(self._tails):
                for rec in self._tails[sid].poll():
                    if rec.get("type") == "line":
                        self.emit("line", {"id": sid, **rec})
            for sid, proc in list(self._transcribers.items()):
                if proc.poll() is not None:
                    del self._transcribers[sid]
                    self.emit("transcribed", {"id": sid, "code": proc.returncode})

    def watch(self, session_id: str, tail: JsonlTail) -> None:
        """Report lines appended to a session's transcript after `tail`'s position."""
        with self._lock:
            self._tails[session_id] = tail

    def shutdown(self) -> None:
        """Stop recording; transcribers finish their work in the background."""
        self.stop()
