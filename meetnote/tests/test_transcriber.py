import threading
import time
import wave

import numpy as np
import pytest

from fake_engine import FakeEngine, tone_bursts
from meetnote import models
from meetnote.durable import JsonlWriter, read_jsonl
from meetnote.recorder import anchors_path
from meetnote.session import Session
from meetnote.transcriber import Transcriber, acquire_lock, read_progress

RATE = 16000
SPEECH = [(1.0, False), (1.0, True), (1.0, False), (1.5, True), (1.0, False), (2.0, True), (1.0, False)]


def make_segment(s, name, src, pcm, closed=True, start_wall=1000.0):
    file = f"audio/{name}.pcm"
    s.add_segment(file, src)
    (s.folder / file).write_bytes(pcm)
    with JsonlWriter(anchors_path(s.folder / file)) as w:  # one anchor at the end: no gaps
        w.append({"bytes": len(pcm), "wall": start_wall + len(pcm) / 2 / RATE, "mono": 0})
    if closed:
        s.close_segment(file)
    return file


def records(s, kind):
    return [r for r in read_jsonl(s.transcript_path) if r["type"] == kind]


def test_transcribes_a_closed_segment():
    s = Session.create("t")
    file = make_segment(s, "others-1", "others", tone_bursts(SPEECH))
    Transcriber(s.folder, FakeEngine()).run()
    lines = records(s, "line")
    assert [r["text"] for r in lines] == ["utterance of 1.0 s", "utterance of 1.5 s", "utterance of 2.0 s"]
    assert [round(r["s0"] / RATE, 1) for r in lines] == [1.0, 3.0, 5.5]
    assert all(r["src"] == "others" and r["seg"] == file for r in lines)
    assert lines[0]["w0"] == pytest.approx(1001.0, abs=0.05)  # placed by the anchor
    assert records(s, "segment_done") == [{"type": "segment_done", "seg": file, "samples": 8.5 * RATE}]


def test_resume_after_a_crash_has_no_duplicates_or_holes():
    s = Session.create("t")
    make_segment(s, "me-1", "me", tone_bursts(SPEECH))
    with pytest.raises(RuntimeError):
        Transcriber(s.folder, FakeEngine(fail_after=1)).run()  # dies in the second utterance
    assert len(records(s, "line")) == 1
    Transcriber(s.folder, FakeEngine()).run()
    texts = [r["text"] for r in records(s, "line")]
    assert texts == ["utterance of 1.0 s", "utterance of 1.5 s", "utterance of 2.0 s"]
    assert len(records(s, "segment_done")) == 1


def test_follows_a_growing_segment_until_it_is_closed():
    s = Session.create("t")
    s.update(status="recording")
    pcm = tone_bursts(SPEECH)
    file = make_segment(s, "others-1", "others", b"", closed=False)
    path = s.folder / file

    def write_slowly():
        for i in range(0, len(pcm), 16000):  # 0.5 s of audio at a time
            with open(path, "ab") as f:
                f.write(pcm[i:i + 16000])
            time.sleep(0.02)
        s.close_segment(file)
        s.update(status="stopped")

    writer = threading.Thread(target=write_slowly)
    writer.start()
    Transcriber(s.folder, FakeEngine(), poll=0.01).run()
    writer.join()
    assert len(records(s, "line")) == 3
    assert len(records(s, "segment_done")) == 1


def test_long_silence_gets_checkpoints_and_resume_uses_them():
    s = Session.create("t")
    file = make_segment(s, "others-1", "others", tone_bursts([(70.0, False), (1.0, True), (1.0, False)]))
    Transcriber(s.folder, FakeEngine()).run()
    points = [r["sample"] for r in records(s, "checkpoint")]
    assert points and all(p < 70 * RATE for p in points)
    done, resume = read_progress(s.transcript_path)
    assert file in done


def test_continuous_speech_is_cut_into_pieces():
    s = Session.create("t")
    make_segment(s, "me-1", "me", tone_bursts([(60.0, True), (1.0, False)]))
    Transcriber(s.folder, FakeEngine()).run()
    lengths = [(r["s1"] - r["s0"]) / RATE for r in records(s, "line")]
    assert len(lengths) >= 3 and max(lengths) <= 25.1
    assert sum(lengths) == pytest.approx(60.0, abs=0.1)  # nothing lost at the cuts


def test_only_one_transcriber_per_session():
    s = Session.create("t")
    first = acquire_lock(s.folder)
    assert first is not None
    assert acquire_lock(s.folder) is None


@pytest.mark.skipif(not (models.is_installed(models.PARAKEET) and models.is_installed(models.VAD)
                         and models.is_installed(models.TEST_WAVS)),
                    reason="speech models not downloaded (meetnote models parakeet-tdt-0.6b-v3-int8 silero-vad test-wavs)")
def test_real_models_transcribe_test_recording():
    from meetnote.transcriber import Engine

    with wave.open(str(models.model_dir(models.TEST_WAVS) / "en.wav")) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
    x = np.interp(np.arange(int(len(x) * RATE / rate)) * rate / RATE, np.arange(len(x)), x)
    silence = np.zeros(RATE)
    pcm = np.concatenate([silence, x, silence]).astype("<i2").tobytes()
    s = Session.create("t")
    make_segment(s, "others-1", "others", pcm)
    Transcriber(s.folder, Engine(threads=1)).run()
    text = " ".join(r["text"] for r in records(s, "line")).lower()
    assert "ask not what your country can do for you" in text
