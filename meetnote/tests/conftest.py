import sys
from pathlib import Path

import pytest

from meetnote import paths

HERE = Path(__file__).parent
REAL_MODELS = paths.models_dir()  # resolved before any test changes MEETNOTE_HOME


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test gets its own empty meetnote home; downloaded models stay usable."""
    root = tmp_path / "home"
    monkeypatch.setenv("MEETNOTE_HOME", str(root))
    monkeypatch.setenv("MEETNOTE_MODELS_DIR", str(REAL_MODELS))
    return root


@pytest.fixture
def fake_parec(tmp_path, monkeypatch):
    """Replace parec with a script that writes a tone in real time."""
    script = tmp_path / "fake_parec"
    script.write_text(f"#!{sys.executable}\n" + (HERE / "fake_parec.py").read_text())
    script.chmod(0o755)
    monkeypatch.setenv("MEETNOTE_PAREC", str(script))
    return script
