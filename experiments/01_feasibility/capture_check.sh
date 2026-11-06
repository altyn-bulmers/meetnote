#!/usr/bin/env bash
# Does parec write 16 kHz mono PCM straight into a file, often enough for live use,
# and can another process fsync that file? Uses an inaudible virtual sink, so it does
# not touch real devices or the default device.
#
# Run: bash capture_check.sh <out-dir>
set -euo pipefail
export LC_ALL=C  # decimal points in printf, English pactl output
out=${1:?usage: capture_check.sh OUT_DIR}
mkdir -p "$out"
wav="$HOME/.local/share/meetnote/models/test-wavs/en.wav"
venv_py="$(dirname "$0")/../../.venv/bin/python"

mod=$(pactl load-module module-null-sink sink_name=meetnote_probe sink_properties=device.description=meetnote-probe)
cleanup() { kill "${rec_pid:-}" 2>/dev/null || true; pactl unload-module "$mod" 2>/dev/null || true; }
trap cleanup EXIT

parec --raw --format=s16le --rate=16000 --channels=1 --latency-msec=200 \
      --client-name=meetnote-probe -d meetnote_probe.monitor > "$out/probe.pcm" &
rec_pid=$!
sleep 0.5
paplay -d meetnote_probe "$wav" &
play_pid=$!

echo "file size every 0.25 s (32000 bytes = 1 s of audio):"
prev=0
for i in $(seq 1 24); do
    sleep 0.25
    size=$(stat -c %s "$out/probe.pcm")
    printf '  t=%5.2fs size=%7d (+%d)\n' "$(echo "$i*0.25" | bc)" "$size" "$((size - prev))"
    prev=$size
done
wait "$play_pid" || true

echo "fsync of the file from a separate read-only descriptor:"
"$venv_py" -c "import os,sys; fd=os.open(sys.argv[1], os.O_RDONLY); os.fsync(fd); os.close(fd); print('  ok')" "$out/probe.pcm"

kill "$rec_pid"; wait "$rec_pid" 2>/dev/null || true
echo "captured $(stat -c %s "$out/probe.pcm") bytes"
