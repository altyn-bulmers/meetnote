from datetime import datetime

import pytest

from meetnote import session
from meetnote.session import Session


def test_create_makes_folder_and_metadata():
    s = Session.create("Team sync: Q4!", when=datetime(2026, 11, 6, 14, 0, 5))
    assert s.id == "20261106-140005-team-sync-q4"
    assert s.title == "Team sync: Q4!"
    assert s.status == "stopped"
    assert (s.folder / "audio").is_dir()
    assert session.load(s.id).meta == s.meta


def test_untitled_session_gets_a_readable_title():
    s = Session.create("", when=datetime(2026, 11, 6, 9, 30, 0))
    assert s.id == "20261106-093000"
    assert s.title == "Meeting 2026-11-06 09:30"


def test_list_is_newest_first():
    a = Session.create("a", when=datetime(2026, 11, 5, 10, 0, 0))
    b = Session.create("b", when=datetime(2026, 11, 6, 10, 0, 0))
    assert [s.id for s in session.list_sessions()] == [b.id, a.id]


@pytest.mark.parametrize("bad", ["../etc", "20261106-140005/../../x", "", "x" * 80, "/tmp"])
def test_load_rejects_ids_that_could_escape(bad):
    with pytest.raises(ValueError):
        session.load(bad)


def test_segments_and_notes_persist():
    s = Session.create("x")
    s.add_segment("audio/me-1.pcm", "me")
    s.set_segment_pid("audio/me-1.pcm", 1234)
    s.write_notes("## Agenda\n- item\n")
    again = session.load(s.id)
    assert again.open_segments()[0]["pid"] == 1234
    again.close_segment("audio/me-1.pcm")
    assert session.load(s.id).open_segments() == []
    assert session.load(s.id).read_notes() == "## Agenda\n- item\n"
