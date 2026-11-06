# 01 Feasibility

## Question

Does meetnote's design meet its targets on this laptop (i9-13900H, Ubuntu 24.04, PipeWire 1.0.5)?
- transcription in real time at well under one CPU core;
- parec writing audio straight into a file that another process can fsync;
- capture that follows a change of the default output and input device;
- `claude -p` usable from the app with the Max subscription, with a short delay and a clear offline signal.

## Design and how to run

Run from this folder with the project venv. Model runs are capped at 4 GB of address space.

| Script | What it does |
|---|---|
| `asr_bench.py --threads N` | Builds 3 min of synthetic meeting audio from the model's test recordings (en, fr, de, es, separated by 1.5 s of silence), feeds it to Silero VAD in 512-sample windows, transcribes each utterance with Parakeet TDT 0.6B v3 int8 (sherpa-onnx 2.0.1), and reports CPU time, delay per utterance and RAM. Run as `timeout 600 prlimit --as=4000000000 ../../.venv/bin/python asr_bench.py --threads 1`. |
| `capture_check.sh OUT` | Plays a test recording into an inaudible virtual sink, records its monitor with parec into a file, prints the file size every 0.25 s and fsyncs the file from a second descriptor. |
| `follow_check.sh OUT` | Starts the two capture streams, makes two virtual sinks the default in turn and reports which device each stream is connected to. It refuses to run while another app records, and restores the original defaults. |
| `claude_check.py [--offline]` | Runs meetnote's `claude -p` command and prints every stream-json event with its time. `--offline` gives only the child process a dead proxy. |

## Answer

All four targets are met. Measured on 2026-11-06, plugged in, power profile "performance".

**Transcription.** With 1 thread, Parakeet needs 10.8 CPU-seconds per minute of continuous speech, which is 18% of one core. It decodes a 4 s utterance in about 1.0 s, loads in 2.3 s and peaks at 930 MB of RAM. More threads cost more CPU for little gain, because onnxruntime threads spin while they wait:

| Threads | CPU-s per audio minute | Decode of a 4.1 s utterance |
|---|---|---|
| 1 | 10.8 | 0.99 s |
| 2 | 23.4 | 0.65 s |
| 4 | 107.7 | 1.42 s |

The transcripts of all four test recordings were exact, with punctuation and capitals. The app therefore defaults to 1 thread (`asr_threads` in the config). A 20 s utterance, the longest the VAD allows, takes about 5 s to decode.

**Capture.** parec appends to its output file every 200 ms at exactly 32,000 bytes per second, and fsync from a separate read-only descriptor works. The captured file transcribed correctly, which confirms the format (s16le, 16 kHz, mono).

**Device following.** When the default output and input moved from virtual sink A to B, both streams moved with them: `@DEFAULT_MONITOR@` for the output and a stream with no device for the input. parec kept running, so no restart is needed. WirePlumber 0.4.17's default `follow = true` policy does this.

**Claude.** This command works with the Max subscription login:

    claude -p --safe-mode --tools "" --system-prompt <prompt> --model sonnet --effort low \
      --output-format stream-json --verbose --include-partial-messages --no-session-persistence

It starts with no tools and no MCP servers. The first answer token arrives after 1.96 s and the full two-sentence answer after 2.5 s. Without a network, an `api_retry` event with `error_status: null` arrives after 1.1 s; the CLI would then retry 10 times, so the app stops it at that event.
