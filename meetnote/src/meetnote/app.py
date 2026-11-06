"""The meetnote window: pywebview (GTK and WebKitGTK) showing web/index.html.

The page calls Python through pywebview's JS bridge (`pywebview.api.<method>`).
Python sends events to the page from one dedicated thread, so recording never
waits on the window. The page is loaded as one inline HTML string, so no local
web server runs and other programs or web pages cannot reach the app.
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from . import devices, models, session
from .core import Controller, acquire_app_lock
from .durable import JsonlTail, read_json
from .recorder import RATE
from .session import Session
from .transcript import mark_echo

log = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"


def page(view: str = "main") -> str:
    """index.html with its CSS and JS inlined."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    return (html.replace("/*STYLE*/", (WEB / "style.css").read_text(encoding="utf-8"))
                .replace("/*VIEW*/", json.dumps(view))
                .replace("/*SCRIPT*/", (WEB / "app.js").read_text(encoding="utf-8")))


class Pusher:
    """Sends events to the open windows from one thread; level updates are coalesced."""

    def __init__(self) -> None:
        self.windows: list[Any] = []
        self._queue: queue.Queue = queue.Queue()
        self._levels: dict | None = None
        self._lock = threading.Lock()
        threading.Thread(target=self._run, name="ui-events", daemon=True).start()

    def emit(self, kind: str, data: Any) -> None:
        if kind == "levels":
            with self._lock:
                self._levels = data  # only the newest levels matter
        else:
            self._queue.put((kind, data))

    def _run(self) -> None:
        while True:
            batch = []
            try:
                batch.append(self._queue.get(timeout=0.15))
                while True:
                    batch.append(self._queue.get_nowait())
            except queue.Empty:
                pass
            with self._lock:
                levels, self._levels = self._levels, None
            if levels is not None:
                batch.append(("levels", levels))
            if not batch:
                continue
            script = f"window.meetnote && window.meetnote.onEvents({json.dumps(batch)})"
            for window in list(self.windows):
                try:
                    window.run_js(script)
                except Exception:  # the window is not loaded yet or already closed
                    log.debug("could not deliver events", exc_info=True)


def summary(s: Session) -> dict:
    return {"id": s.id, "title": s.title, "created": s.meta["created"], "status": s.status}


def lag_seconds(s: Session) -> float | None:
    """Seconds of recorded audio the transcriber has not reached yet."""
    path = s.folder / "transcriber.status.json"
    if not path.exists():
        return None
    try:
        status = read_json(path)
    except (OSError, ValueError):
        return None
    lag = 0.0
    for seg in s.meta["segments"]:
        if seg["file"] in status.get("done", []):
            continue
        pcm = s.folder / seg["file"]
        size = pcm.stat().st_size if pcm.exists() else 0
        lag = max(lag, (size / 2 - status.get("position", {}).get(seg["file"], 0)) / RATE)
    return round(lag, 1)


class App:
    def __init__(self) -> None:
        self.pusher = Pusher()
        self.ctl = Controller(emit=self._on_event)
        self.api = Api(self)
        self.window: Any = None
        self._recent: dict[str, list[dict]] = {}  # newest lines per session, for echo marking
        self._allow_close = False
        self._downloading = False

    # --- events from the controller ------------------------------------------------

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "line":
            self._on_line(data)
        else:
            self.pusher.emit(kind, data)

    def _on_line(self, rec: dict) -> None:
        recent = self._recent.setdefault(rec["id"], [])
        before = {(r["seg"], r["s0"]): r.get("echo", False) for r in recent}
        recent.append(dict(rec))
        recent.sort(key=lambda r: r["w0"])
        del recent[:-30]
        mark_echo(recent)
        for r in recent:
            key = (r["seg"], r["s0"])
            if key == (rec["seg"], rec["s0"]):
                self.pusher.emit("line", r)
            elif key in before and before[key] != r["echo"]:
                self.pusher.emit("echo", {"id": rec["id"], "seg": r["seg"], "s0": r["s0"], "echo": r["echo"]})

    # --- session access --------------------------------------------------------------

    def session(self, sid: str) -> Session:
        """The one Session object that writes `sid` in this process."""
        if self.ctl.session is not None and self.ctl.session.id == sid:
            return self.ctl.session
        return session.load(sid)

    def state(self) -> dict:
        rec, s = self.ctl.recorder, self.ctl.session
        since = None
        if rec is not None and s is not None:
            starts = [g["started"] for g in s.meta["segments"] if not g["closed"]]
            since = min(starts) if starts else time.time()
        return {
            "recording": rec is not None,
            "session": s.id if (rec is not None and s is not None) else None,
            "since": since,
            "mic": rec.mic_on if rec is not None else True,
            "levels": rec.levels if rec is not None else {"me": 0.0, "others": 0.0},
            "devices": (rec.device_names if rec is not None and any(rec.device_names.values())
                        else devices.default_names()),
            "models_missing": [n for n in models.DEFAULT if not models.is_installed(n)],
            "downloading": self._downloading,
        }

    def open(self, sid: str) -> dict:
        s = self.session(sid)
        tail = JsonlTail(s.transcript_path)
        lines = mark_echo(sorted((r for r in tail.poll() if r.get("type") == "line"),
                                 key=lambda r: r["w0"]))
        self._recent[sid] = [dict(r) for r in lines[-30:]]
        self.ctl.watch(sid, tail)  # new lines arrive as events from here on
        return {**summary(s), "recording": self.ctl.recorder is not None and self.ctl.session is s,
                "transcribing": self.ctl.transcribing(sid), "lines": lines, "notes": s.read_notes()}

    # --- background work -------------------------------------------------------------

    def download_models(self) -> None:
        if self._downloading:
            return
        self._downloading = True

        def work() -> None:
            try:
                for name in models.DEFAULT:
                    models.download(name, lambda f, have, total: self.pusher.emit(
                        "download", {"file": f, "have": have, "total": total}))
                self.pusher.emit("download_done", {})
            except Exception as exc:  # network errors are shown in the page; retry is possible
                log.exception("model download failed")
                self.pusher.emit("download_failed", {"error": str(exc)})
            finally:
                self._downloading = False

        threading.Thread(target=work, name="download", daemon=True).start()

    def _poll_loop(self) -> None:
        next_lag = 0.0
        while True:
            try:
                self.ctl.poll()
                now = time.monotonic()
                if now >= next_lag:
                    next_lag = now + 2.0
                    s = self.ctl.session
                    if self.ctl.recorder is not None and s is not None:
                        self.pusher.emit("lag", {"id": s.id, "seconds": lag_seconds(s)})
            except Exception:
                log.exception("poll failed")
            time.sleep(0.25)

    # --- window ----------------------------------------------------------------------

    def _on_closing(self) -> bool:
        if self._allow_close or self.ctl.recorder is None:
            return True
        self.pusher.emit("confirm_quit", {})  # the page asks: stop and quit, or keep recording
        return False

    def quit(self, stop: bool) -> None:
        if stop:
            self.ctl.stop()
        self._allow_close = True
        self.window.destroy()

    def run(self) -> int:
        import webview

        if acquire_app_lock() is None:
            print("meetnote is already running", file=sys.stderr)
            return 1
        self.ctl.recover()
        self.window = webview.create_window(
            "meetnote", html=page(), js_api=self.api, width=1280, height=800,
            min_size=(900, 560), background_color="#f7f7f5", text_select=True)
        self.window.events.closing += self._on_closing
        self.pusher.windows.append(self.window)
        # A daemon thread: pywebview's own start-up thread is not a daemon and
        # would keep the process alive after the window closes.
        threading.Thread(target=self._poll_loop, name="poll", daemon=True).start()
        webview.start(func=self._on_started, gui="gtk", private_mode=True,
                      icon=str(WEB / "icon.svg"))
        self.ctl.shutdown()
        return 0

    def _on_started(self) -> None:
        """Runs once the GUI loop has started; it must return quickly."""


class Api:
    """What the page may call. Session ids are validated by session.load."""

    def __init__(self, app: App) -> None:
        self._app = app

    def state(self) -> dict:
        return self._app.state()

    def sessions(self) -> list[dict]:
        return [summary(s) for s in session.list_sessions()]

    def open(self, sid: str) -> dict:
        return self._app.open(str(sid))

    def start(self, title: str = "") -> dict:
        return {"id": self._app.ctl.start(str(title).strip()[:200]).id}

    def stop(self) -> dict:
        s = self._app.ctl.stop()
        return {"id": s.id if s else None}

    def resume(self, sid: str) -> dict:
        return {"id": self._app.ctl.resume(str(sid)).id}

    def set_mic(self, on: bool) -> None:
        self._app.ctl.set_mic(bool(on))

    def save_notes(self, sid: str, text: str) -> bool:
        self._app.session(str(sid)).write_notes(str(text))
        return True

    def rename(self, sid: str, title: str) -> str:
        s = self._app.session(str(sid))
        title = str(title).strip()[:200]
        if title:
            s.update(title=title)
        return s.title

    def download_models(self) -> None:
        self._app.download_models()

    def open_link(self, url: str) -> None:
        url = str(url)
        if url.startswith(("https://", "http://")):
            webbrowser.open(url)

    def quit(self, stop: bool) -> None:
        self._app.quit(bool(stop))

    def minimize(self) -> None:
        self._app.window.minimize()


def main() -> int:
    return App().run()
