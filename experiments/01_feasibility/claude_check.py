"""Check the `claude -p` command meetnote uses: flags, time to first token, offline behaviour.

Runs the command with the Max subscription login (API-key variables removed from
the child environment) and reports each stream-json event type with its time.
`--offline` gives only the child a dead proxy, which simulates a lost connection
without touching the machine's network.

Run: ../../.venv/bin/python claude_check.py [--offline] [--model sonnet] [--variant full|append]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time

SYSTEM = "You are a meeting assistant. Answer in at most two sentences."
QUESTION = "In one sentence: what does the acronym RTF mean in speech recognition?"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--variant", choices=["full", "append"], default="full")
    ap.add_argument("--timeout", type=float, default=60)
    args = ap.parse_args()

    cmd = ["claude", "-p", "--safe-mode", "--tools", "", "--model", args.model,
           "--effort", "low", "--output-format", "stream-json", "--verbose",
           "--include-partial-messages", "--no-session-persistence"]
    cmd += ["--system-prompt", SYSTEM] if args.variant == "full" else ["--append-system-prompt", SYSTEM]

    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    if args.offline:
        env["HTTPS_PROXY"] = env["https_proxy"] = "http://127.0.0.1:9"

    with tempfile.TemporaryDirectory() as cwd:
        t0 = time.perf_counter()
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        proc.stdin.write(QUESTION)
        proc.stdin.close()
        first_token = None
        answer = []
        seen: dict[str, float] = {}
        try:
            for line in proc.stdout:
                now = time.perf_counter() - t0
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    print(f"{now:6.2f}s non-JSON: {line[:120]!r}")
                    continue
                kind = ev.get("type", "?")
                sub = ev.get("subtype") or (ev.get("event") or {}).get("type") or ""
                key = f"{kind}/{sub}"
                if key not in seen:
                    seen[key] = now
                    detail = ""
                    if kind == "system" and sub == "init":
                        detail = f" model={ev.get('model')} tools={ev.get('tools')} mcp={ev.get('mcp_servers')}"
                    if kind == "system" and sub == "api_retry":
                        detail = " " + json.dumps({k: ev.get(k) for k in ('attempt', 'max_retries', 'retry_delay_ms', 'error_status', 'error')})[:200]
                    print(f"{now:6.2f}s {key}{detail}")
                if kind == "stream_event":
                    delta = (ev.get("event") or {}).get("delta") or {}
                    if delta.get("type") == "text_delta":
                        if first_token is None:
                            first_token = now
                        answer.append(delta.get("text", ""))
                if kind == "result":
                    print(f"{now:6.2f}s result: is_error={ev.get('is_error')} "
                          f"duration_api_ms={ev.get('duration_api_ms')} cost={ev.get('total_cost_usd')}")
                if now > args.timeout:
                    print(f"{now:6.2f}s giving up (timeout); killing")
                    proc.kill()
                    break
        finally:
            proc.wait(timeout=10)
        err = proc.stderr.read().strip()
    print(f"exit code: {proc.returncode}")
    if first_token is not None:
        print(f"time to first token: {first_token:.2f} s")
    print(f"answer: {''.join(answer).strip()[:300]}")
    if err:
        print(f"stderr: {err[:500]}")


if __name__ == "__main__":
    main()
