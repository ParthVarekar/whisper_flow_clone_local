# Gap Analysis — whisper-flow production audit

Audit of the existing implementation against the 14 requested areas, with an
explicit decision per gap: **implement**, **document as future work**, or
**skip (with reason)**. Research findings from Tasks 5/6/7 are cited inline;
full sources in `RESEARCH.md`.

## Honest scope note

This audit was performed in a **headless sandbox**: no `whisper-cli` /
`llama-server` binaries built, no `$DISPLAY`, no microphone, no Docker daemon,
no PyInstaller. Therefore:
- **Verified by tests**: Python syntax/imports, config loading, parser regexes,
  notifier event flow (with mock backends), CLI arg parsing, HTTP server
  endpoints, model-discovery scanning logic, benchmark arithmetic.
- **Verified by upstream docs** (not by running): whisper-cli `--vad -vm` flags,
  Silero VAD ggml model path, ffmpeg per-OS device syntax, pyproject.toml /
  hatchling behavior, tomllib stdlib API.
- **NOT verified here** (requires the user's machine): actual transcription,
  actual GUI rendering, actual mic capture, Docker image build, PyInstaller
  bundle. These are documented as reproducible-from-README and flagged in
  `FINAL_REPORT.md`.

---

## 1. Streaming

**Current state**: `whisper_cpp.py` uses `Popen` + two reader threads and
forwards `whisper_print_progress_callback` (stderr) + `whisper_print_segment_callback`
(stdout) to the notifier **as they arrive**. This IS real-time segment streaming
(matching whisper.cpp's own feedback model — see `RESEARCH.md` Task 3).

**Gap**: No *token-level* partial transcripts. whisper.cpp emits only closed
segments via subprocess; token-by-token output requires `-pc`/`--print-colors`
(ANSI-colored, hard to parse) or linking libwhisper (breaks stdlib-only).

**Decision**: **Document as a known limitation.** True token streaming is not
exposed by `whisper-cli`'s subprocess interface. The current closed-segment
streaming is exactly what whisper.cpp itself shows the user. Linking libwhisper
from Python would add a C extension + ABI coupling, violating the stdlib-only
design principle. `FINAL_REPORT.md` records this.

**Action**: No code change. Add a clear note to `RESEARCH.md` + `FINAL_REPORT.md`.

## 2. Voice Activity Detection

**Current state**: `TranscriptionConfig.vad` / `vad_model` fields exist but are
**not passed to whisper-cli** (Task 1 believed only the server supported `--vad`;
Task 5 research corrected this — `whisper-cli` natively supports `--vad -vm`
since the VAD C API landed in v1.8.5, wired at `cli.cpp:1248-1256`).

**Gap**: VAD flags not wired through; no auto-start/stop mic mode.

**Decision**: **Implement.**
- Wire `--vad` + `-vm <vad_model>` into `_build_cmd` when `cfg.vad` is true and
  `cfg.vad_model` is set.
- Add a `stream` mic mode (`mic --vad` or `--stream`) that captures a rolling
  buffer and periodically re-runs whisper-cli with VAD, emitting only new
  segments. This delivers "automatic start/stop, minimal latency, no manual
  duration" using Silero VAD inside whisper-cli (zero Python deps, MIT, 864 KB).
- Add `ggml-silero-v6.2.0.bin` to `download_models.sh` via the official
  `models/download-vad-model.sh silero-v6.2.0`.

**Chosen VAD**: whisper.cpp's built-in Silero VAD via `whisper-cli --vad -vm`.
Justification in `RESEARCH.md` §B.recommendation: preserves stdlib-only,
Silero-grade accuracy (<1 ms / 30 ms frame, MIT), already wired upstream.

## 3. GUI

**Current state**: `TkNotifier` has stage label + progress bar + segment log.
Missing: recording indicator, level meter, elapsed timer, model label, speed,
cancel, copy, save.

**Gap**: All nine requested widgets absent.

**Decision**: **Implement** (per Task 6 research — Buzz is the canonical
reference; do not invent UX).
- Recording indicator: red `● REC` `ttk.Label` (hidden unless recording).
- Level meter: custom `tk.Canvas` symmetric RMS bar meter (ported from Buzz's
  `AudioMeterWidget` pattern: 2px bars, 0.95 peak decay). Fed by a new
  `Notifier.amplitude(rms)` method; RMS computed from the capture subprocess
  stdout where possible, else a periodic probe.
- Elapsed timer: `StringVar` + `root.after(1000, _tick)`, `MM:SS`/`HH:MM:SS`.
- Model label: `StringVar` fed by new `Notifier.audio_info(duration_sec, model_name)`.
- Speed (×realtime): `audio_duration_sec / elapsed`; updated on each progress
  event. WhisperDesktop convention.
- Cancel: `ttk.Button` → cooperative flag → `subprocess.terminate()` →
  `kill()` after 5 s (Buzz's layered pattern). New `CancelledError`.
- Copy: `ttk.Button` → `root.clipboard_clear()` + `clipboard_append()` with
  2 s "Copied!" feedback (Buzz pattern).
- Save: `ttk.Button` → `filedialog.asksaveasfilename` → reuse pipeline writers.

## 4. Benchmarking

**Current state**: None.

**Gap**: No timing/throughput measurement.

**Decision**: **Implement** a `benchmark.py` module.
- Per-stage timers: audio_load, preprocess (normalize), transcription, llm,
  total. Uses `time.perf_counter()`.
- Derived metrics: realtime_factor = audio_duration / transcription_time;
  tokens/sec (from LLM response char count / llm_time, approximate);
  peak RSS via `resource.getrusage` (POSIX) / `psutil` (optional).
- Export: JSON + Markdown report. CLI flag `--benchmark <path>` writes both.
- New `bench` subcommand to run a file through the pipeline and emit a report
  without requiring the user to parse stdout.

## 5. Model management

**Current state**: User must type `--whisper-model <path>` and `--llm-model <path>`.

**Gap**: No discovery; no selection without paths; no incompatibility warnings.

**Decision**: **Implement** a `models.py` module.
- Scan common dirs: `./models/`, `~/.cache/whisper.cpp/`, `~/.cache/huggingface/`,
  `WHISPER_FLOW_MODELS_DIR` env, the whisper.cpp clone's `models/`, plus any
  dir on a new `--model-dirs` flag.
- Classify by extension + magic: `.bin` whose basename matches
  `ggml-(tiny|base|small|medium|large).*` → Whisper; `.gguf` → LLM;
  `ggml-silero-*.bin` → VAD.
- `list_models()` → `{whisper: [...], gguf: [...], vad: [...]}`.
- New `models` subcommand: prints a table; `--select` launches an interactive
  picker (numbered list) if stdin is a TTY, else prints for scripting.
- Incompatibility warnings: warn if a Whisper `.bin` isn't a recognized size
  name; warn if a GGUF's filename quant tag is unknown; warn if the VAD model
  doesn't match `silero-v*`.

## 6. Robustness

**Current state**: Typed exceptions exist (`BinaryNotFoundError`, etc.). Mic
capture has basic error handling. But: no missing-mic detection, no empty-
transcript handling, no crashed-subprocess recovery, no partial-output
recovery, Ctrl+C on `notifier.run` not fully clean.

**Gap**: Multiple robustness holes.

**Decision**: **Implement** fixes.
- Missing mic: `capture_mic` probes the device with a 0.5 s arecord/ffmpeg
  dry-run first; clear error if no input device.
- Unsupported sample rate: validate `cfg.sample_rate` is in whisper.cpp's
  accepted set; document that ffmpeg always resamples to 16 kHz anyway.
- Corrupted audio: `normalize_file` already checks output size > 0; add a
  WAV header sanity check (RIFF/WAVE/fmt) before handing to whisper-cli.
- Empty transcript: if whisper-cli exits 0 but JSON has no segments, raise
  `TranscriptionError("empty transcript — audio may be silent")`.
- Crashed subprocess: `Popen.wait()` already captures returncode; ensure
  reader threads are joined + stderr tail included in the error.
- Interrupted execution / Ctrl+C: `CancelledError` path + `KeyboardInterrupt`
  in `notifier.run` → clean subprocess terminate.
- Partial output recovery: if whisper-cli dies mid-run but a partial JSON
  exists, attempt to parse it and return partial segments with a warning flag.

## 7. Configuration

**Current state**: JSON + env + CLI overrides. No TOML.

**Gap**: No TOML support.

**Decision**: **Implement** via conditional import.
- `config.py`: try `import tomllib` (3.11+ stdlib), fall back to `tomli`
  (optional extra on 3.8–3.10), else raise `ConfigError` with install hint.
- Detect format by extension: `.json` → json; `.toml` → tomllib; `.toml`/`.json`
  via `--config` auto-detected.
- Document precedence in README: defaults < TOML/JSON file < env vars < CLI.

## 8. Cross-platform

**Current state**: Linux-only mic (arecord + ffmpeg pulse/alsa).

**Gap**: No Windows / macOS capture commands.

**Decision**: **Implement** per-OS ffmpeg commands (Task 7 research).
- Linux: arecord (primary) / `ffmpeg -f pulse -i default` / `ffmpeg -f alsa`.
- macOS: `ffmpeg -f avfoundation -i ":default"` (documented default alias).
- Windows: `ffmpeg -f dshow -i audio="<device>"` with a `--list-devices` helper
  that parses `ffmpeg -list_devices true -f dshow -i dummy` stderr.
- Optional `sounddevice` extra (`pip install whisper-flow[mic]`) for a single
  cross-platform codepath; preferred if importable.
- Platform detection via `sys.platform` (`linux`/`win32`/`darwin`).

## 9. Testing

**Current state**: Ad-hoc inline tests run via `python -c` (not persistent).

**Gap**: No test suite.

**Decision**: **Implement** a `tests/` dir with `pytest`-compatible tests.
- `tests/test_parsers.py`: progress + segment regexes against exact upstream
  format strings (regression-safe).
- `tests/test_config.py`: defaults, env, JSON, TOML, CLI override precedence;
  bool/int/float coercion.
- `tests/test_models.py`: discovery + classification with temp dirs.
- `tests/test_benchmark.py`: metric arithmetic (RTF, tokens/sec) with fakes.
- `tests/test_pipeline.py`: end-to-end with mock STT/LLM backends; event
  ordering; cancel path; empty-transcript path.
- `tests/test_audio.py`: WAV header sanity check; corrupted-input rejection.
- Mock whisper.cpp via a fake `whisper-cli` shell script on PATH that emits
  canned progress + segment lines.
- `tests/conftest.py`: headless `DISPLAY` unset, fake-binaries fixture.
- Run with `python -m pytest tests/ -q` (pytest listed as dev extra; tests
  use only stdlib + pytest, no other deps).

## 10. Documentation

**Current state**: README, ARCHITECTURE, RESEARCH, demo/README.

**Gap**: No architecture/sequence diagrams, no troubleshooting, no perf tuning,
no GPU section, no FAQ.

**Decision**: **Implement** doc expansions.
- ASCII architecture diagram (already in ARCHITECTURE.md — enhance).
- ASCII sequence diagram for file + mic + streaming-mic flows.
- New `docs/TROUBLESHOOTING.md`: missing binary, missing model, mic device,
  llama-server down, OOM, slow CPU.
- New `docs/PERFORMANCE.md`: model size vs RAM table, GPU build flags per
  backend, thread tuning, chunking guidance.
- New `docs/FAQ.md`.
- Screenshots: **cannot capture** in headless sandbox → document as a follow-up
  the user can produce (`python -m whisper_flow transcribe -f ... ` then
  screenshot the window). Honest note in `FINAL_REPORT.md`.

## 11. Packaging

**Current state**: None (run-from-source).

**Gap**: No pip install, no exe, no Docker.

**Decision**: **Implement**.
- `pyproject.toml` (hatchling backend, `[project.scripts] whisper-flow`).
- `whisper-flow[toml]`, `whisper-flow[mic]`, `whisper-flow[dev]` extras.
- `Dockerfile` (multi-stage: build whisper.cpp + llama.cpp, install whisper-flow,
  expose HTTP port, headless CMD). **Cannot build/test in sandbox** →
  documented as reproducible; honest note in `FINAL_REPORT.md`.
- `pyinstaller.spec` for standalone exe. **Cannot cross-build in sandbox** →
  spec provided + build instructions; not verified here.
- `scripts/release.sh` sketch: `python -m build` + per-OS PyInstaller matrix
  (documented; not run here).

## 12. Code review

**Current state**: Functional but audit needed.

**Gaps found during this audit** (to fix):
- `notifier.py`: `_closed` flag set but never read; `_worker` catches
  `BaseException` (too broad — masks `KeyboardInterrupt` handling).
- `whisper_cpp.py`: reader threads swallow all exceptions silently (correct
  for notifier safety, but a bug in the parser would be invisible — add
  debug logging when verbose).
- `audio.py`: `_capture_arecord` uses `-f cd` (44100) then immediately sets
  `-r 16000` — the `-f cd` is misleading/contradictory; fix to `-f dat` or
  drop `-f` and rely on `-r -c`.
- `pipeline.py`: `run_file`/`run_mic` reference `result`/`processed`/`written`
  in the return dict even on the error path (they'd be undefined) — the
  `except` re-raises so it's currently safe, but fragile; restructure.
- `cli.py`: `_cmd_serve` had a dead `cfg._serve_host` line (removed in Task 4
  — verify).
- `server.py`: uses `cgi.FieldStorage` (deprecated in 3.13, will be removed);
  replace with a small multipart parser or `email` module. **Implement** a
  minimal multipart parser to avoid the deprecation.
- Resource leaks: temp WAV files in `audio.py` are `delete=False` and only
  removed on success paths — add cleanup in a `finally`.

**Decision**: **Fix all of the above.**

## 13. Research validation

**Re-checked** (Task 5, §C):
- llama.cpp still has NO `/v1/audio/transcriptions` — confirmed (0 matches in
  server README; issues #15291/#21852 still open).
- whisper.cpp latest stable: v1.9.1 — confirmed.
- `whisper-cli` flags `-oj -np -pp -of -fa -l -tr` — all still valid.
- **NEW since Task 1**: `whisper-cli` natively supports `--vad -vm` (Silero).
  Task 1 missed this; Task 5 confirmed at `cli.cpp:1248-1256`. Implementation
  updated in §2.

**Action**: Update `RESEARCH.md` with the VAD-in-cli correction.

## 14. Final deliverable

Produce: implementation + benchmark results + architecture docs + known
limitations. See `FINAL_REPORT.md` (written last, after verification).

---

## Summary table

| Area | Decision | Verified by |
|---|---|---|
| 1. Streaming | Document limitation (closed-segment streaming already works) | upstream cli.cpp |
| 2. VAD | **Implement** (`--vad -vm` + rolling-buffer mic mode) | upstream cli.cpp:1248-1256 |
| 3. GUI | **Implement** (9 widgets per Buzz patterns) | Task 6 research |
| 4. Benchmarking | **Implement** (`benchmark.py` + `bench` cmd) | tests |
| 5. Model mgmt | **Implement** (`models.py` + `models` cmd) | tests |
| 6. Robustness | **Implement** (7 fixes) | tests |
| 7. Config TOML | **Implement** (cond. import) | tests |
| 8. Cross-platform | **Implement** (per-OS ffmpeg + sounddevice extra) | upstream ffmpeg docs |
| 9. Testing | **Implement** (pytest suite) | `pytest` run |
| 10. Docs | **Implement** (diagrams + 3 new docs) | review |
| 11. Packaging | **Implement** (pyproject + Dockerfile + spec) | pyproject parse; Docker/spec not built here |
| 12. Code review | **Fix** (7 issues) | tests + lint |
| 13. Research validation | **Updated** (VAD-in-cli correction) | upstream re-fetch |
| 14. Final deliverable | `FINAL_REPORT.md` | — |
