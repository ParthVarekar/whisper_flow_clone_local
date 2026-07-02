"""Tests for the error hierarchy + exit codes."""
from __future__ import annotations

from whisper_flow.errors import (
    AudioError,
    BinaryNotFoundError,
    CancelledError,
    ConfigError,
    LLMError,
    ModelNotFoundError,
    TranscriptionError,
    WhisperFlowError,
    render_error,
)


class TestExitCodes:
    def test_distinct_exit_codes(self):
        assert ConfigError().exit_code == 2
        assert BinaryNotFoundError("x").exit_code == 3
        assert ModelNotFoundError("/x").exit_code == 4
        assert AudioError().exit_code == 5
        assert TranscriptionError().exit_code == 6
        assert LLMError().exit_code == 7
        assert CancelledError().exit_code == 130  # 128 + SIGINT

    def test_all_subclass_base(self):
        for exc_cls in (ConfigError, BinaryNotFoundError, ModelNotFoundError,
                        AudioError, TranscriptionError, LLMError, CancelledError):
            assert issubclass(exc_cls, WhisperFlowError)


class TestBinaryNotFoundError:
    def test_includes_hint(self):
        e = BinaryNotFoundError("whisper-cli", "build with scripts/build.sh")
        assert "whisper-cli" in str(e)
        assert "build with scripts/build.sh" in str(e)

    def test_no_hint(self):
        e = BinaryNotFoundError("ffmpeg")
        assert "ffmpeg" in str(e)


class TestModelNotFoundError:
    def test_message(self):
        e = ModelNotFoundError("/path/to/model.bin", "Whisper ggml model")
        assert "/path/to/model.bin" in str(e)
        assert "Whisper ggml model" in str(e)


class TestRenderError:
    def test_renders_whisperflow_error(self):
        e = AudioError("bad audio")
        out = render_error(e)
        assert "[AudioError]" in out
        assert "bad audio" in out

    def test_renders_unexpected(self):
        out = render_error(ValueError("oops"))
        assert "[unexpected]" in out
        assert "oops" in out
