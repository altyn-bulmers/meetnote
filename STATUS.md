# Status

Updated 2026-11-06.

## Done

- Recording of both sides, live offline transcript, notes and crash recovery work end to end on this laptop, tested with virtual devices. This is milestone M1 as proposed for ROADMAP.md.
- 40 automated tests pass (`cd meetnote && ../.venv/bin/python -m pytest`).
- Experiment 01 (feasibility) and experiment 02 (end to end) are complete; results are in FINDINGS.md.

## Next

- M2, the Claude assistant: an Ask panel and side window that answer questions with the recent transcript, a summary at Stop, and Retry when offline.
- Altynbek's checks with real devices: headphones, Bluetooth and a real call (docs/TESTING.md, part 3).

## Open questions for Altynbek

1. Which languages are the meetings in? Parakeet covers 25 European languages; Kazakh would need a second model.
2. Does "in notion" mean sending notes to a Notion page, or only Notion-like editing?
3. ROADMAP.md has no milestones yet. M1 to M4 are proposed in the session report.

## Known limits

- When the CPU throttles or is busy, a line can take up to 5 s to appear instead of 1–2 s.
- Device following was tested with virtual devices only.
- Audio is stored as raw PCM, about 230 MB per hour for both sides; compression is planned.
