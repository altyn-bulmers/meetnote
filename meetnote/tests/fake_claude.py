"""Stand-in for the `claude` CLI in tests: prints stream-json events like `claude -p`.

FAKE_CLAUDE_MODE: answer (default), offline, error or silent.
FAKE_CLAUDE_ANSWER: the answer text.
FAKE_CLAUDE_LOG: file that records each call as a JSON line (argv, cwd, key variables seen, prompt).
"""

import json
import os
import sys
import time

prompt = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
                            "api_key": "ANTHROPIC_API_KEY" in os.environ,
                            "auth_token": "ANTHROPIC_AUTH_TOKEN" in os.environ,
                            "prompt": prompt}) + "\n")


def emit(obj):
    print(json.dumps(obj), flush=True)


mode = os.environ.get("FAKE_CLAUDE_MODE", "answer")
emit({"type": "system", "subtype": "init", "model": "fake", "tools": []})
if mode == "offline":
    emit({"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10,
          "retry_delay_ms": 500, "error_status": None, "error": "unknown"})
    time.sleep(60)  # the real CLI keeps retrying; meetnote has to stop it
elif mode == "error":
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
          "result": "Usage limit reached"})
elif mode == "silent":
    sys.exit(3)
else:
    words = os.environ.get("FAKE_CLAUDE_ANSWER", "The release moves to Friday.").split(" ")
    for i, word in enumerate(words):
        emit({"type": "stream_event",
              "event": {"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": word if i == 0 else " " + word}}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": " ".join(words)})
