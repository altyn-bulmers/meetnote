"""Drive the real meetnote window through a short scripted meeting and take screenshots.

Plays the model's test recordings into two inaudible virtual devices, clicks
Start, types notes, clicks Stop, and saves screenshots of the meetnote window
only (never the whole screen). Uses a temporary meetnote home.

The meeting run also asks Claude a question, opens the pop-out Ask window and
waits for the summary, which uses the Claude subscription (two short calls).
`--offline` gives only meetnote's Claude calls a dead proxy and checks that a
question fails fast with Retry.

Run: python gui_check.py OUT_DIR [--offline]
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
OFFLINE = "--offline" in sys.argv
if OFFLINE:
    os.environ["HTTPS_PROXY"] = os.environ["https_proxy"] = "http://127.0.0.1:9"
os.environ["MEETNOTE_HOME"] = str(out / "home")
os.environ.setdefault("MEETNOTE_MODELS_DIR", str(Path.home() / ".local/share/meetnote/models"))
os.environ["MEETNOTE_MIC_DEVICE"] = "meetnote_gui_me.monitor"
os.environ["MEETNOTE_OUTPUT_DEVICE"] = "meetnote_gui_others.monitor"

from meetnote import models  # noqa: E402
from meetnote.app import App  # noqa: E402

WAVS = models.model_dir(models.TEST_WAVS)
modules = [subprocess.run(["pactl", "load-module", "module-null-sink", f"sink_name=meetnote_gui_{n}"],
                          capture_output=True, text=True, check=True).stdout.strip() for n in ("me", "others")]


def shot(name: str, window: str = "meetnote") -> None:
    info = subprocess.run(["xwininfo", "-name", window], capture_output=True, text=True).stdout
    m = re.search(r"Window id: (0x[0-9a-f]+)", info)
    if m:
        subprocess.run(["import", "-window", m[1], str(out / f"{name}.png")], check=False)
        print(f"screenshot {name}.png", flush=True)
    else:
        print("meetnote window not found", flush=True)


def play(src: str, wav: str) -> None:
    subprocess.run(["paplay", "-d", f"meetnote_gui_{src}", str(WAVS / f"{wav}.wav")], check=False)


def cpu_by_process(root: int) -> dict[str, float]:
    """CPU time per process name for `root` and all its descendants."""
    parent, cpu, name = {}, {}, {}
    tick = os.sysconf("SC_CLK_TCK")
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            raw = open(f"/proc/{pid}/stat").read()
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        fields = raw.rsplit(")", 1)[1].split()
        parent[int(pid)] = int(fields[1])
        cpu[int(pid)] = (int(fields[11]) + int(fields[12])) / tick
        comm = raw[raw.find("(") + 1:raw.rfind(")")]
        name[int(pid)] = "transcriber" if b"meetnote.transcriber" in b" ".join(cmd) else comm
    totals: dict[str, float] = {}
    for pid in cpu:
        p = pid
        while p and p != root:
            p = parent.get(p, 0)
        if p == root:
            label = "app" if pid == root else name[pid]
            totals[label] = totals.get(label, 0.0) + cpu[pid]
    return totals


def cpu_seconds_of_tree(root: int) -> float:
    """CPU time of `root` and all its descendants: window (WebKit), transcriber, parec."""
    return sum(cpu_by_process(root).values())


def measure(seconds: float) -> float:
    """Share of one core used by the whole app over the next `seconds`."""
    c0, t0 = cpu_seconds_of_tree(os.getpid()), time.monotonic()
    time.sleep(seconds)
    return (cpu_seconds_of_tree(os.getpid()) - c0) / (time.monotonic() - t0)


def wait_js(js, expression: str, done, timeout: float) -> str:
    """Evaluate `expression` in the page until `done(value)` holds; returns the last value."""
    deadline = time.monotonic() + timeout
    value = ""
    while time.monotonic() < deadline:
        value = js(expression) or ""
        if done(value):
            break
        time.sleep(0.5)
    return value


STATUS = "(document.querySelector('.qa-item .qa-status') || {}).textContent || ''"


def offline_script(app: App) -> None:
    js = app.window.evaluate_js
    try:
        time.sleep(3)
        js("document.getElementById('title').value = 'Offline check'; document.getElementById('start').click()")
        time.sleep(3)
        started = time.monotonic()
        js("document.getElementById('question').value = 'Are you there?'; document.getElementById('send').click()")
        status = wait_js(js, STATUS, lambda v: "Retry" in v, 30)
        print(f"offline question: {status!r} after {time.monotonic() - started:.1f} s", flush=True)
        shot("offline-ask")
        js("document.getElementById('stop').click()")
        summary = wait_js(js, "document.getElementById('summary-status').textContent",
                          lambda v: v.startswith("Not written") or v.startswith("Nothing"), 60)
        print(f"offline summary: {summary!r}", flush=True)
    finally:
        app._allow_close = True
        app.window.destroy()


def script(app: App) -> None:
    js = app.window.evaluate_js
    try:
        time.sleep(3)
        shot("1-empty")
        print(f"idle, no recording: {measure(10):.1%} of one core", flush=True)
        js("document.getElementById('title').value = 'GUI check'; document.getElementById('start').click()")
        time.sleep(8)  # the transcriber loads and warms up the model
        before, t0 = cpu_by_process(os.getpid()), time.monotonic()
        for _ in range(2):
            for src, wav in (("others", "en"), ("me", "fr"), ("others", "de"), ("me", "es")):
                play(src, wav)
                time.sleep(1.5)
        after, wall = cpu_by_process(os.getpid()), time.monotonic() - t0
        parts = {k: (v - before.get(k, 0.0)) / wall for k, v in after.items()}
        print(f"recording a conversation: {sum(parts.values()):.1%} of one core; "
              + ", ".join(f"{k} {v:.1%}" for k, v in sorted(parts.items(), key=lambda kv: -kv[1]) if v > 0.002),
              flush=True)
        js("const n = document.getElementById('notes');"
           "n.value = '## Decisions\\n- Release moves to **Friday**\\n- [ ] Send the slides';"
           "n.dispatchEvent(new Event('input'))")
        time.sleep(3)
        shot("2-recording")
        started = time.monotonic()
        js("document.getElementById('question').value = 'What did the others say about the country?';"
           "document.getElementById('send').click()")
        status = wait_js(js, STATUS, lambda v: v.endswith(" s") or "Retry" in v, 120)
        print(f"question answered: {status!r} after {time.monotonic() - started:.1f} s", flush=True)
        js("pywebview.api.popout_ask()")
        time.sleep(3)
        shot("3-ask-popout", window="meetnote: Ask")
        js("document.getElementById('stop').click()")
        started = time.monotonic()
        summary = wait_js(js, "document.getElementById('summary-status').textContent",
                          lambda v: v.startswith("Written") or v.startswith("Not written"), 240)
        print(f"summary: {summary!r} after {time.monotonic() - started:.1f} s", flush=True)
        print(f"idle after the meeting: {measure(10):.1%} of one core", flush=True)
        js("document.getElementById('tab-summary').click()")
        time.sleep(1)
        shot("4-summary")
    finally:
        app._allow_close = True
        app.window.destroy()


app = App()
app._on_started = lambda: threading.Thread(target=offline_script if OFFLINE else script,
                                           args=(app,), daemon=True).start()
try:
    code = app.run()
finally:
    for module in modules:
        subprocess.run(["pactl", "unload-module", module], check=False)
sys.exit(code)
