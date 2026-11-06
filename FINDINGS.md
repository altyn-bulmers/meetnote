# Findings

Results that hold, each with the experiment it comes from. Hardware: Intel i9-13900H laptop, Ubuntu 24.04, PipeWire 1.0.5.

## The app uses about a third of one core while recording

With the window open, recording an alternating conversation costs 35% of one core: 27% for the transcriber, 5% for the app and 3% for the window. Idle with the window open costs about 1%. Continuous speech on both streams at once costs 45–74% of one core. RAM stays at 1.2 GB, almost all of it the speech model. [02](experiments/02_end_to_end/README.md)

## Speech recognition needs one thread on a performance core

Parakeet TDT 0.6B v3 int8 in sherpa-onnx 2.0.1 needs 10.8 CPU-seconds per minute of speech on one thread; 2 threads need 23 and 4 threads 108, for little or no gain in delay. On an efficiency core, or at nice 10, a 4 s sentence takes up to 5 s to decode instead of 0.6–1.0 s, so the transcriber runs at normal priority and pins itself to the cores with the highest top clock. Transcripts of the English, French, German and Spanish test recordings were exact. [01](experiments/01_feasibility/README.md), [02](experiments/02_end_to_end/README.md)

## Lines appear 1–2 s after a sentence, up to 5 s when the CPU is busy or hot

On a cool, quiet laptop, a line appears 1.2–1.8 s after its sentence ends; the median stays at or below 3 s. Single sentences take up to 5 s while the CPU throttles or other programs load it. The transcriber always catches up. [02](experiments/02_end_to_end/README.md)

## A crash of the app loses no audio

parec writes straight into the segment files and keeps recording when the app is killed with `kill -9`. The restarted app takes over the running recorders, and the speech recorded meanwhile is transcribed without duplicate lines. [02](experiments/02_end_to_end/README.md)

## Capture follows the default device without restarts

With WirePlumber 0.4.17, a parec stream on `@DEFAULT_MONITOR@` and a parec stream with no device both move to the new default output and input when the default changes. parec appends to its output file every 200 ms. [01](experiments/01_feasibility/README.md)

## `claude -p` answers in about 2 s on the Max subscription

With `--safe-mode --tools ""` and a short system prompt, the first token arrives after 1.96 s. A lost connection shows up after about 1 s as an `api_retry` event with `error_status: null`. [01](experiments/01_feasibility/README.md)
