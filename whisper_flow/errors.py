"""Custom exception hierarchy + friendly error rendering.

These wrap the low-level failure modes (missing binary, missing model,
bad audio, backend runtime failure) so the CLI layer can print a clean,
actionable message and exit with a sensible code instead of a stack trace.
"""

from __future__ import annotations


class WhisperFlowError(Exception):
    """Base class for all whisper-flow errors."""

    exit_code: int = 1


class ConfigError(WhisperFlowError):
    """Invalid configuration (bad flag combination, missing required value)."""

    exit_code = 2


class BinaryNotFoundError(WhisperFlowError):
    """A required external binary (whisper-cli, llama-server, ffmpeg, arecord) is missing."""

    exit_code = 3

    def __init__(self, name: str, hint: str = ""):
        self.name = name
        self.hint = hint
        msg = f"required binary not found on PATH: {name!r}"
        if hint:
            msg += f"\n  hint: {hint}"
        super().__init__(msg)


class ModelNotFoundError(WhisperFlowError):
    """A model file path was configured but does not exist / is not a file."""

    exit_code = 4

    def __init__(self, path: str, kind: str = "model"):
        self.path = path
        self.kind = kind
        super().__init__(
            f"{kind} file not found: {path!r}\n"
            f"  set the path via config or CLI flag, and run scripts/download_models.sh"
        )


class AudioError(WhisperFlowError):
    """Audio capture / conversion / chunking failed."""

    exit_code = 5


class TranscriptionError(WhisperFlowError):
    """The transcription backend returned an error or produced no usable output."""

    exit_code = 6


class LLMError(WhisperFlowError):
    """The LLM backend returned an error or produced no usable output."""

    exit_code = 7


class CancelledError(WhisperFlowError):
    """The user cancelled the operation (Cancel button or Ctrl+C).

    Distinct from other errors so the CLI can exit cleanly (130 = 128 + SIGINT,
    matching shell convention for interrupted commands) and the notifier can
    show a "Canceled" state instead of "Error".
    """

    exit_code = 130


def render_error(err: Exception) -> str:
    """Return a one-line + hint string for terminal display."""
    if isinstance(err, WhisperFlowError):
        return f"[{type(err).__name__}] {err}"
    return f"[unexpected] {err}"
