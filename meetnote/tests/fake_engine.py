"""A model-free stand-in for the speech engine.

EnergyVad has the interface of sherpa-onnx's VoiceActivityDetector; FakeEngine's
"transcription" names the utterance length, so tests can check boundaries.
"""

from dataclasses import dataclass

import numpy as np

RATE = 16000


@dataclass
class Segment:
    start: int
    samples: np.ndarray


class EnergyVad:
    def __init__(self, threshold=0.02, min_silence=16, min_speech=8):  # in 512-sample windows
        self.threshold, self.min_silence, self.min_speech = threshold, min_silence, min_speech
        self.reset()

    def reset(self):
        self.n = 0
        self.queue = []
        self.current = None
        self.start = 0
        self.silence = 0

    def accept_waveform(self, x):
        loud = float(np.sqrt(np.mean(np.square(x)))) > self.threshold
        if self.current is None:
            if loud:
                self.current, self.start, self.silence = [x], self.n, 0
        else:
            self.current.append(x)
            self.silence = 0 if loud else self.silence + 1
            if self.silence >= self.min_silence:
                self._emit(drop=self.silence)
        self.n += len(x)

    def _emit(self, drop=0):
        parts = self.current[: len(self.current) - drop] if drop else self.current
        if len(parts) >= self.min_speech:
            self.queue.append(Segment(self.start, np.concatenate(parts)))
        self.current, self.silence = None, 0

    def is_speech_detected(self):
        return self.current is not None

    def empty(self):
        return not self.queue

    @property
    def front(self):
        return self.queue[0]

    def pop(self):
        self.queue.pop(0)

    def flush(self):
        if self.current is not None:
            self._emit(drop=self.silence)


class FakeEngine:
    name = "fake"

    def __init__(self, fail_after=None):
        self.calls = 0
        self.fail_after = fail_after

    def new_vad(self):
        return EnergyVad()

    def transcribe(self, samples):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("simulated crash")
        return f"utterance of {len(samples) / RATE:.1f} s"


def tone_bursts(spec):
    """PCM bytes: spec is a list of (seconds, loud?) pieces."""
    out = []
    for seconds, loud in spec:
        n = int(seconds * RATE)
        t = np.arange(n) / RATE
        x = 0.3 * np.sin(2 * np.pi * 300 * t) if loud else np.zeros(n)
        out.append((x * 32767).astype("<i2"))
    return np.concatenate(out).tobytes()
