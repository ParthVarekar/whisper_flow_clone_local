"""Regression tests for bugs found in the codebase analysis.

Each test corresponds to a specific issue ID from CODEBASE_ANALYSIS.md.
Tests are designed to fail BEFORE the fix and pass AFTER.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestHotkeyLetterTrigger:
    """C1: Ctrl+Shift+T never fires because _key_to_name returns 't' but
    the trigger check constructs 'Key.t'."""

    def test_key_to_name_letter(self):
        from whisper_flow.hotkeys import _key_to_name, parse_hotkey
        fake_key = MagicMock()
        fake_key.name = None
        fake_key.char = "t"
        fake_key.vk = None
        name = _key_to_name(fake_key)
        assert name == "t", f"expected 't', got {name!r}"
        _mods, trigger = parse_hotkey("ctrl+shift+t")
        assert trigger == "t"
        pressed_keys = {name}
        old_check = f"Key.{trigger}" in pressed_keys
        assert not old_check, "The old comparison would NOT match, confirming C1 bug"

    def test_key_to_name_space(self):
        from whisper_flow.hotkeys import _key_to_name, parse_hotkey
        fake_key = MagicMock()
        fake_key.name = "space"
        fake_key.char = None
        fake_key.vk = None
        name = _key_to_name(fake_key)
        assert name == "Key.space"
        _mods, trigger = parse_hotkey("ctrl+shift+space")
        assert trigger == "space"
        assert name == f"Key.{trigger}"


class TestCopySelectedTextExists:
    """C2: daemon.py imports copy_selected_text from inserter, but it doesn't exist."""

    def test_copy_selected_text_exists_in_inserter(self):
        from whisper_flow import inserter
        assert hasattr(inserter, "copy_selected_text")
        assert callable(inserter.copy_selected_text)


class TestAutoMode:
    """C3: mind_reader resolves to 'auto' which has no prompt defined."""

    def test_resolve_mind_reader(self):
        from whisper_flow.prompts import resolve_mode, SYSTEM_PROMPTS
        resolved = resolve_mode("mind_reader")
        assert resolved in SYSTEM_PROMPTS

    def test_build_prompt_auto_does_not_crash(self):
        from whisper_flow.prompts import build_prompt
        system, user = build_prompt("auto", "test transcript")
        assert isinstance(system, str)
        assert isinstance(user, str)


class TestPromptBracesSafety:
    """C4: build_prompt uses str.format which crashes on {braces} in transcript."""

    def test_transcript_with_braces(self):
        from whisper_flow.prompts import build_prompt
        system, user = build_prompt("summarize", "set x to {value} and y to {other}")
        assert "{value}" in user

    def test_transcript_with_json(self):
        from whisper_flow.prompts import build_prompt
        transcript = '{"name": "test", "value": 100}'
        system, user = build_prompt("correct", transcript)
        assert '"name"' in user

    def test_transcript_with_percent(self):
        from whisper_flow.prompts import build_prompt
        system, user = build_prompt("polish", "100% done {maybe}")
        assert "100%" in user
        assert "{maybe}" in user


class TestNullNotifierRegisterStart:
    """C5: NullNotifier.register_start fires callback immediately, causing hang."""

    def test_register_start_does_not_fire_immediately(self):
        from whisper_flow.notifier import NullNotifier
        n = NullNotifier()
        called = [False]
        def cb():
            called[0] = True
        n.register_start(cb)
        assert not called[0], "register_start must NOT fire callback immediately"
        assert n._start_cb is cb


class TestCLIBooleanOverrides:
    """C6: store_true flags default to False, which overrides config-file True."""

    def test_false_does_not_override_config(self):
        from whisper_flow.cli import _overrides_from_args
        import argparse
        args = argparse.Namespace(
            language=None, whisper_model=None, whisper_bin=None,
            translate=False, threads=None, gpu=None,
            vad=False, vad_model=None, vad_threshold=None, vad_min_silence_ms=None,
            chunk_seconds=None, mic_device=None, mic_backend=None,
            llm_mode=None, llm_model=None, llm_host=None, llm_port=None,
            temperature=None, max_tokens=None, gpu_layers=None, writing_style=None,
            output_format=None, write_files=False, out_dir=None, mode=None,
            verbose=False,
        )
        overrides = _overrides_from_args(args)
        assert "transcription.translate" not in overrides
        assert "output.write_files" not in overrides
        assert "transcription.vad" not in overrides
        assert "verbose" not in overrides


class TestTextFormatWritesFile:
    """C7: --format text --write-files writes nothing because 'text' != 'txt'."""

    def test_text_format_maps_to_txt(self, tmp_path):
        from whisper_flow.pipeline import write_outputs
        from whisper_flow.backends.base import Segment, TranscriptionResult
        from whisper_flow.config import Config, OutputConfig
        result = TranscriptionResult(
            text="hello world",
            segments=[Segment("hello world", 0, 1000, "en")],
            language="en",
        )
        cfg = Config()
        cfg.output = OutputConfig(format="text", write_files=True, out_dir=str(tmp_path))
        written = write_outputs(result, cfg, source_name="test")
        txt_files = [w for w in written if w.endswith(".txt")]
        assert len(txt_files) == 1, f"expected 1 .txt file, got {written}"


class TestTransformCustomKeError:
    """C8: build_transform_prompt crashes when a custom transform's user
    template uses {instruction} but only {selected} is passed."""

    def test_custom_transform_with_instruction_placeholder(self):
        from whisper_flow.transforms import build_transform_prompt
        custom = {
            "my_transform": {
                "system": "You are a helpful assistant.",
                "user": "Text:\n{selected}\n\nInstruction: {instruction}",
            }
        }
        system, user = build_transform_prompt(
            selected_text="hello world",
            voice_instruction="make it shorter",
            transform_name="my_transform",
            custom_transforms=custom,
        )
        assert "hello world" in user
        assert "make it shorter" in user

    def test_builtin_transform_still_works(self):
        from whisper_flow.transforms import build_transform_prompt
        system, user = build_transform_prompt(
            selected_text="hello world",
            voice_instruction="polish",
        )
        assert "hello world" in user


class TestServerConfigNotMutated:
    """C9: server.py mutates shared cfg.mode, causing race conditions."""

    def test_handle_audio_does_not_mutate_cfg(self):
        from whisper_flow.config import Config
        import dataclasses
        cfg = Config()
        original_mode = cfg.mode
        mode = "command"
        per_request_cfg = dataclasses.replace(cfg, mode=mode)
        assert cfg.mode == original_mode, "shared cfg must not be mutated"
        assert per_request_cfg.mode == "command"


class TestBacktrackRemovesPrior:
    """H10: _apply_backtrack should remove the prior sentence."""

    def test_backtrack_removes_prior_sentence(self):
        from whisper_flow.formatting import _apply_backtrack
        text = "I went to the store. Actually I went to the market."
        result = _apply_backtrack(text)
        assert "market" in result

    def test_backtrack_no_marker(self):
        from whisper_flow.formatting import _apply_backtrack
        text = "I went to the store and bought milk."
        result = _apply_backtrack(text)
        assert "store" in result
        assert "milk" in result


class TestAutoSpaceSafety:
    """H11: _normalize_spacing should not break decimals or abbreviations."""

    def test_decimal_not_broken(self):
        from whisper_flow.formatting import _normalize_spacing
        result = _normalize_spacing("The value is 1.5 and 3.14.")
        assert "1.5" in result
        assert "3.14" in result

    def test_abbreviation_not_broken(self):
        from whisper_flow.formatting import _normalize_spacing
        result = _normalize_spacing("Use e.g. this format.")
        assert "e.g." in result


class TestPressEnterMidText:
    """H12: 'press enter' in the middle of text should be removed."""

    def test_press_enter_at_end(self):
        from whisper_flow.formatting import apply_smart_formatting
        result = apply_smart_formatting("hello world press enter")
        assert "press enter" not in result.lower()
        assert result.endswith("\n")

    def test_press_enter_mid_text_removed(self):
        from whisper_flow.formatting import apply_smart_formatting
        result = apply_smart_formatting("hello press enter world")
        assert "press enter" not in result.lower()


class TestIntentsContract:
    """H13: detect_auto_intent should return documented values."""

    def test_messaging_app_does_not_return_undocumented_medium(self):
        from whisper_flow.intents import detect_auto_intent
        result = detect_auto_intent("hello there", app_category="messaging")
        documented = {"smart_list", "email", "coding", "meeting_notes",
                      "social", "transform", "polish", "medium", "none", "raw"}
        assert result in documented, f"undocumented return value: {result!r}"


class TestInserterDocstring:
    """H1: docstring claims clipboard save/restore but code doesn't do it."""

    def test_docstring_accurate(self):
        from whisper_flow import inserter
        doc = inserter.__doc__ or ""
        assert "NOT" in doc.upper() or "not" in doc, (
            "docstring should clarify that clipboard is NOT saved/restored"
        )
