"""Tests for config loading: defaults, env vars, JSON, TOML, CLI overrides."""
from __future__ import annotations

import json

import pytest

from whisper_flow.config import (
    TranscriptionConfig,
    load_config,
)
from whisper_flow.errors import ConfigError


class TestDefaults:
    def test_defaults(self):
        c = load_config()
        assert c.transcription.language == "auto"
        assert c.transcription.flash_attention is True
        assert c.transcription.vad is False
        assert c.llm.mode == "server"
        assert c.llm.port == 8080
        assert c.audio.sample_rate == 16000
        assert c.audio.channels == 1
        assert c.audio.mic_backend == "auto"
        assert c.audio.stream is False
        assert c.mode == "summarize"

    def test_vad_defaults(self):
        c = load_config()
        assert c.transcription.vad_threshold == 0.5
        assert c.transcription.vad_min_silence_ms == 0
        assert c.transcription.vad_model == ""


class TestEnvVars:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("WHISPER_FLOW_TRANSCRIPTION__LANGUAGE", "fr")
        monkeypatch.setenv("WHISPER_FLOW_LLM__PORT", "9090")
        c = load_config()
        assert c.transcription.language == "fr"
        assert c.llm.port == 9090

    def test_env_bool_coercion(self, monkeypatch):
        monkeypatch.setenv("WHISPER_FLOW_OUTPUT__WRITE_FILES", "true")
        c = load_config()
        assert c.output.write_files is True

    def test_env_int_coercion(self, monkeypatch):
        monkeypatch.setenv("WHISPER_FLOW_LLM__MAX_TOKENS", "256")
        c = load_config()
        assert c.llm.max_tokens == 256


class TestJSONConfig:
    def test_json_file(self, tmp_path):
        cfg_path = tmp_path / "c.json"
        cfg_path.write_text(json.dumps({
            "transcription": {"model": "/tmp/m.bin", "language": "en"},
            "llm": {"model": "/tmp/l.gguf", "port": 7777},
            "mode": "command",
        }))
        c = load_config(str(cfg_path))
        assert c.transcription.model == "/tmp/m.bin"
        assert c.transcription.language == "en"
        assert c.llm.port == 7777
        assert c.mode == "command"

    def test_invalid_json(self, tmp_path):
        cfg_path = tmp_path / "bad.json"
        cfg_path.write_text("{not valid json")
        with pytest.raises(ConfigError):
            load_config(str(cfg_path))


class TestTOMLConfig:
    def test_toml_file(self, tmp_path):
        # Python 3.11+ has tomllib; skip on older Pythons without tomli
        try:
            import tomllib  # noqa: F401
        except ImportError:
            try:
                import tomli  # noqa: F401
            except ImportError:
                pytest.skip("neither tomllib nor tomli available")
        cfg_path = tmp_path / "c.toml"
        # In TOML, top-level keys must come BEFORE any [section] header
        # (otherwise they're absorbed into the preceding section).
        cfg_path.write_text("""
mode = "command"

[transcription]
model = "/tmp/m.bin"
language = "en"

[llm]
model = "/tmp/l.gguf"
port = 7777
""")
        c = load_config(str(cfg_path))
        assert c.transcription.model == "/tmp/m.bin"
        assert c.transcription.language == "en"
        assert c.llm.port == 7777
        assert c.mode == "command"


class TestCLIOverrides:
    def test_override_wins_over_defaults(self):
        c = load_config(overrides={"transcription.language": "de", "llm.port": 7777, "mode": "command"})
        assert c.transcription.language == "de"
        assert c.llm.port == 7777
        assert c.mode == "command"

    def test_none_overrides_ignored(self):
        c = load_config(overrides={"transcription.language": None, "mode": None})
        assert c.transcription.language == "auto"
        assert c.mode == "summarize"

    def test_precedence_file_then_overrides(self, tmp_path):
        cfg_path = tmp_path / "c.json"
        cfg_path.write_text(json.dumps({"transcription": {"language": "fr"}, "mode": "summarize"}))
        c = load_config(str(cfg_path), {"transcription.language": "de", "mode": "command"})
        assert c.transcription.language == "de"  # override beats file
        assert c.mode == "command"


class TestVADConfig:
    def test_vad_fields_present(self):
        c = TranscriptionConfig(vad=True, vad_model="/tmp/silero.bin",
                                vad_threshold=0.7, vad_min_silence_ms=500)
        assert c.vad is True
        assert c.vad_model == "/tmp/silero.bin"
        assert c.vad_threshold == 0.7
        assert c.vad_min_silence_ms == 500
