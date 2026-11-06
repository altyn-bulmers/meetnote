"""Stand-in for `python -m meetnote.transcriber` in tests: same loop, no models."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fake_engine import FakeEngine  # noqa: E402
from meetnote.transcriber import Transcriber, acquire_lock  # noqa: E402

if __name__ == "__main__":
    folder = Path(sys.argv[-1])
    if acquire_lock(folder) is not None:
        Transcriber(folder, FakeEngine(), idle_exit=5, poll=0.05).run()
