import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import numpy as np
import pytest

from meetnote.durable import read_jsonl
from meetnote.recorder import (BYTES_PER_SECOND, Recorder, Stream, anchors_path, gaps,
                               sample_to_wall, writer_of)
from meetnote.session import Session


def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_records_both_sources_and_stops(fake_parec):
    s = Session.create("unit")
    rec = Recorder(s, poll_devices=False)
    rec.start()
    assert Session(s.folder).status == "recording"
    time.sleep(2.3)
    rec.stop()

    meta = Session(s.folder).meta
    assert meta["status"] == "stopped"
    assert {g["src"] for g in meta["segments"]} == {"me", "others"}
    for g in meta["segments"]:
        assert g["closed"] and g["pid"]
        path = s.folder / g["file"]
        size = path.stat().st_size
        assert 1.8 * BYTES_PER_SECOND <= size <= 2.8 * BYTES_PER_SECOND
        anchors = list(read_jsonl(anchors_path(path)))
        assert len(anchors) >= 2
        assert anchors[-1]["bytes"] == size  # the final anchor covers all audio
        assert writer_of(path) is None  # parec is gone


def test_recording_survives_an_app_crash_and_is_adopted(fake_parec):
    app_code = textwrap.dedent("""
        import time
        from meetnote.recorder import Recorder
        from meetnote.session import Session
        s = Session.create("crash")
        Recorder(s, poll_devices=False).start()
        print(s.folder, flush=True)
        time.sleep(60)
    """)
    app = subprocess.Popen([sys.executable, "-c", app_code], stdout=subprocess.PIPE, text=True)
    folder = Path(app.stdout.readline().strip())
    time.sleep(1.5)
    app.kill()  # SIGKILL: no cleanup runs
    app.wait()

    s = Session(folder)
    files = [folder / g["file"] for g in s.meta["segments"]]
    assert len(files) == 2
    sizes = [f.stat().st_size for f in files]
    time.sleep(1.0)
    assert all(f.stat().st_size > n for f, n in zip(files, sizes)), "parec stopped with the app"

    rec = Recorder.adopt(s, poll_devices=False)
    assert set(rec.streams) == {"me", "others"}
    rec.start()  # continues the adopted segments instead of starting new ones
    time.sleep(1.2)
    rec.stop()

    meta = Session(folder).meta
    assert len(meta["segments"]) == 2 and all(g["closed"] for g in meta["segments"])
    for f in files:
        assert writer_of(f) is None
        anchors = list(read_jsonl(anchors_path(f)))
        assert anchors[-1]["bytes"] == f.stat().st_size


def test_adopt_after_reboot_closes_dead_segments(fake_parec):
    s = Session.create("reboot")
    s.add_segment("audio/me-x.pcm", "me")
    (s.folder / "audio/me-x.pcm").write_bytes(b"\0" * 3200)
    rec = Recorder.adopt(s, poll_devices=False)
    assert rec.streams == {}
    assert s.open_segments() == []


def test_left_over_parec_stops_after_max_hours(fake_parec):
    s = Session.create("cap")
    rec = Recorder(s, max_hours=1.0 / 3600, poll_devices=False)  # 1 s
    rec.start()
    procs = [st.proc for st in rec.streams.values()]
    rec._stop.set()  # the app vanishes without stopping parec
    assert wait_for(lambda: all(p.poll() is not None for p in procs), timeout=4)


def test_parec_exit_continues_in_a_new_segment(fake_parec, monkeypatch):
    monkeypatch.setenv("FAKE_PAREC_EXIT_AFTER", "0.8")
    s = Session.create("restart")
    rec = Recorder(s, poll_devices=False, restart_delay=0.5)
    rec.start()
    time.sleep(3.5)
    rec.stop()
    me = [g for g in Session(s.folder).meta["segments"] if g["src"] == "me"]
    assert len(me) >= 2
    assert all(g["closed"] for g in me)


def test_mic_toggle_stops_and_restarts_only_the_mic(fake_parec):
    s = Session.create("mute")
    rec = Recorder(s, poll_devices=False)
    rec.start()
    time.sleep(0.5)
    rec.set_mic(False)
    assert set(rec.streams) == {"others"}
    rec.set_mic(True)
    assert set(rec.streams) == {"me", "others"}
    rec.stop()
    srcs = [g["src"] for g in Session(s.folder).meta["segments"]]
    assert srcs.count("me") == 2 and srcs.count("others") == 1


def test_level_of_a_tone(tmp_path):
    s = Session.create("level")
    path = s.folder / "audio/me-t.pcm"
    t = np.arange(16000) / 16000
    path.write_bytes((0.5 * 32767 * np.sin(2 * np.pi * 440 * t)).astype("<i2").tobytes())
    stream = Stream(s, "me", "audio/me-t.pcm", pgid=-1)
    expected = (20 * np.log10(0.5 / np.sqrt(2)) + 60) / 60  # -9 dBFS
    assert stream.level() == pytest.approx(expected, abs=0.01)
    os.close(stream.fd)


def test_time_mapping_places_audio_after_a_stall():
    anchors = [(16000, 101.0), (32000, 102.0), (48000, 104.5)]  # 1.5 s without audio before 104.5
    assert sample_to_wall(anchors, 8000) == 100.5
    assert sample_to_wall(anchors, 32000) == 102.0
    assert sample_to_wall(anchors, 40000) == 104.0
    assert sample_to_wall(anchors, 56000) == 105.0
    assert gaps(anchors) == [(102.0, 103.5)]
