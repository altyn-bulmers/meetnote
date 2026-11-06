import json
import sys
import time
from pathlib import Path

import pytest

from meetnote.assistant import Assistant, ClaudeError, ask_prompt, history, run_claude
from meetnote.config import Config
from meetnote.durable import JsonlWriter
from meetnote.session import Session

HERE = Path(__file__).parent


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(f"#!{sys.executable}\n" + (HERE / "fake_claude.py").read_text())
    script.chmod(0o755)
    log = tmp_path / "claude-calls.jsonl"
    monkeypatch.setenv("MEETNOTE_CLAUDE", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    return log


def calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def write_lines(s, items):
    with JsonlWriter(s.transcript_path) as w:
        for src, w0, text in items:
            w.append({"type": "line", "src": src, "seg": f"audio/{src}.pcm", "s0": int(w0 * 16000),
                      "s1": int((w0 + 2) * 16000), "w0": w0, "w1": w0 + 2, "t0": "", "text": text})


def wait_for(events, kind, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [d for k, d in events if k == kind]
        if found:
            return found
        time.sleep(0.05)
    return []


def test_streams_the_answer_with_safe_flags_and_without_api_keys(fake_claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "not-a-real-token")
    chunks = []
    answer = run_claude("my question", "my system prompt", "sonnet", on_text=chunks.append, effort="low")
    assert answer == "The release moves to Friday."
    assert "".join(chunks) == answer
    call = calls(fake_claude)[0]
    assert call["api_key"] is False and call["auth_token"] is False  # the plan pays, not the API
    argv = call["argv"]
    for flag in ("-p", "--safe-mode", "--no-session-persistence", "--include-partial-messages", "--verbose"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "low"
    assert argv[argv.index("--system-prompt") + 1] == "my system prompt"
    assert call["prompt"] == "my question"
    assert call["cwd"].endswith("/claude")


def test_offline_is_detected_within_seconds(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "offline")
    started = time.monotonic()
    with pytest.raises(ClaudeError) as err:
        run_claude("q", "s", "sonnet")
    assert err.value.offline
    assert time.monotonic() - started < 5


def test_error_results_are_reported(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "error")
    with pytest.raises(ClaudeError, match="Usage limit"):
        run_claude("q", "s", "sonnet")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "silent")
    with pytest.raises(ClaudeError, match="exit code 3"):
        run_claude("q", "s", "sonnet")


def test_missing_cli_is_reported(monkeypatch):
    monkeypatch.setenv("MEETNOTE_CLAUDE", "")
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(ClaudeError, match="not installed"):
        run_claude("q", "s", "sonnet")


def test_question_gets_the_recent_transcript_and_the_notes():
    s = Session.create("Planning")
    write_lines(s, [("others", 1000.0, "An old topic from long ago."),
                    ("others", 1700.0, "The release moves to Friday."),
                    ("me", 1700.2, "The release moves to Friday."),  # echo of the speakers
                    ("me", 1705.0, "Fine by me.")])
    s.write_notes("- ask about the budget")
    prompt = ask_prompt(s, "When is the release?", minutes=5)
    assert "old topic" not in prompt
    assert prompt.count("The release moves to Friday.") == 1
    assert "Me: Fine by me." in prompt and "- ask about the budget" in prompt
    assert prompt.rstrip().endswith("My question: When is the release?")
    assert "old topic" in ask_prompt(s, "q", minutes=None)


def test_failed_question_can_be_retried(fake_claude, monkeypatch):
    events = []
    a = Assistant(Config(), lambda kind, data: events.append((kind, data)))
    s = Session.create("m")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "offline")
    qid = a.ask(s, "What was decided?")
    failed = wait_for(events, "answer_failed")
    assert failed and failed[0]["offline"] and failed[0]["qid"] == qid
    assert history(s)[0]["status"] == "failed"
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "answer")
    a.retry(s, qid)
    done = wait_for(events, "answer_done")
    assert done and done[0]["answer"] == "The release moves to Friday."
    assert [(q["id"], q["status"]) for q in history(s)] == [(qid, "answered")]
    assert any(kind == "answer_delta" for kind, _ in events)


def test_summary_waits_for_the_transcript_then_is_written(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_ANSWER", "## Summary\nShort.")
    events = []
    a = Assistant(Config(), lambda kind, data: events.append((kind, data)))
    s = Session.create("m")
    write_lines(s, [("others", 1000.0, "Hello.")])
    transcript_done = {"yes": False}
    assert a.summarize(s, ready=lambda: transcript_done["yes"])
    assert a.summarize(s, ready=lambda: True) is False  # one summary job per session
    time.sleep(0.5)
    assert not s.summary_path.exists()
    assert Session(s.folder).meta["summary"] == "pending"
    transcript_done["yes"] = True
    assert wait_for(events, "summary_done")
    assert s.summary_path.read_text() == "## Summary\nShort.\n"
    assert Session(s.folder).meta["summary"] == "done"
    assert "Others: Hello." in calls(fake_claude)[-1]["prompt"]


def test_summary_written_offline_stays_pending(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "offline")
    events = []
    a = Assistant(Config(), lambda kind, data: events.append((kind, data)))
    s = Session.create("m")
    write_lines(s, [("others", 1000.0, "Hello.")])
    a.summarize(s, ready=lambda: True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(
            k == "summary_state" and d["state"] == "failed" for k, d in events):
        time.sleep(0.05)
    assert any(k == "summary_state" and d["state"] == "failed" and d["offline"] for k, d in events)
    assert Session(s.folder).meta["summary"] == "pending"
    assert not s.summary_path.exists()
