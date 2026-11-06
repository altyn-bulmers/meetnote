# Status

Updated 2026-11-06.

## Done

- **M1 (proposed): recorder, live transcript, notes, crash recovery.** Recording of both sides, the live offline transcript, notes and crash recovery work end to end on this laptop, tested with virtual devices.
- **M2 (proposed): Claude assistant.** The Ask panel, the pop-out Ask window, the summary at Stop and Retry when offline all work with the real Claude CLI on the Max subscription.
- **Tests:** 51 automated tests pass (`cd meetnote && ../.venv/bin/python -m pytest`).
- **Experiments:** 01 (feasibility) and 02 (end to end) are complete; results are in FINDINGS.md.

## Next

- Altynbek's checks with real devices: headphones, Bluetooth, a real call, Wi-Fi off (docs/TESTING.md, part 3).
- M3 (proposed): Opus compression of the audio, search across meetings, Markdown and Notion export, Kazakh if needed, a settings page.

## Open questions for Altynbek

1. Which languages are the meetings in? Parakeet covers 25 European languages; Kazakh would need a second model.
2. Does "in notion" mean sending notes to a Notion page, or only Notion-like editing?
3. ROADMAP.md has no milestones yet. M1 to M4 are proposed in the session report.

## Known limits

- When the CPU throttles or is busy, a line can take up to 5 s to appear instead of 1–2 s.
- Device following was tested with virtual devices only.
- Audio is stored as raw PCM, about 230 MB per hour for both sides.
