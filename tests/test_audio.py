"""Tests for audio capture utilities (WAV validation, ffmpeg arg construction).

NOTE: actual mic capture and ffmpeg subprocess execution are NOT tested here
(they require hardware + binaries). These tests cover the pure-Python helpers.
"""
from __future__ import annotations

import sys

import pytest

from whisper_flow.audio import _auto_mic_backend, _ffmpeg_mic_input_args, validate_wav
from whisper_flow.config import AudioConfig
from whisper_flow.errors import AudioError


class TestValidateWav:
    def test_valid_wav(self, tmp_wav_1s):
        dur = validate_wav(tmp_wav_1s)
        assert abs(dur - 1.0) < 0.01

    def test_too_small(self, tmp_wav_too_small):
        with pytest.raises(AudioError, match="too small"):
            validate_wav(tmp_wav_too_small)

    def test_corrupted(self, tmp_wav_corrupted):
        with pytest.raises(AudioError):
            validate_wav(tmp_wav_corrupted)

    def test_nonexistent(self):
        with pytest.raises(AudioError, match="not found"):
            validate_wav("/nonexistent/path.wav")


class TestFfmpegMicArgs:
    def test_linux_pulse_when_pactl_present(self, monkeypatch):
        # simulate pactl presence
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/pactl" if name == "pactl" else None)
        monkeypatch.setattr(sys, "platform", "linux")
        cfg = AudioConfig()
        args = _ffmpeg_mic_input_args(cfg)
        assert args == ["-f", "pulse", "-i", "default"]

    def test_linux_alsa_when_no_pactl(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setattr(sys, "platform", "linux")
        cfg = AudioConfig()
        args = _ffmpeg_mic_input_args(cfg)
        assert args == ["-f", "alsa", "-i", "default"]

    def test_macos_avfoundation(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        cfg = AudioConfig()
        args = _ffmpeg_mic_input_args(cfg)
        assert args == ["-f", "avfoundation", "-i", ":default"]

    def test_windows_dshow_requires_device_name(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        cfg = AudioConfig()  # mic_device = "default"
        with pytest.raises(AudioError, match="explicit device name"):
            _ffmpeg_mic_input_args(cfg)

    def test_windows_dshow_with_device(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        cfg = AudioConfig(mic_device="Microphone (USB)")
        args = _ffmpeg_mic_input_args(cfg)
        assert args == ["-f", "dshow", "-i", "audio=Microphone (USB)"]


class TestAutoBackend:
    def test_no_sounddevice_linux_arecord(self, monkeypatch):
        # simulate: no sounddevice, arecord present, on linux
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/arecord" if name == "arecord" else None)
        monkeypatch.setitem(__import__("sys").modules, "sounddevice", None)
        # force ImportError
        import builtins
        real_import = builtins.__import__
        def fake_import(name, *a, **k):
            if name == "sounddevice":
                raise ImportError("no sounddevice")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert _auto_mic_backend() == "arecord"

    def test_falls_back_to_ffmpeg(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        import builtins
        real_import = builtins.__import__
        def fake_import(name, *a, **k):
            if name == "sounddevice":
                raise ImportError("no sounddevice")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert _auto_mic_backend() == "ffmpeg"
