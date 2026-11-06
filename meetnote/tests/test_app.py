import pytest

from meetnote import session
from meetnote.app import Api, App, page


class FakePusher:
    def __init__(self):
        self.events = []
        self.windows = []

    def emit(self, kind, data):
        self.events.append((kind, data))


@pytest.fixture
def app():
    a = App.__new__(App)  # no window, no threads
    a.pusher = FakePusher()
    from meetnote.core import Controller
    a.ctl = Controller(emit=a._on_event, poll_devices=False)
    a.api = Api(a)
    a.window = None
    a._recent = {}
    a._allow_close = False
    a._downloading = False
    return a


def test_page_inlines_everything():
    html = page()
    assert "/*STYLE*/" not in html and "/*SCRIPT*/" not in html and "/*VIEW*/" not in html
    assert "window.meetnote" in html and "--accent" in html


def test_bridge_rejects_bad_session_ids(app):
    for bad in ("../../etc", "/tmp/x", "20261106-140005/.."):
        with pytest.raises(ValueError):
            app.api.open(bad)
        with pytest.raises(ValueError):
            app.api.save_notes(bad, "x")


def test_notes_and_title_are_saved(app):
    s = session.Session.create("Old title")
    app.api.save_notes(s.id, "- [ ] send the slides")
    assert app.api.rename(s.id, "  New title  ") == "New title"
    assert app.api.rename(s.id, "   ") == "New title"  # empty titles are ignored
    opened = app.api.open(s.id)
    assert opened["notes"] == "- [ ] send the slides"
    assert opened["title"] == "New title"
    assert opened["lines"] == []


def test_live_lines_get_echo_flags(app):
    sid = "20261106-140005-x"
    base = {"id": sid, "type": "line", "seg": "audio/x.pcm"}
    app._on_event("line", {**base, "src": "me", "s0": 1, "w0": 10.2, "w1": 14.0, "text": "Move the release to Friday"})
    app._on_event("line", {**base, "src": "others", "s0": 2, "w0": 10.0, "w1": 14.0, "text": "Move the release to Friday."})
    kinds = [k for k, _ in app.pusher.events]
    assert kinds == ["line", "line", "echo"]  # the earlier "me" line turns out to be echo
    assert app.pusher.events[2][1] == {"id": sid, "seg": "audio/x.pcm", "s0": 1, "echo": True}


def test_links_only_open_web_urls(app, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    app.api.open_link("file:///etc/passwd")
    app.api.open_link("javascript:alert(1)")
    app.api.open_link("https://example.org")
    assert opened == ["https://example.org"]
