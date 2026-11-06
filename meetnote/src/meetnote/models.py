"""Download the speech-recognition and voice-activity models once.

Every file comes from a pinned URL (a fixed Hugging Face commit or a
sherpa-onnx release asset) and is checked against its SHA-256. Downloads
stream to disk in 1 MB chunks and resume with HTTP Range requests, so a
large model never sits in memory and an interrupted download continues
where it stopped.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import paths
from .durable import fsync_dir

_HF = (
    "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/"
    "resolve/2bda32ec70b097a55adaa07d9a7173915b43cc78/"
)
_GH = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
_CHUNK = 1 << 20
_USER_AGENT = "meetnote/0.1"

Progress = Callable[[str, int, int], None]


@dataclass(frozen=True)
class RemoteFile:
    name: str  # file name inside the model folder
    url: str
    size: int  # bytes
    sha256: str


PARAKEET = "parakeet-tdt-0.6b-v3-int8"
VAD = "silero-vad"
TEST_WAVS = "test-wavs"

CATALOG: dict[str, tuple[RemoteFile, ...]] = {
    PARAKEET: (
        RemoteFile("encoder.int8.onnx", _HF + "encoder.int8.onnx", 652_184_281,
                   "acfc2b4456377e15d04f0243af540b7fe7c992f8d898d751cf134c3a55fd2247"),
        RemoteFile("decoder.int8.onnx", _HF + "decoder.int8.onnx", 11_845_275,
                   "179e50c43d1a9de79c8a24149a2f9bac6eb5981823f2a2ed88d655b24248db4e"),
        RemoteFile("joiner.int8.onnx", _HF + "joiner.int8.onnx", 6_355_277,
                   "3164c13fc2821009440d20fcb5fdc78bff28b4db2f8d0f0b329101719c0948b3"),
        RemoteFile("tokens.txt", _HF + "tokens.txt", 93_939,
                   "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d"),
    ),
    VAD: (
        RemoteFile("silero_vad.onnx", _GH + "silero_vad.onnx", 643_854,
                   "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"),
    ),
    TEST_WAVS: (
        RemoteFile("en.wav", _HF + "test_wavs/en.wav", 184_608,
                   "148b936b43ce7c546a866e64da059f0458aee2d65e617f16e9d94f06e8d99ed6"),
        RemoteFile("fr.wav", _HF + "test_wavs/fr.wav", 219_180,
                   "b59be4349b92d344fb903677165eaf4694025d1ab119c608726ecbcb3164b528"),
        RemoteFile("de.wav", _HF + "test_wavs/de.wav", 121_388,
                   "36d3c4845b9808a1656a2a2e92d884590e2db94389e6fe559643291ae0cd3710"),
        RemoteFile("es.wav", _HF + "test_wavs/es.wav", 235_052,
                   "49fd2cfa4b62db7068143c582b35de9d31ec2733495ece3611105131d21de06c"),
    ),
}

# What the app needs to run.
DEFAULT = (PARAKEET, VAD)


def model_dir(name: str) -> Path:
    return paths.models_dir() / name


def is_installed(name: str) -> bool:
    folder = model_dir(name)
    return all(_complete(folder / f.name, f) for f in CATALOG[name])


def download(name: str, progress: Progress | None = None) -> Path:
    """Fetch every missing file of model `name` and return its folder."""
    folder = model_dir(name)
    folder.mkdir(parents=True, exist_ok=True)
    for remote in CATALOG[name]:
        _fetch(remote, folder / remote.name, progress)
    return folder


def _complete(path: Path, remote: RemoteFile) -> bool:
    # A file gets its final name only after its SHA-256 matched, so the size suffices here.
    return path.is_file() and path.stat().st_size == remote.size


def _fetch(remote: RemoteFile, dest: Path, progress: Progress | None) -> None:
    if _complete(dest, remote):
        return
    part = dest.with_name(dest.name + ".part")
    for _attempt in range(2):
        have = part.stat().st_size if part.exists() else 0
        if have > remote.size:
            part.unlink()
            have = 0
        if have == remote.size or _stream(remote, part, have, progress):
            break
        part.unlink(missing_ok=True)  # the server ignored the range: start from zero
    size = part.stat().st_size if part.exists() else 0
    if size != remote.size:
        raise OSError(f"{remote.name}: downloaded {size} of {remote.size} bytes")
    digest = _sha256(part)
    if digest != remote.sha256:
        part.unlink()
        raise OSError(f"{remote.name}: SHA-256 is {digest}, expected {remote.sha256}")
    os.replace(part, dest)
    fsync_dir(dest.parent)


def _stream(remote: RemoteFile, part: Path, have: int, progress: Progress | None) -> bool:
    """Append the rest of the file to `part`; False if the server ignored the Range header."""
    request = urllib.request.Request(remote.url, headers={"User-Agent": _USER_AGENT})
    if have:
        request.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(request, timeout=60) as response:
        if have and response.status != 206:
            return False
        with open(part, "ab" if have else "wb") as out:
            while chunk := response.read(_CHUNK):
                have += len(chunk)
                if have > remote.size:
                    raise OSError(f"{remote.name}: server sent more than {remote.size} bytes")
                out.write(chunk)
                if progress:
                    progress(remote.name, have, remote.size)
            out.flush()
            os.fsync(out.fileno())
    return True


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(_CHUNK):
            h.update(block)
    return h.hexdigest()
