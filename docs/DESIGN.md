# Design

## Processes

```
parec  default input            ──> audio/me-<time>.pcm       (parec writes the file itself)
parec  monitor of default output ──> audio/others-<time>.pcm
          │ app: fsync each file every second, timing anchors, level meters, device names
          v
transcriber process: reads the files back ─> Silero VAD ─> Parakeet ─> transcript.jsonl
          │
window (pywebview): meetings │ notes │ live transcript
```

- `meetnote` is one process: the window, the recorder thread and a poll loop. The page calls Python through pywebview's JS bridge, and no web server or port is opened.
- Each parec runs in its own session under `timeout <max_hours>`, with its stdout pointing at its segment file. If the app dies, recording continues. When the app starts again it takes the parecs over: it finds them by scanning `/proc/*/fd/1` for the segment files, so stored PIDs, which a reboot can reuse, are never trusted. A parec left behind stops after `max_hours`.
- The transcriber is a separate process with a lock file, so only one writes a session. It runs while audio remains and exits afterwards, which frees the model's 1 GB of RAM.
- The macOS port only needs a capture helper that writes the same PCM to stdout; everything else is portable.

## Files

One folder per meeting in `~/.local/share/meetnote/sessions/<id>/`. Every file has exactly one writer.

| File | Writer | Contents |
|---|---|---|
| `session.json` | app | id, title, status (`recording`, `stopped`, `interrupted`), segments, device changes |
| `audio/<src>-<time>.pcm` | parec | raw s16le, 16 kHz, mono; `<src>` is `me` or `others` |
| `audio/<src>-<time>.anchors.jsonl` | app | `{"bytes", "wall", "mono"}` once a second |
| `transcript.jsonl` | transcriber | `line`, `checkpoint` and `segment_done` records |
| `transcriber.status.json` | transcriber | position in each segment, for the lag display |
| `notes.md` | app | the notes |
| `qa.jsonl` | app | `question`, `answer` and `failed` records; the last record of an id is its state |
| `summary.md` | app | Claude's summary; `session.json` holds `"summary": "pending"` until it is written |

A `line` record is `{"type": "line", "src", "seg", "s0", "s1", "w0", "w1", "t0", "text"}`. `s0` and `s1` are sample offsets in the segment, `w0` and `w1` are Unix times and `t0` is an ISO 8601 time.

## Crash safety

| Data | How it is written | Most lost on a crash or power loss |
|---|---|---|
| Audio | parec appends; the app fsyncs every second | about 1 s. If the app died first, parec keeps recording and the kernel writes the data within about 30 s. |
| Transcript | one fsync per line | nothing; missing lines are redone from the audio |
| Notes | 1 s after typing stops: temp file, fsync, rename | about 1 s of typing |

New files are made durable with an fsync of their folder. A reader ignores a last line without its newline, and a writer cuts such a line before appending. A `kill -9` test cannot prove the fsync works, so the power-loss guarantee rests on fsync semantics.

## Timing

Raw PCM carries no timestamps, and samples stop flowing while a device switches or the laptop sleeps. The app therefore appends an anchor (file size, wall-clock time) every second. A sample's time is counted back from the first anchor at or after it, so audio that arrives after a stall gets the time it really arrived. A stretch where wall time runs ahead of the samples by more than 0.3 s is a gap.

## Devices

The input stream has no device and the output stream records `@DEFAULT_MONITOR@`. WirePlumber's `follow` policy moves both when the default device changes, so headphones and Bluetooth need no restart. The streams never set the Communication media role, which would force a Bluetooth headset into its low-quality call profile. Device names come from `pactl` every 2 s.

## Transcription

- The VAD is fed exactly 512 samples per call, one VAD per segment. An utterance ends after 0.5 s of silence; speech still running after 25 s is cut, because the VAD's own 20 s limit is soft.
- Each utterance is transcribed once, on one thread pinned to the performance cores at normal priority. Efficiency cores decode up to 5x slower (experiment 02).
- Resume points are the end of the last line, or a checkpoint written every 30 s of silence, 0.5 s before the read position so that no speech start is cut. After a crash the transcriber restarts from the latest point and skips utterances that end before it.
- Echo: with laptop speakers the microphone also hears the call. A "me" line that overlaps an "others" line in time with text similarity of 0.6 or more is flagged as echo. It stays in the file but is hidden in the window and left out of Claude's context.

## Claude

Questions and summaries run the Claude Code CLI, which uses the subscription it is logged in with:

    claude -p --safe-mode --tools "" --no-session-persistence --model <model> --system-prompt <prompt> \
      --output-format stream-json --verbose --include-partial-messages

- The prompt goes on stdin, and the CLI runs in an empty folder. Safe mode drops CLAUDE.md, plugins, hooks and MCP servers, and `--tools ""` removes all tools, so text from the meeting cannot make Claude act.
- `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` are removed from the CLI's environment, because with either set `claude -p` would bill the API instead of the plan.
- Only `text_delta` events are shown while they stream; the `result` event is final. A lost connection shows up as an `api_retry` event without an HTTP status after about 1 s; meetnote then stops the CLI, which would otherwise retry for minutes, and offers Retry.
- **Questions** go to `ask_model` (Sonnet) with `--effort low`. The prompt holds the last `context_minutes` of transcript before the newest line, without echo lines, the last 8,000 characters of the notes and the question.
- **Summaries** go to `summary_model` (Opus) once the transcript is complete. The session is marked `pending` first, so a summary that fails, for example offline, is retried at the next start.
- `meetnote doctor` checks that the CLI is logged in with a Claude subscription. Claude Code's documentation says `--bare`, which ignores subscription logins, may become the default for `-p`; doctor would then fail.

## Window

Plain HTML, CSS and JS, inlined into one page. The Ask window is the same page in an Ask-only view, kept on top, and it follows the meeting open in the main window. Transcript text is inserted as text, Markdown is rendered with raw HTML escaped, and links open in the browser only if they are http(s). The bridge accepts session ids that must match `YYYYMMDD-HHMMSS[-slug]` and never file paths. Events go to the page from one thread, so the recorder never waits on the window. The page avoids CSS animations: an endless one made WebKit repaint 60 times a second.
