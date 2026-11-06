"""Transcribe a session's audio into transcript.jsonl, live or after a crash.

Runs as its own process, with one thread at normal priority:
    python -m meetnote.transcriber <session-folder> [--threads 1]
It reads the segment files back from disk, so live transcription and recovery
are the same code: after any crash it resumes where its last line or checkpoint
ended. A lock file makes sure only one transcriber writes a session.

transcript.jsonl holds one JSON object per line, each fsynced:
  {"type": "line", "src": "others", "seg": "audio/others-….pcm", "s0": 3076800, "s1": 3149440,
   "w0": 1762434192.48, "w1": 1762434197.02, "t0": "2026-11-06T14:03:12.480+01:00", "text": "…"}
  {"type": "checkpoint", "seg": "…", "sample": 4800000}     resume point inside silence
  {"type": "segment_done", "seg": "…", "samples": 5760000}  the segment is fully transcribed
s0/s1 are sample offsets in the segment file; w0/w1 are Unix times from its anchors.
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .durable import JsonlTail, JsonlWriter, read_jsonl, write_json
from .recorder import RATE, anchors_path, sample_to_wall
from .session import Session

log = logging.getLogger("meetnote.transcriber")

WINDOW = 512  # samples per VAD call: Silero's window at 16 kHz
MAX_UTTERANCE = 20.0  # seconds; the VAD tries to split speech here
HARD_CUT = 25.0  # seconds; speech still running is cut here (the VAD limit is soft)
CHECKPOINT_EVERY = 30.0  # seconds of silence between resume points
LOOKBACK = 0.5  # seconds; the VAD may still assign audio this far back to new speech
STATUS_EVERY = 2.0  # seconds between status-file updates


class Engine:
    """Silero VAD and Parakeet TDT 0.6B v3, both run by sherpa-onnx."""

    def __init__(self, threads: int = 1):
        import sherpa_onnx as so

        from . import models

        asr = models.model_dir(models.PARAKEET)
        self._recognizer = so.OfflineRecognizer.from_transducer(
            encoder=str(asr / "encoder.int8.onnx"),
            decoder=str(asr / "decoder.int8.onnx"),
            joiner=str(asr / "joiner.int8.onnx"),
            tokens=str(asr / "tokens.txt"),
            num_threads=threads,
            model_type="nemo_transducer",
            decoding_method="greedy_search",
        )
        cfg = so.VadModelConfig()
        cfg.silero_vad.model = str(models.model_dir(models.VAD) / "silero_vad.onnx")
        cfg.silero_vad.threshold = 0.5
        cfg.silero_vad.min_silence_duration = 0.5
        cfg.silero_vad.min_speech_duration = 0.25
        cfg.silero_vad.max_speech_duration = MAX_UTTERANCE
        cfg.silero_vad.window_size = WINDOW
        cfg.sample_rate = RATE
        cfg.num_threads = 1
        self._vad_config = cfg
        self._so = so
        self.name = models.PARAKEET
        self.transcribe(np.zeros(RATE, np.float32))  # warm-up: the first decode is slower

    def new_vad(self):
        return self._so.VoiceActivityDetector(self._vad_config, buffer_size_in_seconds=HARD_CUT + 10)

    def transcribe(self, samples: np.ndarray) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(RATE, samples)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()


def read_progress(transcript: Path) -> tuple[set[str], dict[str, int]]:
    """Segments that are finished, and the sample each other segment resumes from."""
    done: set[str] = set()
    resume: dict[str, int] = {}
    for rec in read_jsonl(transcript):
        seg = rec.get("seg")
        if rec.get("type") == "segment_done":
            done.add(seg)
        elif rec.get("type") == "line":
            resume[seg] = max(resume.get(seg, 0), rec["s1"])
        elif rec.get("type") == "checkpoint":
            resume[seg] = max(resume.get(seg, 0), rec["sample"])
    return done, resume


def needs_work(session: Session) -> bool:
    done, _ = read_progress(session.transcript_path)
    return session.status == "recording" or any(
        s["file"] not in done for s in session.meta["segments"])


def fast_cpus() -> set[int] | None:
    """CPUs whose top clock is within 10% of the fastest: the performance cores.

    On hybrid CPUs (Intel 12th gen and later) a short, intermittent task such as
    one decode per sentence is often placed on an efficiency core, where decoding
    is up to 5x slower (experiment 02). None means all CPUs are alike.
    """
    try:
        allowed = os.sched_getaffinity(0)
        top_clock = {}
        for cpu in allowed:
            with open(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq") as f:
                top_clock[cpu] = int(f.read())
    except (OSError, ValueError):
        return None
    fastest = max(top_clock.values())
    fast = {cpu for cpu, clock in top_clock.items() if clock >= 0.9 * fastest}
    return fast if fast and fast != allowed else None


def acquire_lock(folder: Path) -> int | None:
    """Hold the session's transcriber lock; None if another transcriber has it."""
    fd = os.open(folder / "transcriber.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


class SegmentJob:
    """Transcribes one segment file from its resume point, following it while it grows."""

    def __init__(self, folder: Path, seg: dict, engine, writer: JsonlWriter, start: int):
        self.file, self.src = seg["file"], seg["src"]
        self.path = folder / self.file
        self.engine, self.writer = engine, writer
        self.vad = engine.new_vad()
        self.base = start  # file sample where the current VAD stream starts
        self.pos = start  # next file sample to read
        self.last_end = start  # utterances ending before this were already written
        self.last_point = start  # last resume point written
        self.speech_since: int | None = None
        self.size = 0
        self.last_growth = time.monotonic()
        self._anchor_tail = JsonlTail(anchors_path(self.path))
        self._anchors: list[tuple[int, float]] = []
        self._fallback_wall = seg.get("started") or time.time()

    def available(self) -> int:
        """Whole VAD windows written to the file but not read yet."""
        size = self.path.stat().st_size if self.path.exists() else 0
        if size != self.size:
            self.size = size
            self.last_growth = time.monotonic()
        n = size // 2 - self.pos
        return n - n % WINDOW

    def step(self, max_seconds: float = 30.0) -> bool:
        n = min(self.available(), int(max_seconds * RATE) // WINDOW * WINDOW)
        if n <= 0:
            return False
        audio = self._read(self.pos, n)
        for i in range(0, n, WINDOW):
            self.vad.accept_waveform(audio[i:i + WINDOW])
            self.pos += WINDOW
            self._drain()
            self._cut_long_speech()
        self._maybe_checkpoint()
        return True

    def finish(self) -> None:
        """The segment is closed: read its tail, flush the VAD and mark it done."""
        rest = (self.path.stat().st_size // 2 - self.pos) if self.path.exists() else 0
        if rest > 0:
            audio = self._read(self.pos, rest)
            for i in range(0, rest, WINDOW):
                chunk = audio[i:i + WINDOW]
                if len(chunk) < WINDOW:
                    chunk = np.concatenate([chunk, np.zeros(WINDOW - len(chunk), np.float32)])
                self.vad.accept_waveform(chunk)
                self._drain()
            self.pos += rest
        self.flush()
        self.writer.append({"type": "segment_done", "seg": self.file, "samples": self.pos})

    def flush(self) -> None:
        self.vad.flush()
        self._drain()

    def _read(self, start: int, n: int) -> np.ndarray:
        with open(self.path, "rb") as f:
            f.seek(start * 2)
            raw = f.read(n * 2)
        return np.frombuffer(raw[: len(raw) - len(raw) % 2], dtype="<i2").astype(np.float32) / 32768

    def _drain(self) -> None:
        while not self.vad.empty():
            seg = self.vad.front
            samples = np.asarray(seg.samples, dtype=np.float32)
            s0 = self.base + seg.start
            self.vad.pop()
            self.speech_since = None
            s1 = s0 + len(samples)
            if s1 <= self.last_end:  # written before a restart
                continue
            started = time.monotonic()
            text = self.engine.transcribe(samples)
            took = time.monotonic() - started
            self.last_end = s1
            if text:
                line = self._line(s0, s1, text)
                self.writer.append(line)
                log.info("%s: %.1f s of speech decoded in %.2f s; line written %.1f s after the speech ended",
                         self.src, len(samples) / RATE, took, time.time() - line["w1"])

    def _cut_long_speech(self) -> None:
        if not self.vad.is_speech_detected():
            self.speech_since = None
        elif self.speech_since is None:
            self.speech_since = self.pos
        elif self.pos - self.speech_since > HARD_CUT * RATE:
            self.flush()
            self.vad.reset()
            self.base = self.pos
            self.speech_since = None

    def _maybe_checkpoint(self) -> None:
        if self.vad.is_speech_detected() or not self.vad.empty():
            return
        point = self.pos - int(LOOKBACK * RATE)
        if point - max(self.last_point, self.last_end) >= CHECKPOINT_EVERY * RATE:
            self.writer.append({"type": "checkpoint", "seg": self.file, "sample": point})
            self.last_point = point

    def _line(self, s0: int, s1: int, text: str) -> dict:
        self._anchors += [(a["bytes"] // 2, a["wall"]) for a in self._anchor_tail.poll()]
        anchors = self._anchors or [(0, self._fallback_wall)]
        w0, w1 = sample_to_wall(anchors, s0), sample_to_wall(anchors, s1)
        return {"type": "line", "src": self.src, "seg": self.file, "s0": s0, "s1": s1,
                "w0": round(w0, 3), "w1": round(w1, 3),
                "t0": datetime.fromtimestamp(w0).astimezone().isoformat(timespec="milliseconds"),
                "text": text}


class Transcriber:
    """Transcribes every segment of one session until nothing is left to do."""

    def __init__(self, folder: Path, engine, idle_exit: float = 120.0, poll: float = 0.2):
        self.folder, self.engine = folder, engine
        self.idle_exit, self.poll = idle_exit, poll

    def run(self) -> None:
        session = Session(self.folder)
        done, resume = read_progress(session.transcript_path)
        jobs: dict[str, SegmentJob] = {}
        last_status = 0.0
        with JsonlWriter(session.transcript_path) as writer:
            while True:
                try:
                    session.reload()
                except (OSError, ValueError):
                    pass  # replaced atomically, so this is only a transient read error
                closed = set()
                for seg in session.meta["segments"]:
                    if seg["closed"]:
                        closed.add(seg["file"])
                    if seg["file"] not in done and seg["file"] not in jobs:
                        jobs[seg["file"]] = SegmentJob(self.folder, seg, self.engine, writer,
                                                       resume.get(seg["file"], 0))
                worked = False
                for file, job in list(jobs.items()):
                    worked |= job.step()
                    if file in closed and job.available() == 0:
                        job.finish()
                        done.add(file)
                        del jobs[file]
                now = time.monotonic()
                if now - last_status >= STATUS_EVERY:
                    self._write_status(jobs, done)
                    last_status = now
                if not jobs and session.status != "recording":
                    break
                if not worked:
                    if jobs and all(now - j.last_growth > self.idle_exit for j in jobs.values()):
                        log.info("no audio for %.0f s and no segment closed; the app is gone", self.idle_exit)
                        for job in jobs.values():
                            job.flush()
                        break
                    time.sleep(self.poll)
        self._write_status(jobs, done, finished=True)

    def _write_status(self, jobs: dict[str, SegmentJob], done: set[str], finished: bool = False) -> None:
        write_json(self.folder / "transcriber.status.json", {
            "pid": os.getpid(), "updated": round(time.time(), 3), "finished": finished,
            "engine": getattr(self.engine, "name", "?"),
            "position": {file: job.pos for file, job in jobs.items()}, "done": sorted(done),
        })


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m meetnote.transcriber")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--idle-exit", type=float, default=120.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s transcriber %(levelname)s %(message)s")
    # Normal priority on purpose: at nice 10 the scheduler moves decoding to the
    # efficiency cores, which decode up to 5x slower (experiment 02). One thread
    # cannot starve the meeting app on a multi-core laptop.
    lock = acquire_lock(args.folder)
    if lock is None:
        log.info("another transcriber is working on %s", args.folder.name)
        return 0
    if not needs_work(Session(args.folder)):
        return 0
    cpus = fast_cpus()
    if cpus:
        os.sched_setaffinity(0, cpus)  # before the model loads, so its threads inherit it
        log.info("running on the performance cores %s", sorted(cpus))
    log.info("transcribing %s", args.folder.name)
    Transcriber(args.folder, Engine(args.threads), idle_exit=args.idle_exit).run()
    log.info("finished %s", args.folder.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
