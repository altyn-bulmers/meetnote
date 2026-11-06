# meetnote

Meeting notes for Linux that transcribe live, offline, and never lose a recording.

meetnote records your microphone and what your computer plays, so both sides of a call are captured and labelled "Me" and "Others". A speech model running on your own CPU turns each sentence into text a second or two after it is spoken, and you write your own notes next to the live transcript. Everything is written to disk as it happens: if the app crashes, the network drops or the laptop loses power, what was recorded stays saved and meetnote continues from there.

During the meeting you can ask Claude a question in a side panel, or in a small window that stays on top of the call, and get an answer in a few seconds. When you press Stop, Claude writes a summary with decisions and action items. Claude runs through your Claude Code login, so it uses your existing Claude subscription at no extra cost.

## Status

| Part | State |
|---|---|
| Recording both sides; following headphones and device changes | works |
| Live offline transcript in 25 European languages, including English, French and Russian | works |
| Notes in Markdown with preview, autosaved | works |
| Crash recovery: recording continues if the app dies, and the transcript resumes | works |
| Claude: questions during the meeting, summary at the end | works |
| macOS | later |

Tested on Ubuntu 24.04 (GNOME, X11, PipeWire 1.0) with an Intel i9-13900H.

## Install

You need Linux with PipeWire or PulseAudio, Python 3.11 or newer, the GTK and WebKitGTK bindings, and about 700 MB of disk for the speech model. On Ubuntu:

    sudo apt install python3-gi gir1.2-webkit2-4.1 pulseaudio-utils python3-venv
    git clone https://github.com/altyn-bulmers/meetnote.git
    cd meetnote
    ./install.sh

`install.sh` creates a virtual environment in `.venv`, downloads the speech model once (about 670 MB, checked against fixed SHA-256 sums), adds meetnote to the app grid and runs `meetnote doctor` to check the setup.

For the Claude features, install [Claude Code](https://claude.com/claude-code) and log in with your Claude account (`claude`, then `/login`). Recording and transcription work without it.

## Use

Open meetnote from the app grid, or run `.venv/bin/meetnote`. Press Start recording, take notes while the transcript fills in, and press Stop at the end. Past meetings are listed on the left.

- **Mic on / Mic muted** stops recording your microphone, for example while you are muted in the call.
- **Headphones** can be plugged in or switched at any time; recording follows the system's default input and output.
- **Laptop speakers**: the microphone also hears the call. meetnote hides "Me" lines that repeat what "Others" just said; "Show echo lines" shows them.
- **Ask Claude** (right panel, or Ctrl+K): Claude answers from the last 10 minutes of transcript and your notes, or from the whole meeting if you tick the box. **Pop out** opens a small window that stays on top of the call. Without a connection a question fails within seconds and offers Retry.
- **Summary**: after Stop, the Summary tab next to Notes fills in once the transcript is complete. If Claude cannot be reached, the summary is written the next time meetnote starts, or when you press Try again.
- **Closing the window** during a recording asks whether to stop or keep recording in the background.
- **After a crash**, start meetnote again. A recording that kept running is taken over, and an interrupted one can be resumed into the same meeting.

From the terminal:

| Command | What it does |
|---|---|
| `meetnote record [--title T]` | record and transcribe in the terminal, without the window |
| `meetnote sessions` | list meetings |
| `meetnote transcribe ID` | finish the transcript of a meeting |
| `meetnote models` | download the speech model |
| `meetnote doctor` | check the setup |

## Your data

Everything stays on your computer in `~/.local/share/meetnote/sessions/`, one folder per meeting with its audio, transcript, notes, questions and summary. Audio is raw 16 kHz PCM, about 115 MB per hour for each side, and is never sent anywhere. When you ask a question or a summary is written, the transcript text concerned and your notes go to Anthropic through Claude Code; nothing else leaves the computer. Recording other people can require their consent, so tell participants when you record.

## Settings

Optional, in `~/.config/meetnote/config.toml`:

    asr_threads = 1          # more threads: lower delay, more CPU
    max_hours = 6.0          # a recorder left behind by a crash stops after this
    ask_model = "sonnet"     # Claude model for questions
    summary_model = "opus"   # Claude model for summaries
    context_minutes = 10     # minutes of transcript sent with a question

## How it works

Two `parec` processes record the default input and the monitor of the default output, each straight into its own file, so recording continues even if the app crashes. A transcriber process reads the files back, cuts them into sentences with Silero VAD and transcribes each sentence once with NVIDIA Parakeet TDT 0.6B v3 (int8, run by sherpa-onnx). The window is plain HTML in a pywebview (WebKitGTK) window. Questions and summaries run `claude -p` with tools, plugins and MCP servers switched off, so Claude only sees the text meetnote sends. [docs/DESIGN.md](docs/DESIGN.md) has the details and [FINDINGS.md](FINDINGS.md) the measurements.

## Repository

| Path | Contents |
|---|---|
| `meetnote/` | the Python package (`src/meetnote`) and its tests |
| `experiments/` | feasibility and end-to-end measurements |
| `docs/` | design and testing notes |
| `ROADMAP.md`, `STATUS.md`, `FINDINGS.md` | goal, current state, results |

Run the tests with `cd meetnote && ../.venv/bin/python -m pytest`.

## Licence

MIT. The speech model, NVIDIA Parakeet TDT 0.6B v3, is licensed CC-BY-4.0.
