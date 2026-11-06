"""Questions during a meeting and the summary at the end, answered by Claude.

Claude runs through the Claude Code CLI (`claude -p`), so it uses the
subscription the CLI is logged in with. Each call runs in an empty folder with
tools disabled and safe mode on (no CLAUDE.md, plugins, hooks or MCP servers),
and without API-key variables, which would bill the API instead. Claude can
therefore only answer from the text meetnote gives it.

qa.jsonl holds one JSON object per line, each fsynced:
  {"type": "question", "id": "q…", "t": ISO time, "question": "…", "whole": false, "model": "sonnet"}
  {"type": "answer", "id": "q…", "t": …, "answer": "…", "seconds": 2.4}
  {"type": "failed", "id": "q…", "t": …, "reason": "…", "offline": true}
The last record of an id is its state; a retry appends to the same id.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any, Callable

from . import paths, transcript
from .config import Config
from .durable import JsonlWriter, atomic_write, read_jsonl
from .session import Session, now_iso

log = logging.getLogger(__name__)

FLAGS = ["-p", "--safe-mode", "--tools", "", "--no-session-persistence",
         "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
NOTES_LIMIT = 8000  # characters of notes sent with a question

ASK_SYSTEM = """You help someone during a live meeting. They ask short questions and read your answer while the meeting goes on.
Answer in a few sentences or a short list unless they ask for more.
Use the transcript and their notes when relevant. If the answer is not there, answer from general knowledge and say so.
"Me" in the transcript is the person asking; "Others" are the other participants. The transcript comes from speech recognition and can contain errors.
Answer in the language of the question."""

SUMMARY_SYSTEM = """You write meeting notes from a transcript and the user's own notes.
Write Markdown with exactly these sections:
## Summary
Three to five sentences.
## Decisions
## Action items
As "- [ ] Owner: task (due date if one was said)".
## Open questions
Use only what the transcript and notes contain, and write "None." for an empty section.
"Me" is the user; "Others" are the other participants. The transcript comes from speech recognition and can contain errors.
Write in the language of the meeting."""

Emit = Callable[[str, dict[str, Any]], None]


class ClaudeError(Exception):
    def __init__(self, message: str, offline: bool = False):
        super().__init__(message)
        self.offline = offline


def claude_binary() -> str | None:
    return os.environ.get("MEETNOTE_CLAUDE") or shutil.which("claude")


def claude_env() -> dict[str, str]:
    """The child's environment: an API key would make `claude -p` bill the API instead of the plan."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def run_claude(prompt: str, system: str, model: str, on_text: Callable[[str], None] | None = None,
               effort: str | None = None, timeout: float = 300.0) -> str:
    """One `claude -p` call. Streams text to `on_text` and returns the whole answer.

    Raises ClaudeError; `offline` is set when the connection itself failed, which
    the CLI reports as an api_retry event without an HTTP status.
    """
    binary = claude_binary()
    if not binary:
        raise ClaudeError("Claude Code is not installed: the `claude` command was not found")
    cmd = [binary, *FLAGS, "--model", model, "--system-prompt", system]
    if effort:
        cmd += ["--effort", effort]
    workdir = paths.state_dir() / "claude"  # empty, so no project files are read
    workdir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(cmd, cwd=workdir, env=claude_env(), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True).start()
    timed_out = threading.Event()

    def on_timeout() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, on_timeout)
    timer.start()
    parts: list[str] = []
    result: dict | None = None
    offline = False
    try:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "stream_event":
                delta = (event.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta":
                    parts.append(delta.get("text", ""))
                    if on_text:
                        on_text(delta.get("text", ""))
            elif kind == "system" and event.get("subtype") == "api_retry" and event.get("error_status") is None:
                offline = True
                proc.kill()
                break
            elif kind == "result":
                result = event
    finally:
        timer.cancel()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if offline:
        raise ClaudeError("Claude is not reachable: check the internet connection", offline=True)
    if timed_out.is_set():
        raise ClaudeError(f"Claude did not answer within {timeout:.0f} s")
    if result is None:
        raise ClaudeError("".join(stderr).strip()[-300:] or f"claude stopped with exit code {proc.returncode}")
    if result.get("is_error"):
        raise ClaudeError(str(result.get("result") or result.get("subtype") or "Claude reported an error"))
    return (result.get("result") or "".join(parts)).strip()


def ask_prompt(s: Session, question: str, minutes: float | None) -> str:
    """The question with the transcript of the last `minutes` (None: the whole meeting) and the notes."""
    items = transcript.lines(s.transcript_path)
    since = max(r["w1"] for r in items) - 60 * minutes if (items and minutes is not None) else None
    notes = s.read_notes().strip()[-NOTES_LIMIT:]
    scope = "the whole meeting" if minutes is None else f"the last {minutes:g} minutes"
    return (f"Meeting: {s.title}\n\n"
            f"Transcript of {scope}:\n{transcript.as_text(items, since) or '(no transcript yet)'}\n\n"
            f"My notes:\n{notes or '(none)'}\n\n"
            f"My question: {question}\n")


def summary_prompt(s: Session) -> str:
    items = transcript.lines(s.transcript_path)
    when = s.meta["created"][:16].replace("T", " ")
    return (f"Meeting: {s.title} ({when})\n\n"
            f"Transcript:\n{transcript.as_text(items) or '(no transcript)'}\n\n"
            f"My notes:\n{s.read_notes().strip() or '(none)'}\n")


def history(s: Session) -> list[dict]:
    """Questions of a session with their latest state: pending, answered or failed."""
    items: dict[str, dict] = {}
    for rec in read_jsonl(s.qa_path):
        q = items.setdefault(rec["id"], {"id": rec["id"]})
        if rec["type"] == "question":
            q.update(question=rec["question"], t=rec["t"], whole=rec.get("whole", False), status="pending")
        elif rec["type"] == "answer":
            q.update(answer=rec["answer"], status="answered", seconds=rec.get("seconds"))
        elif rec["type"] == "failed":
            q.update(status="failed", reason=rec.get("reason", ""), offline=rec.get("offline", False))
    return [q for q in items.values() if "question" in q]


class Assistant:
    """Runs questions and summaries in background threads and reports them through `emit`."""

    def __init__(self, cfg: Config, emit: Emit):
        self.cfg = cfg
        self.emit = emit
        self._guard = threading.Lock()
        self._qa_locks: dict[str, threading.Lock] = {}
        self._summaries: set[str] = set()

    def _append(self, s: Session, record: dict) -> None:
        with self._guard:
            lock = self._qa_locks.setdefault(s.id, threading.Lock())
        with lock, JsonlWriter(s.qa_path) as w:
            w.append(record)

    # --- questions ----------------------------------------------------------------

    def ask(self, s: Session, question: str, whole: bool = False) -> str:
        qid = f"q{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self._append(s, {"type": "question", "id": qid, "t": now_iso(), "question": question,
                         "whole": whole, "model": self.cfg.ask_model})
        self._answer(s, qid, question, whole)
        return qid

    def retry(self, s: Session, qid: str) -> None:
        q = next((q for q in history(s) if q["id"] == qid), None)
        if q is None:
            raise KeyError(f"no question {qid}")
        self._answer(s, qid, q["question"], q.get("whole", False))

    def _answer(self, s: Session, qid: str, question: str, whole: bool) -> None:
        self.emit("answer_start", {"id": s.id, "qid": qid, "question": question})

        def work() -> None:
            started = time.monotonic()
            try:
                answer = run_claude(
                    ask_prompt(s, question, None if whole else self.cfg.context_minutes),
                    ASK_SYSTEM, self.cfg.ask_model, effort="low", timeout=120,
                    on_text=lambda text: self.emit("answer_delta", {"id": s.id, "qid": qid, "text": text}))
            except ClaudeError as exc:
                self._append(s, {"type": "failed", "id": qid, "t": now_iso(), "reason": str(exc),
                                 "offline": exc.offline})
                self.emit("answer_failed", {"id": s.id, "qid": qid, "reason": str(exc), "offline": exc.offline})
                return
            seconds = round(time.monotonic() - started, 1)
            self._append(s, {"type": "answer", "id": qid, "t": now_iso(), "answer": answer, "seconds": seconds})
            self.emit("answer_done", {"id": s.id, "qid": qid, "answer": answer, "seconds": seconds})

        threading.Thread(target=work, name=f"ask-{qid}", daemon=True).start()

    # --- summary ------------------------------------------------------------------

    def summarize(self, s: Session, ready: Callable[[], bool], wait: float = 900.0) -> bool:
        """Write summary.md once `ready()` says the transcript is complete.

        The session is marked "pending" first, so a summary that cannot be written
        now (offline, app closed) is retried at the next start. Returns False if a
        summary of this session is already being written.
        """
        with self._guard:
            if s.id in self._summaries:
                return False
            self._summaries.add(s.id)
        s.update(summary="pending")

        def work() -> None:
            try:
                self.emit("summary_state", {"id": s.id, "state": "waiting"})
                deadline = time.monotonic() + wait
                while not ready():
                    if time.monotonic() > deadline:
                        raise ClaudeError("the transcript did not finish in time")
                    time.sleep(1.0)
                if not transcript.lines(s.transcript_path) and not s.read_notes().strip():
                    s.update(summary=None)
                    self.emit("summary_state", {"id": s.id, "state": "empty"})
                    return
                self.emit("summary_state", {"id": s.id, "state": "writing"})
                text = run_claude(summary_prompt(s), SUMMARY_SYSTEM, self.cfg.summary_model, timeout=600,
                                  on_text=lambda t: self.emit("summary_delta", {"id": s.id, "text": t}))
                atomic_write(s.summary_path, text + "\n")
                s.update(summary="done")
                self.emit("summary_done", {"id": s.id, "summary": text})
            except ClaudeError as exc:
                log.warning("summary of %s not written: %s", s.id, exc)
                self.emit("summary_state", {"id": s.id, "state": "failed", "reason": str(exc),
                                            "offline": exc.offline})
            finally:
                with self._guard:
                    self._summaries.discard(s.id)

        threading.Thread(target=work, name=f"summary-{s.id}", daemon=True).start()
        return True
