# 02 End to end

## Question

Does the whole app work on this laptop? Lines should appear a few seconds after each sentence, CPU use should stay under one core, and killing the app mid-meeting should lose no audio and create no duplicate lines.

## Design and how to run

All scripts use two inaudible virtual devices in place of the microphone and the speakers, so real devices, the default devices and the room are never recorded. Run them from the repository root with the project venv.

`bash experiments/02_end_to_end/run.sh OUT` drives `meetnote record` through four phases and then runs `check.py OUT`:
- A: four test recordings (en, fr, de, es), alternating Me and Others; it measures the delay from the end of each recording to its line in the terminal;
- B: 60 s of continuous speech on both streams at once; it measures the CPU time and RAM of the app, the transcriber and parec;
- C: `kill -9` of the app, two more recordings while it is down, then a restart;
- D: stop with SIGTERM, and the transcriber finishes.

`.venv/bin/python experiments/02_end_to_end/gui_check.py OUT` drives the real window through a short meeting, measures the CPU of the whole process tree, window included, and saves screenshots of the window only.

## Answer

Yes. Measured on 2026-11-06.

**Crash recovery.** After `kill -9`, both parec processes kept recording. The restarted app took them over without starting new segments, transcribed the speech played while it was down and wrote no duplicate lines. Nothing was left running after Stop.

**CPU and RAM.** With the window open, recording an alternating conversation costs 35% of one core in total: 27% for the transcriber, 5% for the app and 3% for the WebKit page. Idle with the window open costs about 1%. Continuous speech on both streams at once, the worst case, cost 45–74% of one core across four runs. RAM stays at 1.2 GB, almost all of it the speech model.

**Delay.** On a cool, quiet laptop, a line appears 1.2–1.8 s after its sentence ends. Single decodes sometimes take 3–5 s; this happens when the CPU throttles (this laptop logged about 214,000 package-throttle events in 2 days) or when other programs load it. The median delay stays at or below 3 s with these three settings, all found in this experiment:

| Setting | Effect |
|---|---|
| Normal priority instead of nice 10 | At nice 10 the scheduler moved decoding to the efficiency cores, which decode a 4 s sentence in 5.2 s instead of 1.0 s. |
| Transcriber pinned to the performance cores | Steady decode of a 4 s sentence: 0.56 s (worst 0.73 s) instead of 1.34 s (worst 3.9 s). The cores are chosen by their top clock, so CPUs without efficiency cores are not restricted. |
| One warm-up decode after the model loads | The first real decode is no longer 1–2 s slower. |

**Window.** Two CSS animations, a blinking recording dot and sliding level meters, made the page repaint continuously. Removing them cut the window's share from 27% to 8% of one core.

**Claude.** With the real CLI on the Max subscription, a question asked during the meeting was fully answered in 3.3 s, and the pop-out Ask window showed the same answer. The summary, with its four sections, appeared 16 s after Stop, transcript completion included. With `--offline`, a question failed after 1.0 s with Retry, and no `claude` process was left running.

Phase A delays and phase B CPU of the four runs of `run.sh`:

| Run | Phase A delays (s) | Phase B CPU (one core) |
|---|---|---|
| nice 10 | 1.2, 4.2, 2.3, 3.8 | 54% |
| normal priority, another job using 17 cores | 1.7, 6.7, 2.9, 4.0 | 62% |
| pinned and warm-up; the first delay includes model loading | 6.8, 3.9, 2.6, 3.6 | 45% |
| pinned and warm-up, 8 s start | 1.4, 1.3, 5.0, 3.9 | 74% |
