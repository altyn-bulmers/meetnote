"""Drive the real meetnote window through a short scripted meeting and take screenshots.

Plays the model's test recordings into two inaudible virtual devices, clicks
Start, types notes, clicks Stop, and saves screenshots of the meetnote window
only (never the whole screen). Uses a temporary meetnote home.

Run: python gui_check.py OUT_DIR
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
os.environ["MEETNOTE_HOME"] = str(out / "home")
os.environ.setdefault("MEETNOTE_MODELS_DIR", str(Path.home() / ".local/share/meetnote/models"))
os.environ["MEETNOTE_MIC_DEVICE"] = "meetnote_gui_me.monitor"
os.environ["MEETNOTE_OUTPUT_DEVICE"] = "meetnote_gui_others.monitor"

from meetnote import models  # noqa: E402
from meetnote.app import App  # noqa: E402

WAVS = models.model_dir(models.TEST_WAVS)
modules = [subprocess.run(["pactl", "load-module", "module-null-sink", f"sink_name=meetnote_gui_{n}"],
                          capture_output=True, text=True, check=True).stdout.strip() for n in ("me", "others")]


def shot(name: str) -> None:
    info = subprocess.run(["xwininfo", "-name", "meetnote"], capture_output=True, text=True).stdout
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
        js("document.getElementById('stop').click()")
        time.sleep(6)  # the transcriber finishes and exits
        print(f"idle after the meeting: {measure(10):.1%} of one core", flush=True)
        js("document.getElementById('preview-toggle').click()")
        time.sleep(1)
        shot("3-stopped-preview")
    finally:
        app._allow_close = True
        app.window.destroy()


app = App()
app._on_started = lambda: threading.Thread(target=script, args=(app,), daemon=True).start()
try:
    code = app.run()
finally:
    for module in modules:
        subprocess.run(["pactl", "unload-module", module], check=False)
sys.exit(code)
