"""Stand-in for parec in tests: writes a 440 Hz tone as s16le 16 kHz mono, in real time.

Set FAKE_PAREC_EXIT_AFTER=<seconds> to make it exit early, as parec does when
PipeWire fails. Its arguments are ignored.
"""

import math
import os
import signal
import sys
import time

RATE = 16000
CHUNK = RATE // 10  # 100 ms
TONE = b"".join(int(8000 * math.sin(2 * math.pi * 440 * i / RATE)).to_bytes(2, "little", signed=True)
                for i in range(RATE))  # exactly one second; 440 Hz repeats every second

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
exit_after = float(os.environ.get("FAKE_PAREC_EXIT_AFTER") or 0)
out = sys.stdout.buffer
start = time.monotonic()
written = 0
while True:
    pos = (written % RATE) * 2
    out.write(TONE[pos:pos + 2 * CHUNK])
    out.flush()
    written += CHUNK
    if exit_after and time.monotonic() - start >= exit_after:
        sys.exit(1)
    delay = start + written / RATE - time.monotonic()
    if delay > 0:
        time.sleep(delay)
