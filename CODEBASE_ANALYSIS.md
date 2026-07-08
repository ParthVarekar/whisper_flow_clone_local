# whisper-flow Codebase Analysis

**Reviewer:** Senior Codebase Manager
**Date:** 2026-07-08
**Scope:** All 25 Python modules in `whisper_flow/`, all 11 test files, config files, scripts
**Method:** Line-by-line review of every file + `py_compile` syntax check + `pytest` run

## Executive Summary

| Metric | Result |
|--------|--------|
| Python files | 25 |
| Syntax check (`py_compile`) | ✅ All pass |
| Test suite (`pytest`) | ✅ 90/90 pass (0.22s) |
| CRITICAL issues found | 9 |
| HIGH issues found | 15 |
| MEDIUM issues found | 25+ |
| LOW issues found | 20+ |
| Files with zero issues | 2 (`__init__.py`, `errors.py`) |

**Bottom line:** The codebase compiles and tests pass, but there are **9 critical bugs** that will cause crashes, hangs, or data corruption in real usage. The tests pass because they don't exercise the broken code paths (daemon, hotkeys, inserter, server — all platform-specific or integration-level).

---

## CRITICAL Issues (9) — Will crash, hang, or corrupt data

### C1. `hotkeys.py` — Command hotkey Ctrl+Shift+T never fires
**File:** `whisper_flow/hotkeys.py` lines 190, 212, 233
**Problem:** The trigger check constructs `f"Key.{self._dict_trigger}"` (e.g. `"Key.t"`), but `_key_to_name()` returns bare characters (`"t"`) or VK codes (`"vk_84"`) for letter keys — never `"Key.t"`. The comparison always fails.
**Impact:** The command/transform mode hotkey (Ctrl+Shift+T) is completely non-functional. Users cannot access the transform feature.
**Fix:** Change the trigger check to compare against the raw trigger string directly, not `f"Key.{trigger}"`.

### C2. `daemon.py` + `inserter.py` — `copy_selected_text` doesn't exist
**File:** `whisper_flow/daemon.py` line 433, `whisper_flow/inserter.py`
**Problem:** `daemon.py` does `from .inserter import copy_selected_text`, but `inserter.py` only defines `get_selected_text` — there is no `copy_selected_text`. The `ImportError` is silently caught, so `_command_selected_text` is always `""`.
**Impact:** Command/transform mode always thinks no text is selected. The entire transform feature is broken.
**Fix:** Change the import to `from .inserter import get_selected_text as copy_selected_text`, or rename the function.

### C3. `prompts.py` — `mind_reader` mode crashes with `ValueError`
**File:** `whisper_flow/prompts.py` lines 100, 105
**Problem:** `ALIASES` maps `"mind_reader"` → `"auto"`, and `VALID_MODES` includes `"auto"`, but `"auto"` is NOT a key in `SYSTEM_PROMPTS` or `USER_TEMPLATES`. `build_prompt("auto", ...)` raises `ValueError("unknown mode: 'auto'")`.
**Impact:** Selecting `mind_reader` mode always crashes.
**Fix:** Add an `"auto"` entry to `SYSTEM_PROMPTS`/`USER_TEMPLATES`, or remove `mind_reader`/`auto` from `ALIASES`/`VALID_MODES`.

### C4. `prompts.py` — `str.format()` crashes on transcripts containing braces
**File:** `whisper_flow/prompts.py` line 148
**Problem:** `USER_TEMPLATES[mode].format(transcript=transcript)` treats `{...}` in the transcript as replacement fields. If the transcript contains `{value}`, `100%`, or JSON-like text, it raises `KeyError` or `IndexError`.
**Impact:** Any dictation containing literal braces crashes the LLM stage.
**Fix:** Use `USER_TEMPLATES[mode].replace("{transcript}", transcript)` instead of `.format()`.

### C5. `pipeline.py` + `notifier.py` — Headless `--duration 0` hangs forever
**File:** `whisper_flow/pipeline.py` lines 175-188, `whisper_flow/notifier.py` lines 92-94
**Problem:** `NullNotifier.register_start()` immediately calls the callback. But at `Pipeline.__init__` time, `self._start_evt` is `None`, so the callback does nothing. Later, `_wait_for_manual_start` creates a NEW event and blocks on it — but nothing will ever set it.
**Impact:** `whisper-flow mic --duration 0` in headless mode hangs forever with no way out except SIGKILL.
**Fix:** `NullNotifier.register_start` should NOT call `cb()` immediately (match `TkNotifier` behavior), or `_wait_for_manual_start` should skip the wait for non-GUI notifiers.

### C6. `cli.py` — `store_true` defaults silently override config file
**File:** `whisper_flow/cli.py` lines 76, 84, 88, 132, 136
**Problem:** Flags like `--write-files`, `--no-gui`, `--notify`, `--translate`, `--vad`, `--verbose` use `action="store_true"` (default `False`). `_overrides_from_args` only skips `None`, not `False`. So every CLI invocation pushes `False` into the override dict, silently overriding any `true` value from the config file.
**Impact:** Config-file settings for booleans are dead — CLI always wins with `False`.
**Fix:** Use `default=argparse.SUPPRESS` for all `store_true` flags, or skip `False` values in `put()`.

### C7. `cli.py` — `--format text --write-files` writes nothing
**File:** `whisper_flow/cli.py` line 73, `whisper_flow/pipeline.py` `write_outputs()`
**Problem:** CLI choices include `"text"` (default), but `write_outputs` only handles `"txt"`, `"json"`, `"srt"`, `"vtt"`. The string `"text"` never maps to the `.txt` extension.
**Impact:** `--format text --write-files` silently writes no file.
**Fix:** Map `"text" → "txt"` in `write_outputs`, or change CLI choices from `"text"` to `"txt"`.

### C8. `transforms.py` — `KeyError: 'instruction'` on custom transforms
**File:** `whisper_flow/transforms.py` lines 117-118
**Problem:** `tmpl["user"].format(selected=selected_text)` only passes `selected`. But the default custom transform template at L104 uses `{instruction}`. When an explicit `transform_name` matches a custom transform, `KeyError: 'instruction'` is raised.
**Impact:** Any custom transform invoked by name crashes.
**Fix:** Pass both `selected=selected_text` and `instruction=instruction` to `.format()`, or use `.replace()`.

### C9. `server.py` — Shared `cfg.mode` mutation races across threads
**File:** `whisper_flow/server.py` lines 168, 190
**Problem:** `cfg.mode = mode` mutates the shared `Config` object. `ThreadingHTTPServer` handles requests in separate threads. Two simultaneous `/process` requests with different modes race — one client gets the wrong mode.
**Impact:** Wrong cleanup mode delivered to clients under concurrent load.
**Fix:** Use `dataclasses.replace(cfg, mode=mode)` to create a per-request copy.

---

## HIGH Issues (15) — Significant correctness, data, or security problems

### H1. `inserter.py` — Clipboard clobbered on every insertion (contradicts docstring)
**Lines 6-7 vs 44:** Docstring promises clipboard save/restore; code does not. After every insertion, the user's previous clipboard content is destroyed.

### H2. `inserter.py` — `get_selected_text` saves old clipboard but never restores it
**Lines 159-195:** Saves `old` clipboard content to detect "nothing selected", but never puts it back. If the user had important content (e.g., a password), it's overwritten with whatever was selected.

### H3. `daemon.py` — `_clean_llm_output` corrupts raw transcripts
**Line 373:** When `mode == "raw"`, `processed` is the raw transcript. `_clean_llm_output` then strips `"Transcript:"` / `"Output:"` prefixes. If the user actually dictated "Output: the report is ready", the prefix is removed.

### H4. `daemon.py` — Command mode output not cleaned, no `target_hwnd`
**Lines 544, 573:** Dictation mode passes `target_hwnd` and calls `_clean_llm_output`. Command mode does neither. LLM echo artifacts are inserted raw, and text may go to the wrong window.

### H5. `daemon.py` — Command mode omits `writing_style`, `context_words`, `app_context`
**Lines 560-564:** The command mode "no selection" fallback omits all enrichment parameters that dictation mode passes, producing lower-quality results.

### H6. `qwen3_asr.py` — Subprocess deadlock (stdout/stderr read sequentially)
**Lines 159-176:** Stdout is read in a blocking loop first, then stderr. If the process writes >64KB to stderr while stdout is being consumed, both block forever.

### H7. `qwen3_asr.py` — `language` parameter silently ignored
**Lines 90, 121-124:** `transcribe()` accepts a `language` arg but `_build_cmd()` uses `c.language` (config) instead. Callers cannot override the language per-call.

### H8. `qwen3_asr.py` — Cancel flag reset outside lock (race condition)
**Lines 126-128:** `_cancel_requested = False` is set outside the lock. A late `cancel()` from a previous session can set it `True` before the lock is acquired, causing premature abort.

### H9. `llama_cpp.py` — Prompt-echo fallback is unreachable
**Lines 179-183:** The fallback only triggers when `stdout.strip()` is empty, but if the build echoes the prompt, `stdout` is non-empty. The prompt-echo stripping never runs.

### H10. `formatting.py` — Backtrack markers don't remove the prior sentence
**Line 47:** `_apply_backtrack` only edits the current sentence. "I went to the store. Actually I went to the market." → keeps both sentences. Feature is non-functional.

### H11. `formatting.py` — Auto-space breaks decimals and abbreviations
**Line 103:** `re.sub(r"([,.;:!?])(?=[^\s\n])", r"\1 ", out)` turns `"1.5"` → `"1. 5"`, `"e.g."` → `"e. g. "`.

### H12. `formatting.py` — "press enter" only works at end-of-text
**Line 91:** `re.sub(r"\bpress enter\b$", "", ...)` only strips at the very end. Mid-text occurrences leak through as literal words.

### H13. `intents.py` — Returns `"medium"` violating documented contract
**Line 73:** Docstring promises return values `"smart_list"`, `"email"`, `"coding"`, etc. `"medium"` is not in that list. Downstream code expecting the documented enum misbehaves.

### H14. `server.py` — Multipart parser corrupts uploaded files
**Lines 45, 57:** Boundary parsing fails when Content-Type has extra params after `boundary=`. `strip(b"\r\n")` strips trailing CR/LF from file content, corrupting WAV files.

### H15. `server.py` — No upload/body size limit (DoS)
**Lines 89-90, 113:** Entire body is read into memory with no size limit. A malicious client can exhaust memory or disk.

---

## MEDIUM Issues (25+) — Edge case bugs, inconsistencies, missing handling

| # | File | Issue |
|---|------|-------|
| M1 | `cli.py` | `process --mode raw` silently overridden to `summarize` |
| M2 | `cli.py` | `mic -f file.wav` gives confusing "both" error (`mic=True` is a default) |
| M3 | `cli.py` | `bench` can never benchmark raw STT (mode rewritten to `summarize`) |
| M4 | `cli.py` | Benchmark `audio_dur = 0.0` hardcoded — RTF metric is meaningless |
| M5 | `config.py` | `save_config` TOML doesn't escape quotes/backslashes in paths |
| M6 | `config.py` | `save_config` silently drops `snippets`, `dictionary`, `app_styles`, `custom_transforms` |
| M7 | `config.py` | `_coerce` silently keeps old value on parse failure (no error to user) |
| M8 | `config.py` | Env var `__` separator undocumented; `WHISPER_FLOW_LLM_GPU_LAYERS` maps to wrong field |
| M9 | `pipeline.py` | `_recording_stop_cb` is never assigned — dead code branch |
| M10 | `pipeline.py` | `build_prompt` called without `context_words` or `app_context` — dictionary/config ignored |
| M11 | `pipeline.py` | Race condition: mode can change between formatting and LLM processing in threaded GUI |
| M12 | `audio.py` | `normalize_file` temp WAV never cleaned up — leaks per transcription |
| M13 | `audio.py` | `_split` temp directory never cleaned up — leaks per chunked run |
| M14 | `audio.py` | arecord with `duration <= 0` hangs forever, can't be stopped by GUI |
| M15 | `audio.py` | ffmpeg not terminated on Ctrl+C — zombie process holds the mic |
| M16 | `audio.py` | `_capture_sounddevice` open-ended mode: `NameError` on KeyboardInterrupt (`data` undefined) |
| M17 | `notifier.py` | `TkNotifier` doesn't accept `verbose` parameter — silently dropped in GUI mode |
| M18 | `daemon.py` | Mic failure doesn't reset tray state — stuck "recording" icon |
| M19 | `daemon.py` | Per-app style is sticky — never restored after dictation ends |
| M20 | `daemon.py` | Hands-free flag desyncs between daemon and hotkeys manager |
| M21 | `app_detect.py` | ctypes `restype` defaults to `c_int` — HWND/HANDLE truncated on 64-bit Windows |
| M22 | `history.py` | JSONL append is not atomic — concurrent writes can corrupt |
| M23 | `history.py` | `search_history` loads max 10,000 records — older entries silently unsearchable |
| M24 | `snippets.py` | Chained substitution: `{"hi":"hello","hello":"world"}` turns `"hi"` → `"world"` |
| M25 | `snippets.py` | `re.sub` replacement string: backreferences in expansions cause `re.error` |
| M26 | `vocabulary.py` | Read-modify-write is not atomic — concurrent processes lose updates |
| M27 | `vocabulary.py` | `"w"` mode truncates before write — crash mid-write leaves empty file |
| M28 | `overlay.py` | Non-daemon `threading.Timer` delays shutdown by up to 3s |
| M29 | `qwen3_asr.py` | Cancel returns empty result instead of raising `CancelledError` (inconsistent with whisper_cpp) |
| M30 | `qwen3_asr.py` | `wait()` with no timeout — hung process blocks forever |

---

## LOW Issues (20+) — Code smells, dead code, minor inconsistencies

| File | Issue |
|------|-------|
| `__main__.py` | Broad `except ImportError` catches any import failure, not just "no package context" |
| `cli.py` | `if __name__ == "__main__"` block is dead (never executed via `python -m`) |
| `cli.py` | `arecord` optional detection compares binary name string, not `shutil.which` |
| `config.py` | `Config()` constructed fresh inside `_build_flat_index` loop — should construct once |
| `config.py` | `AudioConfig.stream` field defined but never read — dead config |
| `pipeline.py` | `_ts_to_ms` function defined but never called — dead code |
| `pipeline.py` | `import threading` after `from typing` — PEP 8 violation |
| `audio.py` | `stream_mic_chunks` defined but never called — superseded by `LiveMicCapture` |
| `audio.py` | `LiveMicCapture._started` set but never read — dead state |
| `audio.py` | `os.path.exists(x) and os.remove(x)` — short-circuit side effect, unreadable |
| `prompts.py` | `if context_words and len(context_words) > 0:` — redundant check |
| `models.py` | Second `if not rows:` in `render_table` is unreachable dead code |
| `models.py` | Quantization `detail` uses original case but validation uses uppercase — inconsistent |
| `llama_cpp.py` | Uses `print()` (stdout) for verbose logs; rest of codebase uses `sys.stderr` |
| `llama_cpp.py` | `str(None)` returns `"None"` if server returns `content: null` |
| `tray.py` | `on_open_settings` callback stored but never called — dead parameter |
| `tray.py` | Color matching is case-sensitive, hardcoded to 3 exact hex values |
| `tray.py` | Mode/style lists hardcoded — not sourced from central definition |
| `overlay.py` | `register_cancel` stores callback but no cancel button exists in the UI |
| `overlay.py` | `stage()`, `segment()`, `audio_info()` are no-ops — silent data discard |
| `benchmark.py` | `except (ImportError, Exception)` — `ImportError` already subclass of `Exception` |
| `benchmark.py` | Hardcoded stage list in Markdown report — new stages silently omitted |
| `intents.py` | `"ide"` in app category check — `app_detect.py` never returns `"ide"` |
| `daemon.py` | `"..."` in hallucination filter set — would filter legitimate "dot dot dot" |
| `daemon.py` | `proc_name` assigned but never used in `_on_dictation_start` |
| `inserter.py` | Releasing `VK_SPACE` even when hotkey doesn't use Space |
| `inserter.py` | `ctypes.wstring_at(p)` has no length bound — could read past buffer |

---

## Test Coverage Gaps

The 90 passing tests give a false sense of security. The **untested** modules are exactly where the critical bugs live:

| Untested Module | Critical Bugs Hidden |
|-----------------|---------------------|
| `daemon.py` | C2 (copy_selected_text), H3, H4, H5, M18, M19, M20 |
| `hotkeys.py` | C1 (Ctrl+Shift+T never fires), double-tap recording bug |
| `inserter.py` | H1 (clipboard clobbered), H2 (clipboard not restored) |
| `server.py` | C9 (cfg.mode race), H14 (file corruption), H15 (DoS) |
| `overlay.py` | M28 (non-daemon timer) |
| `app_detect.py` | M21 (HWND truncation on 64-bit Windows) |
| `transforms.py` | C8 (KeyError on custom transforms) |
| `snippets.py` | M24 (chained substitution), M25 (re.sub backreferences) |
| `vocabulary.py` | M26 (race), M27 (data loss on crash) |
| `qwen3_asr.py` | H6 (deadlock), H7 (language ignored), H8 (race) |

**Recommendation:** Add integration tests that exercise the daemon → hotkeys → inserter → pipeline flow with mocked platform APIs.

---

## Top 10 Actions (Priority Order)

| # | Priority | Issue | Fix |
|---|----------|-------|-----|
| 1 | **P0** | C1: Ctrl+Shift+T never fires | Fix `_key_to_name` / trigger comparison in `hotkeys.py` |
| 2 | **P0** | C2: `copy_selected_text` missing | Fix import in `daemon.py` to use `get_selected_text` |
| 3 | **P0** | C4: `str.format` crashes on braces | Use `.replace()` in `prompts.py` |
| 4 | **P0** | C5: Headless `--duration 0` hangs | Fix `NullNotifier.register_start` to not call `cb()` |
| 5 | **P0** | C6: Config booleans overridden by CLI | Use `argparse.SUPPRESS` defaults |
| 6 | **P0** | C8: Custom transforms KeyError | Pass `instruction` to `.format()` in `transforms.py` |
| 7 | **P1** | H1+H2: Clipboard clobbered | Implement clipboard save/restore in `inserter.py` |
| 8 | **P1** | H6: Qwen3-ASR deadlock | Use dual reader threads like `whisper_cpp.py` |
| 9 | **P1** | H10-H12: Formatting bugs | Fix backtrack, auto-space, press-enter in `formatting.py` |
| 10 | **P1** | C9: Server cfg.mode race | Use `dataclasses.replace(cfg, mode=mode)` in `server.py` |
