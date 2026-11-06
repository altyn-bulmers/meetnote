#!/usr/bin/env bash
# End-to-end check of `meetnote record` with two inaudible virtual devices.
# Plays the model's test recordings as "me" and "others", measures how fast lines
# appear and how much CPU and RAM everything uses, kills the app mid-meeting,
# restarts it and checks that the transcript is complete. Real devices and the
# default devices are not touched. Results are analysed by check.py.
#
# Run: bash run.sh OUT_DIR
set -euo pipefail
export LC_ALL=C
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
mkdir -p "${1:?usage: run.sh OUT_DIR}"
out=$(cd "$1" && pwd)
rm -rf "$out/home"
: > "$out/app.log"
: > "$out/events.log"
wavs="$HOME/.local/share/meetnote/models/test-wavs"
export MEETNOTE_HOME="$out/home"
export MEETNOTE_MODELS_DIR="$HOME/.local/share/meetnote/models"
export MEETNOTE_MIC_DEVICE=meetnote_e2e_me.monitor
export MEETNOTE_OUTPUT_DEVICE=meetnote_e2e_others.monitor
meetnote="$repo/.venv/bin/meetnote"
py="$repo/.venv/bin/python"

m1=$(pactl load-module module-null-sink sink_name=meetnote_e2e_me sink_properties=device.description=meetnote-e2e-me)
m2=$(pactl load-module module-null-sink sink_name=meetnote_e2e_others sink_properties=device.description=meetnote-e2e-others)
cleanup() {
    [ -n "${app_pid:-}" ] && kill -TERM "$app_pid" 2>/dev/null || true
    sleep 1
    pkill -TERM -f "meetnote.transcriber $MEETNOTE_HOME" 2>/dev/null || true
    pactl unload-module "$m1" 2>/dev/null || true
    pactl unload-module "$m2" 2>/dev/null || true
}
trap cleanup EXIT

now() { date +%s.%3N; }
event() { echo "$(now) $*" >> "$out/events.log"; }
play() {  # play SRC WAV: play into SRC's virtual device, then log the end time
    paplay -d "meetnote_e2e_$1" "$wavs/$2.wav"
    event played "$1" "$2"
}
start_app() {
    "$meetnote" record --title e2e 2>&1 | while IFS= read -r line; do echo "$(now) $line"; done >> "$out/app.log" &
    for _ in $(seq 1 50); do
        app_pid=$(pgrep -n -f "meetnote record --title e2e" || true)
        [ -n "$app_pid" ] && break
        sleep 0.1
    done
    event started app "$app_pid"
}
usage() {  # total CPU seconds and RSS (MB) of meetnote's processes: app, transcriber, parec
    "$py" - <<'EOF'
import os
tick, cpu, rss = os.sysconf("SC_CLK_TCK"), 0.0, 0
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        if not any(k in cmd for k in ("meetnote record", "meetnote.transcriber", "stream-name=meetnote-")):
            continue
        fields = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        cpu += (int(fields[11]) + int(fields[12])) / tick
        rss += int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
    except OSError:
        pass
print(f"{cpu:.2f} {rss / 1e6:.0f}")
EOF
}

echo "phase A: single utterances, live delay"
start_app
sleep 8  # the transcriber loads and warms up the model (about 4 s)
for pair in "others en" "me fr" "others de" "me es"; do
    # shellcheck disable=SC2086
    set -- $pair
    play "$1" "$2"
    sleep 3
done

echo "phase B: 60 s of continuous speech on both streams, CPU and RAM"
read -r cpu0 _ <<< "$(usage)"
t0=$(now)
( end=$(( $(date +%s) + 60 )); while [ "$(date +%s)" -lt "$end" ]; do for w in en fr de es; do paplay -d meetnote_e2e_others "$wavs/$w.wav"; done; done ) &
p1=$!
( end=$(( $(date +%s) + 60 )); while [ "$(date +%s)" -lt "$end" ]; do for w in es de fr en; do paplay -d meetnote_e2e_me "$wavs/$w.wav"; done; done ) &
p2=$!
sleep 50
read -r _ rss <<< "$(usage)"
wait "$p1" "$p2"
read -r cpu1 _ <<< "$(usage)"
t1=$(now)
event usage cpu_seconds "$(echo "$cpu1 - $cpu0" | bc)" wall_seconds "$(echo "$t1 - $t0" | bc)" rss_mb "$rss"
sleep 8  # let the transcriber catch up before the crash

echo "phase C: kill -9 the app, keep talking, restart"
kill -9 "$app_pid"
event killed app "$app_pid"
sleep 0.5
play others en
play me fr
start_app  # takes over the running parec processes
sleep 10

echo "phase D: stop and finish the transcript"
kill -TERM "$app_pid"
event stopped app "$app_pid"
for _ in $(seq 1 240); do
    pgrep -f "meetnote record --title e2e" > /dev/null || pgrep -f "meetnote.transcriber $MEETNOTE_HOME" > /dev/null || break
    sleep 0.5
done
app_pid=""
event finished
"$py" "$here/check.py" "$out"
