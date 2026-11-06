import sys
import time
from pathlib import Path

import pytest

from meetnote import session
from meetnote.config import Config
from meetnote.core import Controller, acquire_app_lock
from meetnote.recorder import writer_of

HERE = Path(__file__).parent


def controller(events=None):
    def emit(kind, data):
        if events is not None:
            events.append((kind, data))

    return Controller(cfg=Config(), emit=emit, poll_devices=False,
                      transcriber_cmd=[sys.executable, str(HERE / "fake_transcriber.py")])


def wait_for(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_start_mute_stop_and_transcriber_lifecycle(fake_parec):
    events = []
    ctl = controller(events)
    s = ctl.start("Weekly")
    assert ("recording", {"id": s.id, "title": "Weekly"}) in events
    with pytest.raises(RuntimeError):
        ctl.start("second")  # one recording at a time
    time.sleep(0.5)
    ctl.set_mic(False)
    assert set(ctl.recorder.streams) == {"others"}
    ctl.poll()
    assert ctl.transcribing(s.id)
    ctl.stop()
    assert session.load(s.id).status == "stopped"
    assert wait_for(lambda: (ctl.poll(), not ctl.transcribing(s.id))[1])  # finishes and exits
    assert any(kind == "transcribed" for kind, _ in events)
    assert any(kind == "levels" for kind, _ in events)


def test_recover_marks_dead_recording_interrupted(fake_parec):
    s = session.Session.create("crashed")
    s.add_segment("audio/me-x.pcm", "me")
    (s.folder / "audio/me-x.pcm").write_bytes(b"\0" * 32000)
    s.update(status="recording")  # the app died and parec is gone too (reboot)
    events = []
    ctl = controller(events)
    ctl.recover()
    assert session.load(s.id).status == "interrupted"
    assert ("interrupted", {"id": s.id}) in events
    assert ctl.recorder is None
    ctl.resume(s.id)  # record more into the same meeting
    assert session.load(s.id).status == "recording"
    ctl.stop()
    files = [g["file"] for g in session.load(s.id).meta["segments"]]
    assert len(files) == 3 and all(writer_of(s.folder / f) is None for f in files)


def test_only_one_app_instance():
    first = acquire_app_lock()
    assert first is not None
    assert acquire_app_lock() is None
