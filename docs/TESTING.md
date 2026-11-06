# Testing

## 1. Automated tests

    cd meetnote && ../.venv/bin/python -m pytest

The tests use a fake parec that writes a tone, a model-free VAD and recognizer, and a fake transcriber process. They cover crash adoption of parec, durability and anchors, resume without lost or duplicate lines, VAD feeding, the echo filter, JSONL repair, the controller and the window's bridge. One test transcribes a real recording and is skipped if the models are missing (`meetnote models parakeet-tdt-0.6b-v3-int8 silero-vad test-wavs`).

## 2. End to end with virtual devices

`experiments/02_end_to_end/` drives the command-line app and the real window with two inaudible virtual devices. It measures delay, CPU and RAM, and it kills the app mid-meeting. See its README.

## 3. With real devices (by hand)

1. **Both sides.** Start a recording, play a video with speech and talk over it with headphones on. Both meters move, and Me and Others lines appear a second or two after each sentence.
2. **Device switch.** During the recording, plug in or unplug headphones, or switch the output in the system settings. The device names in the top bar change and the transcript continues. Repeat with a Bluetooth headset.
3. **Speakers.** Without headphones, the mic hears the speakers; the window shows "Show echo lines" and hides the repeated Me lines.
4. **Crash.** During a recording, run `pkill -9 -f "bin/meetnote"` and keep talking for a few seconds. Start meetnote again: a banner says the recording continued, and the transcript has the sentences spoken while the window was gone.
5. **Power loss.** During a recording, hold the power button. After booting, start meetnote: the meeting is marked interrupted, everything up to about a second before the power cut is in the transcript, and Resume recording continues the same meeting.
6. **Mute.** Press Mic on to mute; speech on the mic is no longer transcribed until you unmute.
