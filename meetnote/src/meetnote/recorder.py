"""Record the microphone ("me") and what the laptop plays ("others").

Each stream is a parec process that writes raw PCM straight into its own segment
file. parec runs in its own session under `timeout`, so it keeps recording if
meetnote crashes, and stops by itself after `max_hours` if meetnote never comes
back. While meetnote runs, the recorder:
- fsyncs each file once a second and appends an anchor {bytes, wall, mono}, from
  which transcript times are computed;
- restarts parec in a new segment if it exits during a recording;
- measures the level of each stream for the meters, and the default devices.

The input stream has no device and the output stream records @DEFAULT_MONITOR@.
WirePlumber moves both when the default device changes (experiment 01).
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from . import devices
from .durable import JsonlWriter, fsync_dir, read_jsonl
from .session import SOURCES, Session

log = logging.getLogger(__name__)

RATE = 16000
BYTES_PER_SECOND = 2 * RATE
DEVICE = {"me": None, "others": "@DEFAULT_MONITOR@"}  # None: follow the default input
GAP_SECONDS = 0.3


def device_for(src: str) -> str | None:
    """MEETNOTE_MIC_DEVICE / MEETNOTE_OUTPUT_DEVICE pin a source to a device; tests use them."""
    override = os.environ.get("MEETNOTE_MIC_DEVICE" if src == "me" else "MEETNOTE_OUTPUT_DEVICE")
    return override or DEVICE[src]


def parec_command(src: str, max_hours: float) -> list[str]:
    parec = os.environ.get("MEETNOTE_PAREC") or shutil.which("parec") or "parec"
    cmd = ["timeout", "--signal=TERM", f"{max_hours * 3600:g}s", parec,
           "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=200",
           "--client-name=meetnote", f"--stream-name=meetnote-{src}"]
    device = device_for(src)
    if device:
        cmd.append(f"--device={device}")
    return cmd


def anchors_path(pcm: Path) -> Path:
    return pcm.with_suffix(".anchors.jsonl")


def writer_of(path: Path) -> int | None:
    """Process group of a live process whose stdout is `path`, or None.

    Scans /proc, so it works after a crash without trusting stored PIDs, which a
    reboot can reuse.
    """
    target = str(path)
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            if os.readlink(f"/proc/{entry.name}/fd/1") == target:
                return os.getpgid(int(entry.name))
        except OSError:
            continue
    return None


class Stream:
    """One parec process recording one source into one segment file."""

    def __init__(self, session: Session, src: str, file: str, pgid: int,
                 proc: subprocess.Popen | None = None):
        self.session, self.src, self.file = session, src, file
        self.pgid, self.proc = pgid, proc
        self.path = session.folder / file
        self.started = time.monotonic()
        self.fd = os.open(self.path, os.O_RDONLY)
        self.anchors = JsonlWriter(anchors_path(self.path))
        self.closed = False

    @classmethod
    def launch(cls, session: Session, src: str, max_hours: float) -> "Stream":
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S.%f")[:-3]
        file = f"audio/{src}-{stamp}.pcm"
        path = session.folder / file
        # Record the segment before parec starts, so a crash in between leaves no unknown file.
        session.add_segment(file, src)
        with open(path, "ab") as out:
            proc = subprocess.Popen(parec_command(src, max_hours), stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.DEVNULL,
                                    start_new_session=True, close_fds=True)
        fsync_dir(path.parent)
        session.set_segment_pid(file, proc.pid)
        log.info("recording %s into %s (pid %d)", src, file, proc.pid)
        return cls(session, src, file, proc.pid, proc)

    def alive(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        return writer_of(self.path) == self.pgid

    def tick(self) -> None:
        """Flush the audio to disk and record how much of it exists now."""
        os.fsync(self.fd)
        self.anchors.append({"bytes": os.fstat(self.fd).st_size,
                             "wall": round(time.time(), 3), "mono": round(time.monotonic(), 3)})

    def level(self) -> float:
        """Loudness of the last 100 ms, from 0 (-60 dBFS or less) to 1 (full scale)."""
        size = os.fstat(self.fd).st_size
        n = min(size - size % 2, BYTES_PER_SECOND // 10)
        if n <= 0:
            return 0.0
        x = np.frombuffer(os.pread(self.fd, n, size - n), dtype="<i2").astype(np.float32)
        rms = math.sqrt(float(np.mean(x * x))) / 32768
        return 0.0 if rms <= 1e-6 else min(1.0, max(0.0, (20 * math.log10(rms) + 60) / 60))

    def stop(self, wait: float = 3.0) -> None:
        if self.closed:
            return
        if self.alive():
            try:
                os.killpg(self.pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + wait
            while self.alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            if self.alive():
                try:
                    os.killpg(self.pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self.proc is not None:
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        self.close()

    def close(self) -> None:
        """Final flush and anchor; mark the segment closed."""
        if self.closed:
            return
        self.tick()
        self.anchors.close()
        os.close(self.fd)
        self.closed = True
        self.session.close_segment(self.file)


class Recorder:
    """Records both sources of one session until stopped."""

    def __init__(self, session: Session, max_hours: float = 6.0, poll_devices: bool = True,
                 restart_delay: float = 3.0,
                 on_levels: Callable[[dict[str, float]], None] | None = None,
                 on_device: Callable[[str, str], None] | None = None):
        self.session = session
        self.max_hours = max_hours
        self.poll_devices = poll_devices
        self.restart_delay = restart_delay
        self.on_levels = on_levels
        self.on_device = on_device
        self.streams: dict[str, Stream] = {}
        self.mic_on = True
        self.levels = {src: 0.0 for src in SOURCES}
        self.device_names = {src: "" for src in SOURCES}
        self._last_launch = {src: 0.0 for src in SOURCES}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def adopt(cls, session: Session, **kwargs) -> "Recorder":
        """Take over the parec processes of a session whose app died.

        Segments without a live writer are closed. The returned recorder has no
        streams if nothing survived, for example after a reboot.
        """
        rec = cls(session, **kwargs)
        for seg in session.open_segments():
            path = session.folder / seg["file"]
            pgid = writer_of(path) if path.exists() else None
            if pgid is None:
                session.close_segment(seg["file"])
            else:
                rec.streams[seg["src"]] = Stream(session, seg["src"], seg["file"], pgid)
                log.info("adopted %s recorder (process group %d)", seg["src"], pgid)
        return rec

    @property
    def recording(self) -> bool:
        return self._thread is not None and not self._stop.is_set()

    def start(self) -> None:
        with self._lock:
            self.session.update(status="recording")
            for src in SOURCES:
                if src not in self.streams and (src != "me" or self.mic_on):
                    self._launch(src)
            if self._thread is None:
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="recorder", daemon=True)
                self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        with self._lock:
            for stream in self.streams.values():
                stream.stop()
            self.streams.clear()
            self.session.update(status="stopped")
            self.levels = {src: 0.0 for src in SOURCES}

    def set_mic(self, on: bool) -> None:
        """Mute or unmute the microphone stream; muted speech is never recorded."""
        with self._lock:
            self.mic_on = on
            if on and "me" not in self.streams and self.recording:
                self._launch("me")
            elif not on and "me" in self.streams:
                self.streams.pop("me").stop()
                self.levels["me"] = 0.0

    def _launch(self, src: str) -> None:
        self.streams[src] = Stream.launch(self.session, src, self.max_hours)
        self._last_launch[src] = time.monotonic()

    def _loop(self) -> None:
        next_tick = time.monotonic() + 1.0
        next_devices = 0.0
        while not self._stop.wait(0.2):
            with self._lock:
                if self._stop.is_set():
                    break
                for src in SOURCES:
                    stream = self.streams.get(src)
                    self.levels[src] = stream.level() if stream else 0.0
                now = time.monotonic()
                if now >= next_tick:
                    next_tick = now + 1.0
                    self._tick_streams(now)
                if self.poll_devices and now >= next_devices:
                    next_devices = now + 2.0
                    self._check_devices()
                levels = dict(self.levels)
            if self.on_levels:
                self.on_levels(levels)

    def _tick_streams(self, now: float) -> None:
        for src in SOURCES:
            stream = self.streams.get(src)
            if stream is not None:
                stream.tick()
                if not stream.alive():
                    log.warning("%s recorder exited; continuing in a new segment", src)
                    stream.close()
                    del self.streams[src]
            wanted = src != "me" or self.mic_on
            if (wanted and src not in self.streams
                    and now - self._last_launch[src] >= self.restart_delay):
                self._launch(src)

    def _check_devices(self) -> None:
        names = devices.default_names()
        for src in SOURCES:
            if device_for(src) != DEVICE[src]:
                names[src] = device_for(src)  # pinned by an environment variable
        for src, name in names.items():
            if name and name != self.device_names[src]:
                self.device_names[src] = name
                self.session.add_device(src, name)
                if self.on_device:
                    self.on_device(src, name)


# --- time mapping ------------------------------------------------------------

def load_anchors(pcm: Path) -> list[tuple[int, float]]:
    """(sample index, wall-clock time) pairs for a segment, in order."""
    return [(a["bytes"] // 2, a["wall"]) for a in read_jsonl(anchors_path(pcm))]


def sample_to_wall(anchors: list[tuple[int, float]], sample: int) -> float:
    """Wall-clock time at which `sample` of a segment was recorded.

    Each sample is placed back from the first anchor at or after it, so audio
    that arrived after a stall gets the time it really arrived.
    """
    if not anchors:
        raise ValueError("segment has no anchors")
    for s, w in anchors:
        if s >= sample:
            return w - (s - sample) / RATE
    s, w = anchors[-1]
    return w + (sample - s) / RATE


def gaps(anchors: list[tuple[int, float]]) -> list[tuple[float, float]]:
    """Periods with no audio: wall time ran ahead of the samples by more than 0.3 s."""
    out = []
    for (s0, w0), (s1, w1) in zip(anchors, anchors[1:]):
        audio = (s1 - s0) / RATE
        if (w1 - w0) - audio > GAP_SECONDS:
            out.append((w0, round(w1 - audio, 3)))
    return out
