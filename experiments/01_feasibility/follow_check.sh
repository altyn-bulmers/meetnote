#!/usr/bin/env bash
# Do meetnote's two capture streams follow a change of the default output and input?
#   output: parec -d @DEFAULT_MONITOR@
#   input:  parec with no device
# Creates two inaudible virtual sinks, makes each the default for about 3 s, checks
# which device each stream is connected to, then restores the original defaults.
# Refuses to run while any other app is recording, so it cannot mute a call.
#
# Run: bash follow_check.sh <out-dir>
set -euo pipefail
export LC_ALL=C  # decimal points in printf, English pactl output
out=${1:?usage: follow_check.sh OUT_DIR}
mkdir -p "$out"

others=$(pactl --format=json list source-outputs | python3 -c "
import json, sys
names = [o['properties'].get('application.name', '?') for o in json.load(sys.stdin)]
print(', '.join(n for n in names if not n.startswith('meetnote')))")
if [ -n "$others" ]; then
    echo "another app is recording ($others); not changing the default devices"; exit 3
fi

orig_sink=$(pactl get-default-sink)
orig_source=$(pactl get-default-source)
mod_a=$(pactl load-module module-null-sink sink_name=meetnote_follow_a sink_properties=device.description=meetnote-follow-a)
mod_b=$(pactl load-module module-null-sink sink_name=meetnote_follow_b sink_properties=device.description=meetnote-follow-b)
cleanup() {
    pactl set-default-sink "$orig_sink" || true
    pactl set-default-source "$orig_source" || true
    kill "${out_pid:-}" "${in_pid:-}" 2>/dev/null || true
    pactl unload-module "$mod_a" 2>/dev/null || true
    pactl unload-module "$mod_b" 2>/dev/null || true
    echo "restored defaults: sink=$(pactl get-default-sink) source=$(pactl get-default-source)"
}
trap cleanup EXIT

# Which source is each of our streams connected to?
where() {
    pactl --format=json list source-outputs | python3 -c "
import json, sys
src = {s['index']: s['name'] for s in json.loads(sys.argv[1])}
for o in json.load(sys.stdin):
    name = o['properties'].get('application.name', '')
    if name.startswith('meetnote-follow'):
        print(f\"    {name}: {src.get(o['source'], o['source'])}\")" "$(pactl --format=json list sources)"
}

pactl set-default-sink meetnote_follow_a
pactl set-default-source meetnote_follow_a.monitor
sleep 0.5
parec --raw --format=s16le --rate=16000 --channels=1 --latency-msec=200 \
      --client-name=meetnote-follow-output -d @DEFAULT_MONITOR@ > "$out/output.pcm" &
out_pid=$!
parec --raw --format=s16le --rate=16000 --channels=1 --latency-msec=200 \
      --client-name=meetnote-follow-input > "$out/input.pcm" &
in_pid=$!
sleep 2
echo "defaults = A:"; where

pactl set-default-sink meetnote_follow_b
pactl set-default-source meetnote_follow_b.monitor
sleep 2
echo "defaults = B (expected: both streams on meetnote_follow_b.monitor):"; where

echo "parec still running: output=$(kill -0 $out_pid 2>/dev/null && echo yes || echo no) input=$(kill -0 $in_pid 2>/dev/null && echo yes || echo no)"
