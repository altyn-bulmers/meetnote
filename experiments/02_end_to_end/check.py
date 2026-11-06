"""Analyse a run of run.sh: live delay, CPU and RAM, crash recovery, leftovers.

Run: python check.py OUT_DIR
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

KEYWORD = {"en": "country", "fr": "pays", "de": "wurst", "es": "país"}
LABEL = {"me": "Me", "others": "Others"}


def main(out: Path) -> int:
    events = [line.split() for line in (out / "events.log").read_text().splitlines()]
    app = []  # (time, src label, text)
    for line in (out / "app.log").read_text().splitlines():
        m = re.match(r"(\d+\.\d+) \[\d\d:\d\d:\d\d\] (Me|Others): (.*)", line)
        if m:
            app.append((float(m[1]), m[2], m[3].lower()))
    sessions = list((out / "home/data/sessions").iterdir())
    assert len(sessions) == 1, sessions
    folder = sessions[0]
    meta = json.loads((folder / "session.json").read_text())
    lines = [json.loads(x) for x in (folder / "transcript.jsonl").read_text().splitlines()]
    lines = [r for r in lines if r["type"] == "line"]
    ok = True

    def report(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"{'ok  ' if passed else 'FAIL'}  {label}{': ' + detail if detail else ''}")

    # Phase A: delay from the end of each played utterance to its line in the terminal.
    plays = [e for e in events if e[1] == "played"][:4]
    delays = []
    for t, _, src, wav in plays:
        hits = [a for a in app if a[0] > float(t) and a[1] == LABEL[src] and KEYWORD[wav] in a[2]]
        delays.append(hits[0][0] - float(t) if hits else None)
    shown = ", ".join("missing" if d is None else f"{d:.1f} s" for d in delays)
    complete = all(d is not None for d in delays)
    report("phase A: every utterance shown live, median delay at most 3 s",
           complete and statistics.median(delays) <= 3.0, shown)

    # Phase B: CPU and RAM during 60 s of continuous speech on both streams.
    usage = next(e for e in events if e[1] == "usage")
    cpu, wall, rss = float(usage[3]), float(usage[5]), float(usage[7])
    per_min = 60 * cpu / wall
    report("phase B: under 1 core on average", per_min < 60,
           f"{per_min:.1f} CPU-s per minute ({per_min / 60:.0%} of one core), RSS {rss:.0f} MB")
    report("phase B: under 2 GB of RAM", rss < 2000)

    # Phase C: speech played while the app was dead is in the transcript, without duplicates.
    killed = float(next(e for e in events if e[1] == "killed")[0])
    after = [r for r in lines if r["w0"] > killed]
    report("phase C: speech during the crash was recorded and transcribed",
           any(r["src"] == "others" and "country" in r["text"].lower() for r in after)
           and any(r["src"] == "me" and "pays" in r["text"].lower() for r in after),
           f"{len(after)} lines after the kill")
    keys = [(r["seg"], r["s0"]) for r in lines]
    report("no duplicate lines", len(keys) == len(set(keys)), f"{len(lines)} lines in total")
    report("app took over the running recorders",
           any("adopted" in line for line in (out / "app.log").read_text().splitlines()))
    by_src = {src: sum(1 for g in meta["segments"] if g["src"] == src) for src in LABEL}
    report("one segment per source (no restart needed)", by_src == {"me": 1, "others": 1}, str(by_src))
    report("session stopped, all segments closed",
           meta["status"] == "stopped" and all(g["closed"] for g in meta["segments"]))
    left = subprocess.run(["pgrep", "-af", "stream-name=meetnote-"], capture_output=True, text=True).stdout
    report("no parec left running", not left.strip(), left.strip())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
