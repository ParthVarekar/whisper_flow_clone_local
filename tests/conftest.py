"""Shared fixtures: headless env, fake binaries, temp WAV files."""
from __future__ import annotations

import os
import tempfile
import wave

import pytest

# Force headless so the notifier factory always returns NullNotifier in tests.
os.environ.pop("DISPLAY", None)


@pytest.fixture
def tmp_wav_1s() -> str:
    """A real 1-second 16kHz mono WAV file (silence)."""
    p = tempfile.mktemp(suffix=".wav")
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    yield p
    os.path.exists(p) and os.unlink(p)


@pytest.fixture
def tmp_wav_corrupted() -> str:
    """A file with .wav extension but garbage contents."""
    p = tempfile.mktemp(suffix=".wav")
    with open(p, "wb") as f:
        f.write(b"not a wav file at all but long enough xxxxxxxxxxxxxxxxxxxx")
    yield p
    os.path.exists(p) and os.unlink(p)


@pytest.fixture
def tmp_wav_too_small() -> str:
    """A .wav file too small to be valid."""
    p = tempfile.mktemp(suffix=".wav")
    with open(p, "wb") as f:
        f.write(b"RIFF\x10\x00\x00\x00")
    yield p
    os.path.exists(p) and os.unlink(p)


@pytest.fixture
def tmp_models_dir(monkeypatch) -> str:
    """A temp dir populated with fake model files for discovery tests."""
    monkeypatch.setattr("whisper_flow.models.default_model_dirs", lambda: [])
    d = tempfile.mkdtemp(prefix="wf_models_")
    for name in ("ggml-base.en.bin", "ggml-small.bin", "ggml-silero-v6.2.0.bin",
                 "gemma-3-1b-it-Q4_K_M.gguf", "model-Q9_X.gguf", "random.bin"):
        with open(os.path.join(d, name), "wb") as f:
            f.write(b"\x00" * 1024)
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)

