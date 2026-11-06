"""Measure CPU, RAM and per-utterance delay of Silero VAD + Parakeet TDT v3 here.

Builds a synthetic meeting from the model's four test recordings (English,
French, German, Spanish), separated by 1.5 s of silence and repeated. Feeds it
to the VAD in 512-sample windows, as the app does, transcribes each utterance
once and reports CPU seconds per minute of audio.

Run (capped at 4 GB of address space):
    timeout 600 prlimit --as=4000000000 ../../.venv/bin/python asr_bench.py --threads 4
"""

from __future__ import annotations

import argparse
import resource
import time
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx as so

from meetnote import models

RATE = 16000
WINDOW = 512


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Windowed-sinc low-pass, then linear interpolation (good enough for test speech)."""
    if src == dst:
        return x
    n = np.arange(-50, 51)
    fc = 0.45 * min(src, dst) / src  # cut-off in cycles per input sample
    h = 2 * fc * np.sinc(2 * fc * n) * np.hamming(len(n))
    y = np.convolve(x, h / h.sum(), mode="same")
    t = np.arange(int(len(x) * dst / src)) * (src / dst)
    return np.interp(t, np.arange(len(x)), y).astype(np.float32)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, path
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    return resample(x, rate, RATE)


def cpu_seconds() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--repeat", type=int, default=8)
    args = ap.parse_args()

    asr = models.model_dir(models.PARAKEET)
    t0, c0 = time.perf_counter(), cpu_seconds()
    rec = so.OfflineRecognizer.from_transducer(
        encoder=str(asr / "encoder.int8.onnx"),
        decoder=str(asr / "decoder.int8.onnx"),
        joiner=str(asr / "joiner.int8.onnx"),
        tokens=str(asr / "tokens.txt"),
        num_threads=args.threads,
        model_type="nemo_transducer",
        decoding_method="greedy_search",
    )
    print(f"model load: {time.perf_counter() - t0:.1f} s wall, {cpu_seconds() - c0:.1f} s CPU, "
          f"peak RSS {peak_rss_mb():.0f} MB")

    cfg = so.VadModelConfig()
    cfg.silero_vad.model = str(models.model_dir(models.VAD) / "silero_vad.onnx")
    cfg.silero_vad.threshold = 0.5
    cfg.silero_vad.min_silence_duration = 0.5
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = 20
    cfg.silero_vad.window_size = WINDOW
    cfg.sample_rate = RATE
    cfg.num_threads = 1
    vad = so.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)

    wavs = models.model_dir(models.TEST_WAVS)
    silence = np.zeros(int(1.5 * RATE), np.float32)
    pieces = []
    for name in ("en", "fr", "de", "es"):
        pieces += [read_wav(wavs / f"{name}.wav"), silence]
    audio = np.tile(np.concatenate(pieces), args.repeat)
    pad = (-len(audio)) % WINDOW
    audio = np.concatenate([audio, np.zeros(pad, np.float32)])
    duration = len(audio) / RATE

    decodes: list[tuple[float, float]] = []  # (utterance seconds, decode seconds)
    texts: list[str] = []

    def drain() -> None:
        while not vad.empty():
            seg = vad.front() if callable(vad.front) else vad.front
            samples = np.asarray(seg.samples, dtype=np.float32)
            vad.pop()
            stream = rec.create_stream()
            stream.accept_waveform(RATE, samples)
            td = time.perf_counter()
            rec.decode_stream(stream)
            decodes.append((len(samples) / RATE, time.perf_counter() - td))
            texts.append(stream.result.text.strip())

    t1, c1 = time.perf_counter(), cpu_seconds()
    for i in range(0, len(audio), WINDOW):
        vad.accept_waveform(audio[i:i + WINDOW])
        drain()
    vad.flush()
    drain()
    wall, cpu = time.perf_counter() - t1, cpu_seconds() - c1

    lens = np.array([d[0] for d in decodes])
    times = np.array([d[1] for d in decodes])
    print(f"audio: {duration:.0f} s, {len(decodes)} utterances (expected {4 * args.repeat})")
    print(f"processing: {wall:.1f} s wall, {cpu:.1f} s CPU -> {60 * cpu / duration:.1f} CPU-s per audio minute, "
          f"{duration / wall:.0f}x real time (wall)")
    print(f"decode per utterance: mean {times.mean():.2f} s, max {times.max():.2f} s "
          f"(utterances {lens.mean():.1f} s on average, longest {lens.max():.1f} s)")
    print(f"peak RSS: {peak_rss_mb():.0f} MB")
    print("first pass transcripts:")
    for lang, text in zip(("en", "fr", "de", "es"), texts[:4]):
        print(f"  {lang}: {text}")


if __name__ == "__main__":
    main()
