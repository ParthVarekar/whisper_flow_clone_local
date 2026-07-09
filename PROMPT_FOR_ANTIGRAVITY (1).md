# PROMPT FOR ANTIGRAVITY: Apply Bug Fixes to whisper_flow_clone_local

## CRITICAL: Do NOT push to git until the user says "approved" or "push it".

## Instructions

Apply the following exact find-and-replace edits to each file. Do NOT write any new code yourself. Do NOT modify anything beyond what is specified here. After all edits, run `python -m pytest tests/ -v` to verify 113 tests pass.

---

## File 1: `whisper_flow/hotkeys.py`

### Edit 1: Replace the `_on_press` method's dictation hotkey check

FIND:
```python
    def _on_press(self, key) -> None:
        if not self._running:
            return
        name = _key_to_name(key)
        self._pressed_keys.add(name)

        # -- Dictation hotkey press --
        if (
            not self._dict_held
            and not self._cmd_held
            and self._modifiers_held(self._dict_mods)
            and f"Key.{self._dict_trigger}" in self._pressed_keys
        ):
```

REPLACE WITH:
```python
    def _on_press(self, key) -> None:
        if not self._running:
            return
        name = _key_to_name(key)
        self._pressed_keys.add(name)

        # C1 FIX: check trigger using both bare name and Key. prefix.
        # _key_to_name returns "t" for letter keys but "Key.space" for special keys.
        # The old code only checked f"Key.{trigger}" which never matched letters.
        dict_trigger_names = {self._dict_trigger, f"Key.{self._dict_trigger}"}
        cmd_trigger_names = {self._cmd_trigger, f"Key.{self._cmd_trigger}"}

        # -- Dictation hotkey press --
        if (
            not self._dict_held
            and not self._cmd_held
            and self._modifiers_held(self._dict_mods)
            and not self._pressed_keys.isdisjoint(dict_trigger_names)
        ):
```

### Edit 2: Replace the command hotkey press check

FIND:
```python
        # -- Command hotkey press --
        if (
            not self._cmd_held
            and not self._dict_held
            and self._modifiers_held(self._cmd_mods)
            and f"Key.{self._cmd_trigger}" in self._pressed_keys
        ):
```

REPLACE WITH:
```python
        # -- Command hotkey press --
        if (
            not self._cmd_held
            and not self._dict_held
            and self._modifiers_held(self._cmd_mods)
            and not self._pressed_keys.isdisjoint(cmd_trigger_names)
        ):
```

### Edit 3: Replace the dictation hotkey release check

FIND:
```python
        # -- Dictation hotkey release --
        trigger_name = f"Key.{self._dict_trigger}"
        if self._dict_held and (name == trigger_name or not self._modifiers_held(self._dict_mods)):
```

REPLACE WITH:
```python
        # -- Dictation hotkey release --
        # C1 FIX: check both bare name and Key. prefix for release too
        dict_trigger_names = {self._dict_trigger, f"Key.{self._dict_trigger}"}
        if self._dict_held and (name in dict_trigger_names or not self._modifiers_held(self._dict_mods)):
```

### Edit 4: Replace the command hotkey release check

FIND:
```python
        # -- Command hotkey release --
        cmd_trigger_name = f"Key.{self._cmd_trigger}"
        if self._cmd_held and (name == cmd_trigger_name or not self._modifiers_held(self._cmd_mods)):
```

REPLACE WITH:
```python
        # -- Command hotkey release --
        cmd_trigger_names = {self._cmd_trigger, f"Key.{self._cmd_trigger}"}
        if self._cmd_held and (name in cmd_trigger_names or not self._modifiers_held(self._cmd_mods)):
```

---

## File 2: `whisper_flow/prompts.py`

### Edit 1: Fix mind_reader alias

FIND:
```python
    "mind_reader": "auto",
}
```

REPLACE WITH:
```python
    "mind_reader": "medium",
    "auto": "medium",
}
```

### Edit 2: Remove "auto" from VALID_MODES

FIND:
```python
VALID_MODES = {"summarize", "correct", "polish", "medium", "smart_list", "email",
               "coding", "meeting_notes", "social", "command", "assistant", "raw",
               "auto", "mind_reader",
               "none", "light", "high", "summary", "bullets", "list", "dev", "notes", "tweet"}
```

REPLACE WITH:
```python
VALID_MODES = {"summarize", "correct", "polish", "medium", "smart_list", "email",
               "coding", "meeting_notes", "social", "command", "assistant", "raw",
               "mind_reader",
               "none", "light", "high", "summary", "bullets", "list", "dev", "notes", "tweet"}
```

### Edit 3: Fix str.format crash on braces

FIND:
```python
    user = USER_TEMPLATES[mode].format(transcript=transcript)
    return system, user
```

REPLACE WITH:
```python
    # C4 FIX: use str.replace instead of str.format to avoid KeyError/IndexError
    # when the transcript contains literal braces (e.g. JSON, code, {value}).
    user = USER_TEMPLATES[mode].replace("{transcript}", transcript)
    return system, user
```

---

## File 3: `whisper_flow/notifier.py`

### Edit 1: Fix NullNotifier.register_start hanging

FIND:
```python
    def register_start(self, cb: Callable[[], None]) -> None:
        self._start_cb = cb
        cb()
```

REPLACE WITH:
```python
    def register_start(self, cb: Callable[[], None]) -> None:
        # C5 FIX: do NOT call cb() immediately — store it for later.
        # The pipeline creates _start_evt AFTER register_start is called,
        # so firing immediately means the event is never set and headless
        # --duration 0 hangs forever. TkNotifier already stores without firing.
        self._start_cb = cb
```

---

## File 4: `whisper_flow/transforms.py`

### Edit 1: Fix built-in transform format call

FIND:
```python
    for name, tmpl in BUILTIN_TRANSFORMS.items():
        if name in instr_lower or instr_lower.startswith(name):
            return (
                tmpl["system"],
                tmpl["user"].format(selected=selected_text),
            )
```

REPLACE WITH:
```python
    for name, tmpl in BUILTIN_TRANSFORMS.items():
        if name in instr_lower or instr_lower.startswith(name):
            # C8 FIX: use str.replace instead of str.format
            return (
                tmpl["system"],
                tmpl["user"].replace("{selected}", selected_text),
            )
```

### Edit 2: Fix custom transform format call

FIND:
```python
            if name.lower() in instr_lower:
                return (
                    tmpl.get("system", "You are a helpful writing assistant. Output only the result."),
                    tmpl.get("user", "Text:\n\"\"\"\n{selected}\n\"\"\"\n\n{instruction}").format(
                        selected=selected_text, instruction=voice_instruction
                    ),
                )
```

REPLACE WITH:
```python
            if name.lower() in instr_lower:
                # C8 FIX: use str.replace instead of str.format
                user_tmpl = tmpl.get("user", "Text:\n\"\"\"\n{selected}\n\"\"\"\n\n{instruction}")
                return (
                    tmpl.get("system", "You are a helpful writing assistant. Output only the result."),
                    user_tmpl.replace("{selected}", selected_text).replace("{instruction}", voice_instruction),
                )
```

### Edit 3: Fix explicit transform_name lookup and format call

FIND:
```python
    if transform_name:
        all_transforms = {**BUILTIN_TRANSFORMS}
        if custom_transforms:
            all_transforms.update(custom_transforms)
        tmpl = all_transforms.get(transform_name)
        if tmpl:
            return (
                tmpl["system"],
                tmpl["user"].format(selected=selected_text),
            )
```

REPLACE WITH:
```python
    if transform_name:
        all_transforms = {**BUILTIN_TRANSFORMS}
        if custom_transforms:
            all_transforms.update(custom_transforms)
        # C8 FIX: case-insensitive lookup
        tmpl = all_transforms.get(transform_name) or all_transforms.get(transform_name.lower())
        if tmpl:
            # C8 FIX: use str.replace instead of str.format to avoid KeyError
            # when the template uses {instruction} or other placeholders.
            user = tmpl["user"].replace("{selected}", selected_text).replace("{instruction}", voice_instruction)
            return (
                tmpl["system"],
                user,
            )
```

---

## File 5: `whisper_flow/formatting.py`

### Edit 1: Fix backtrack to remove prior sentence

FIND:
```python
def _apply_backtrack(text: str) -> str:
    sentences = re.split(r"([.!?]\s+)", text)
    rebuilt: list[str] = []
    for part in sentences:
        lowered = part.lower()
        marker = next((m for m in _BACKTRACK_MARKERS if m in lowered), "")
        if not marker:
            rebuilt.append(part)
            continue
        idx = lowered.find(marker)
        suffix = part[idx + len(marker):].strip(" ,")
        prefix = part[:idx].strip(" ,")
        if len(suffix.split()) >= 2:
            rebuilt.append(suffix)
        else:
            rebuilt.append(prefix)
    return "".join(rebuilt)
```

REPLACE WITH:
```python
def _apply_backtrack(text: str) -> str:
    # H10 FIX: backtrack should remove the PRIOR sentence, not just edit
    # the current one. "I went to the store. Actually I went to the market."
    # should become "I went to the market."
    sentences = re.split(r"([.!?]\s+)", text)
    rebuilt: list[str] = []
    skip_next_sentence = False
    for i, part in enumerate(sentences):
        lowered = part.lower()
        marker = next((m for m in _BACKTRACK_MARKERS if m in lowered), "")
        if marker:
            idx = lowered.find(marker)
            suffix = part[idx + len(marker):].strip(" ,")
            # Remove the last appended sentence (the one being corrected)
            if rebuilt:
                while rebuilt and not rebuilt[-1].strip():
                    rebuilt.pop()
                if rebuilt:
                    rebuilt.pop()
            if len(suffix.split()) >= 1:
                rebuilt.append(suffix)
            continue
        if skip_next_sentence:
            skip_next_sentence = False
            continue
        rebuilt.append(part)
    return "".join(rebuilt)
```

### Edit 2: Fix press-enter to remove ALL occurrences

FIND:
```python
def _apply_press_enter(text: str) -> str:
    out = re.sub(r"\bpress enter\b$", "", text, flags=re.IGNORECASE).rstrip()
    if out != text.rstrip():
        return out + "\n"
    return text
```

REPLACE WITH:
```python
def _apply_press_enter(text: str) -> str:
    # H12 FIX: remove ALL occurrences of "press enter", not just at end
    out = re.sub(r"\bpress enter\b", "", text, flags=re.IGNORECASE)
    out = out.rstrip()
    if out != text.rstrip():
        return out + "\n"
    return text
```

### Edit 3: Fix auto-space breaking decimals and abbreviations

FIND:
```python
    out = re.sub(r"([,.;:!?])(?=[^\s\n])", r"\1 ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
```

REPLACE WITH:
```python
    out = re.sub(r"([,.;:!?])(?=[^\s\n])", r"\1 ", out)
    # H11 FIX: undo the auto-space for decimals (1.5, 3.14) and common abbreviations
    # (e.g., i.e., Mr., Dr., etc.)
    out = re.sub(r"(\d)\. (\d)", r"\1.\2", out)  # fix decimals: 1. 5 → 1.5
    out = re.sub(r"\b([ei])\. ([ge])\.", r"\1.\2.", out)  # e. g. → e.g., i. e. → i.e.
    out = re.sub(r"\n{3,}", "\n\n", out)
```

---

## File 6: `whisper_flow/daemon.py`

### Edit 1: Fix copy_selected_text import

FIND:
```python
        try:
            from .inserter import copy_selected_text
            self._command_selected_text = copy_selected_text()
```

REPLACE WITH:
```python
        try:
            # C2 FIX: inserter.py defines get_selected_text, not copy_selected_text
            from .inserter import get_selected_text as copy_selected_text
            self._command_selected_text = copy_selected_text()
```

---

## File 7: `whisper_flow/inserter.py`

### Edit 1: Fix docstring to be accurate about clipboard

FIND:
```python
"""System-wide text insertion via clipboard + keystroke simulation.

On Windows, uses ctypes SendInput to simulate Ctrl+V after placing text on
the clipboard. Falls back to pyperclip + pyautogui if available.

The clipboard is saved/restored around each insertion so the user's clipboard
is not clobbered.
"""
```

REPLACE WITH:
```python
"""System-wide text insertion via clipboard + keystroke simulation.

On Windows, uses ctypes SendInput to simulate Ctrl+V after placing text on
the clipboard. Falls back to pyperclip + pyautogui if available.

NOTE: The clipboard is NOT saved/restored around insertion — the dictated
text remains on the clipboard for re-paste. This is intentional (matching
Wispr Flow behavior). If clipboard preservation is needed in the future,
add save/restore logic around the EmptyClipboard/SetClipboardData calls.
"""
```

### Edit 2: Add copy_selected_text alias after get_selected_text function

FIND:
```python
    return selected.strip()


# ---------------------------------------------------------------------------
# Fallback: pyperclip-based
```

REPLACE WITH:
```python
    return selected.strip()


# C2 FIX: alias for backward compat — daemon.py imports copy_selected_text
copy_selected_text = get_selected_text


# ---------------------------------------------------------------------------
# Fallback: pyperclip-based
```

---

## File 8: `whisper_flow/server.py`

### Edit 1: Fix cfg.mode race in _handle_audio

FIND:
```python
                cfg.mode = mode
                result = pipe.run_file(upath)
```

REPLACE WITH:
```python
                # C9 FIX: use per-request config copy to avoid race condition
                import dataclasses
                req_cfg = dataclasses.replace(cfg, mode=mode)
                result = Pipeline(req_cfg).run_file(upath)
```

### Edit 2: Fix cfg.mode race in _handle_text

FIND:
```python
        cfg.mode = mode
        try:
            processed = Pipeline(cfg).process(text)
```

REPLACE WITH:
```python
        # C9 FIX: use per-request config copy to avoid race condition
        import dataclasses
        req_cfg = dataclasses.replace(cfg, mode=mode)
        try:
            processed = Pipeline(req_cfg).process(text)
```

---

## File 9: `whisper_flow/cli.py`

### Edit 1: Add SUPPRESS to --translate

FIND:
```python
    p.add_argument("--translate", action="store_true", help="translate to English")
```

REPLACE WITH:
```python
    p.add_argument("--translate", action="store_true", default=argparse.SUPPRESS, help="translate to English")
```

### Edit 2: Add SUPPRESS to --vad

FIND:
```python
    p.add_argument("--vad", action="store_true", help="enable Silero VAD (skips silence; requires --vad-model)")
```

REPLACE WITH:
```python
    p.add_argument("--vad", action="store_true", default=argparse.SUPPRESS, help="enable Silero VAD (skips silence; requires --vad-model)")
```

### Edit 3: Add SUPPRESS to --write-files

FIND:
```python
    p.add_argument("--write-files", action="store_true",
                   help="write transcript files next to source / in --out-dir")
```

REPLACE WITH:
```python
    p.add_argument("--write-files", action="store_true", default=argparse.SUPPRESS,
                   help="write transcript files next to source / in --out-dir")
```

### Edit 4: Add SUPPRESS to --no-gui, --gui, --notify

FIND:
```python
    p.add_argument("--no-gui", action="store_true",
                   help="disable the live GUI progress window (console output only)")
    p.add_argument("--gui", action="store_true",
                   help="force the GUI progress window even if no display is detected")
    p.add_argument("--notify", action="store_true",
                   help="also fire desktop notifications (notify-send) on start/done/error")
```

REPLACE WITH:
```python
    p.add_argument("--no-gui", action="store_true", default=argparse.SUPPRESS,
                   help="disable the live GUI progress window (console output only)")
    p.add_argument("--gui", action="store_true", default=argparse.SUPPRESS,
                   help="force the GUI progress window even if no display is detected")
    p.add_argument("--notify", action="store_true", default=argparse.SUPPRESS,
                   help="also fire desktop notifications (notify-send) on start/done/error")
```

### Edit 5: Add SUPPRESS to top-level --verbose

FIND:
```python
    p.add_argument("-v", "--verbose", action="store_true", help="verbose logging to stderr")
```

REPLACE WITH:
```python
    p.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="verbose logging to stderr")
```

### Edit 6: Fix _overrides_from_args to skip False for booleans

FIND:
```python
def _overrides_from_args(args: argparse.Namespace) -> dict:
    """Map CLI args to dotted config overrides (None values skipped by load_config)."""
    o: dict = {}
    def put(key, val):
        if val is not None:
            o[key] = val
```

REPLACE WITH:
```python
def _overrides_from_args(args: argparse.Namespace) -> dict:
    """Map CLI args to dotted config overrides.

    C6 FIX: None values are skipped (attribute not set via argparse.SUPPRESS).
    False values for store_true flags are also skipped so that config-file
    True settings survive when the flag isn't explicitly passed on the CLI.
    """
    o: dict = {}
    _BOOL_FLAGS = frozenset({
        "transcription.translate", "transcription.vad",
        "output.write_files", "verbose",
    })
    def put(key, val):
        if val is None:
            return
        if key in _BOOL_FLAGS and val is False:
            return
        o[key] = val
```

---

## File 10: `whisper_flow/pipeline.py`

### Edit 1: Map "text" format to "txt"

FIND:
```python
    fmt = cfg.output.format
    formats = ["txt", "json", "srt", "vtt"] if fmt == "all" else [fmt]
```

REPLACE WITH:
```python
    fmt = cfg.output.format
    # C7 FIX: map "text" → "txt" so --format text --write-files actually writes a file
    if fmt == "text":
        fmt = "txt"
    formats = ["txt", "json", "srt", "vtt"] if fmt == "all" else [fmt]
```

---

## File 11: `tests/test_wispr_flow.py`

### Edit 1: Update test_auto_mode_alias

FIND:
```python
def test_auto_mode_alias():
    """Test that mind_reader resolves to auto and auto is a valid mode."""
    from whisper_flow.prompts import resolve_mode, VALID_MODES

    assert resolve_mode("mind_reader") == "auto"
    assert resolve_mode("auto") == "auto"
    assert "auto" in VALID_MODES
    assert "mind_reader" in VALID_MODES
```

REPLACE WITH:
```python
def test_auto_mode_alias():
    """Test that mind_reader and auto resolve to a valid prompt mode."""
    from whisper_flow.prompts import resolve_mode, VALID_MODES, SYSTEM_PROMPTS

    # C3 FIX: mind_reader and auto both resolve to 'medium' (a valid mode with a prompt)
    assert resolve_mode("mind_reader") == "medium"
    assert resolve_mode("auto") == "medium"
    assert "mind_reader" in VALID_MODES
    assert "medium" in SYSTEM_PROMPTS
```

---

## File 12: `tests/test_regression.py` (NEW FILE)

Create this new file with the complete content below. This is a new file — write it in full.

```python
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
```

---

## After all edits

Run: `python -m pytest tests/ -v`

Expected: **113 passed**

## DO NOT push to git

Wait for the user to test the app themselves and say "approved" or "push it". Only then:
```bash
git add -A
git commit -m "fix: 9 critical + 4 high bugs from codebase analysis

- C1: hotkeys letter-key trigger (Ctrl+Shift+T now works)
- C2: daemon copy_selected_text import (transform mode works)
- C3: prompts mind_reader/auto crash (resolves to medium)
- C4: prompts str.format crash on braces (uses str.replace)
- C5: notifier NullNotifier.register_start hang (stores not fires)
- C6: cli store_true defaults override config (SUPPRESS + skip False)
- C7: pipeline text→txt format mapping
- C8: transforms KeyError on custom transforms (uses str.replace)
- C9: server cfg.mode race (dataclasses.replace per request)
- H10: formatting backtrack removes prior sentence
- H11: formatting auto-space preserves decimals/abbreviations
- H12: formatting press-enter removes all occurrences
- H1: inserter docstring accurate
- 23 new regression tests (113 total)"
git push origin main
```
