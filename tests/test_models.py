"""Tests for model discovery and classification."""
from __future__ import annotations

import os

from whisper_flow.models import (
    _classify,
    default_model_dirs,
    list_models,
    render_table,
)


class TestClassification:
    def test_whisper_known(self, tmp_path):
        p = str(tmp_path / "ggml-base.en.bin")
        open(p, "wb").write(b"\x00" * 1024)
        m = _classify(p)
        assert m.kind == "whisper"
        assert m.detail == "base.en"
        assert m.warning == ""

    def test_whisper_unknown_name(self, tmp_path):
        p = str(tmp_path / "ggml-unknown-model.bin")
        open(p, "wb").write(b"\x00" * 1024)
        m = _classify(p)
        assert m.kind == "whisper"
        assert "unrecognized" in m.warning

    def test_vad(self, tmp_path):
        p = str(tmp_path / "ggml-silero-v6.2.0.bin")
        open(p, "wb").write(b"\x00" * 1024)
        m = _classify(p)
        assert m.kind == "vad"
        assert "silero" in m.detail.lower()

    def test_gguf_valid_quant(self, tmp_path):
        p = str(tmp_path / "gemma-3-1b-it-Q4_K_M.gguf")
        open(p, "wb").write(b"\x00" * 1024)
        m = _classify(p)
        assert m.kind == "gguf"
        assert m.detail == "Q4_K_M"
        assert m.warning == ""

    def test_gguf_unknown_quant(self, tmp_path):
        p = str(tmp_path / "model-Q9_X.gguf")
        open(p, "wb").write(b"\x00" * 1024)
        m = _classify(p)
        assert m.kind == "gguf"
        assert "Q9_X" in m.warning

    def test_gguf_no_quant(self, tmp_path):
        p = str(tmp_path / "model.gguf")
        open(p, "wb").write(b"\x00" * 1024)
        m = _classify(p)
        assert m.kind == "gguf"
        assert m.detail == ""
        assert m.warning == ""

    def test_unknown_bin(self, tmp_path):
        p = str(tmp_path / "random.bin")
        open(p, "wb").write(b"\x00" * 1024)
        m = _classify(p)
        assert m.kind == "unknown"
        assert "doesn't match" in m.warning


class TestScanDirs:
    def test_scan_finds_all_kinds(self, tmp_models_dir):
        by = list_models([tmp_models_dir])
        assert len(by["whisper"]) == 2
        assert len(by["vad"]) == 1
        assert len(by["gguf"]) == 2
        assert len(by["unknown"]) == 1

    def test_scan_dedupes_realpath(self, tmp_models_dir):
        # scanning the same dir twice should not duplicate
        by = list_models([tmp_models_dir, tmp_models_dir])
        assert len(by["whisper"]) == 2

    def test_scan_ignores_dotfiles(self, tmp_models_dir):
        open(os.path.join(tmp_models_dir, ".hidden.bin"), "wb").write(b"\x00")
        by = list_models([tmp_models_dir])
        assert all(".hidden" not in m.name for ms in by.values() for m in ms)

    def test_render_table_empty(self):
        by = {"whisper": [], "gguf": [], "vad": [], "unknown": []}
        out = render_table(by)
        assert "No models found" in out

    def test_render_table_populated(self, tmp_models_dir):
        by = list_models([tmp_models_dir])
        out = render_table(by)
        assert "Whisper" in out
        assert "GGUF" in out
        assert "VAD" in out
        assert "base.en" in out


class TestDefaultDirs:
    def test_default_dirs_returns_existing_only(self):
        dirs = default_model_dirs()
        for d in dirs:
            assert os.path.isdir(d), f"{d} should exist"

    def test_env_var_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WHISPER_FLOW_MODELS_DIR", str(tmp_path))
        dirs = default_model_dirs()
        assert str(tmp_path) in dirs


class TestDownloadModel:
    def test_download_model_existing_skip(self, tmp_path):
        from whisper_flow.models import download_model
        p = tmp_path / "ggml-small.en.bin"
        p.write_bytes(b"\x00" * 2000)
        out = download_model("small.en", target_dir=str(tmp_path))
        assert out == str(p)

    def test_download_model_invalid_name(self, tmp_path):
        import pytest
        from whisper_flow.models import download_model
        with pytest.raises(ValueError, match="Unknown model name"):
            download_model("invalid_model_123", target_dir=str(tmp_path))

