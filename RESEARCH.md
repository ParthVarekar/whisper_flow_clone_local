# Research checklist & caveats

This document records what was researched before implementing whisper-flow, the
sources consulted, and the uncertainties that remain (and must be validated on
the target machine). Full citations are at the bottom.

## Research checklist

- [x] **llama.cpp audio support** — does it have a stable Whisper transcription path?
      Result: **No.** Audio support is for multimodal audio-LLMs only
      (Ultravox, Voxtral, Qwen3-ASR, Qwen2.5-Omni) via `mmproj` projectors.
      Whisper is not in the supported model list. (`docs/multimodal.md`)
- [x] **llama.cpp server `/v1/audio/transcriptions`** — does it exist?
      Result: **No.** It is an open feature request (issues #15291, #21852).
      The only audio surface is `input_audio` content on `/v1/chat/completions`
      (audio-LLM, not transcription).
- [x] **whisper.cpp current state** — stable? latest? features?
      Result: **Yes.** v1.9.x stable, actively maintained, ggml `.bin` models,
      CPU/Metal/CUDA/Vulkan/ROCm, JSON/SRT/VTT output, mic streaming, HTTP
      server example.
- [x] **"Whisper inside llama.cpp" supported?**
      Result: **No.** Not an official workflow.
- [x] **Recommended architecture** — single-backend vs split?
      Result: **Split.** whisper.cpp (STT) + llama.cpp (LLM). Confirmed by
      upstream's own `examples/talk-llama` combined binary.
- [x] **Integration patterns** — combined binary / subprocess / bindings / HTTP?
      Result: subprocess (`whisper-cli -oj`) + HTTP (`llama-server`) chosen as
      simplest robust, stdlib-only, no-ABI-coupling approach.
- [x] **Model acquisition** — where to get Whisper ggml + GGUF LLM?
      Result: Whisper via `models/download-ggml-model.sh` (HuggingFace
      `ggerganov/whisper.cpp`); GGUF LLMs from HuggingFace `ggml-org`.
- [x] **Microphone capture on Linux** — simplest reliable method?
      Result: `arecord -f cd -r 16000 -c 1 -d N out.wav` (ALSA, usually
      preinstalled) or `ffmpeg -f pulse/alsa -i default ...`.
- [x] **Build system** — official build path?
      Result: CMake for both. `cmake -B build && cmake --build build`.
      GPU via `-DGGML_CUDA=1` / `-DGGML_VULKAN=1` / `-DGGML_HIP=1`; Metal
      automatic on Apple Silicon.

## Chosen architecture (summary)

```
audio → whisper.cpp (whisper-cli, ggml .bin) → text + segments
      → llama.cpp (llama-server, GGUF, /v1/chat/completions) → processed text
```

Orchestrator: Python 3.8+, **stdlib only** (no pip dependencies), driving the
two official C++ backends via subprocess + HTTP. See `ARCHITECTURE.md`.

## Caveats / uncertainties (to validate on the target machine)

1. **Build toolchain must be present.** `scripts/build.sh` requires
   `cmake`, `gcc`/`clang`, `git`, `curl`. GPU builds additionally require the
   CUDA / Vulkan / ROCm SDK. SDL2 is only needed for whisper.cpp's own
   in-process mic examples (not by whisper-flow, which uses arecord/ffmpeg).
2. **`llama-cli` flag names vary across builds.** The `cli` LLM mode
   (`llm.mode = "cli"`) uses `-no-cnv` and `--no-display-prompt`, which exist
   in recent llama.cpp builds but may not in older ones. **Server mode
   (`llm.mode = "server"`, the default) is robust and recommended.** If `cli`
   mode fails with an unknown-flag error, the backend surfaces a hint.
3. **`whisper-stream` is upstream-described as "naive".** Fine for prototyping
   live mic; for production streaming a custom VAD/chunking loop is needed.
   whisper-flow uses batch mic capture (record N seconds → transcribe), which
   is robust and simple. True low-latency streaming is out of scope for the
   smallest correct version.
4. **whisper.cpp GPU is build-time.** There is no stable runtime `--gpu` flag;
   the backend (CPU/CUDA/Vulkan/ROCm) is chosen at CMake configure time. The
   `transcription.gpu` config field is therefore informational for the STT
   backend (documented in code). For the LLM backend, `-ngl` (gpu_layers) is a
   runtime flag and is honored.
5. **whisper-server's endpoint is `/inference`, not OpenAI-compatible.**
   whisper-flow does not use `whisper-server`; it uses `whisper-cli` directly.
   If you need an OpenAI-compatible `/v1/audio/transcriptions` surface, use a
   wrapper (LocalAI, Lemonade Server) or whisper-flow's own `/transcribe`
   HTTP endpoint.
6. **Model size vs RAM.** Whisper `medium` ≈ 2.1 GB, `large-v3` ≈ 3.9 GB; a
   1–3B Q4 LLM ≈ 1–2 GB. Plan total memory if running both simultaneously.
   Defaults (`base.en` + `gemma-3-1b-it-Q4_K_M`) fit comfortably in ~3 GB.
7. **PulseAudio/PipeWire device selection.** On multi-device systems you may
   need `--mic-device plughw:CARD=...` (ALSA) or a specific PulseAudio source.
   `arecord -l` / `pactl list sources short` list devices.
8. **llama.cpp release cadence** uses build-number tags (e.g. `b9860`), not
   semver. `scripts/build.sh` clones `--depth 1` (latest master). For
   reproducibility, pin a specific tag/commit in `third_party/llama.cpp`.
9. **HF cache migration.** llama.cpp's `-hf` auto-download now lands in the
   standard HuggingFace cache. whisper-flow's `download_models.sh` downloads
   into the project's `models/` dir explicitly, so this doesn't affect it.
10. **Qwen3-ASR via llama.cpp** is the most interesting "audio-LLM as ASR"
    alternative and could be benchmarked as a future STT backend, but it is
    too new to be the recommended default. The `TranscriptionBackend` ABC
    would accommodate a `LlamaCppASRBackend` implementation if added later.

## What was intentionally left out of the smallest correct version

- Auto-spawning `llama-server` from the orchestrator (lifecycle complexity).
- Silero VAD integration beyond a passthrough config flag (whisper.cpp's
  server supports `--vad --vad-model`; the CLI uses internal energy VAD).
- Word-level / token-level DTW timestamps (whisper.cpp supports `-wt`/`-dtw`;
  easy to expose later via a config flag).
- Diarization (whisper.cpp `small.en-tdrz` + `-di`).
- A web UI (the spec asked CLI first; HTTP server is provided as the bridge).
- Docker packaging (the build scripts are reproducible without it).

These are all natural extensions that fit the existing ABCs without rework.

## GUI notifier — progress model (Task ID 3 research)

The GUI notifier mirrors whisper.cpp's *own* live feedback, confirmed verbatim
from upstream `examples/cli/cli.cpp` (master, 2025-07-02):

- **Progress** is emitted by `whisper_print_progress_callback` (`cli.cpp:353-360`)
  as `fprintf(stderr, "%s: progress = %3d%%\n", "whisper_print_progress_callback", progress)`,
  i.e. the literal line `whisper_print_progress_callback: progress =  10%` (note
  `%3d` right-justifies, so single-digit % has two leading spaces). It fires only
  when `progress >= progress_prev + progress_step` (`progress_step` defaults to 5,
  `cli.cpp:41`), so ~20 lines max per run (5%, 10%, …, 100%). Enabled by `-pp`.
  Throttled inline in the main transcription loop (`src/whisper.cpp:7021-7026`),
  not on a timer thread.
- **Segments** are emitted by `whisper_print_segment_callback` (`cli.cpp:362-453`)
  via `printf("[%s --> %s]  ", …)` (two spaces after `]`) + the segment text +
  `\n`, flushed per segment (`fflush(stdout)`). Timestamps use
  `"%02d:%02d:%02d.%03d"` → `HH:MM:SS.mmm`. Emitted **per closed segment**, not
  token-by-token.
- **`-np` / `--no-prints`** (`cli.cpp:71,191,1039-1041`) silences all
  `WHISPER_LOG_*` noise (model load, `system_info`, `whisper_print_timings`) on
  stderr, but does NOT silence the raw `fprintf(stderr)` progress line nor the
  `printf` segment line — so progress + segments still stream with `-np`.
- **`-of -` (pipe mode) DISABLES both callbacks** (`cli.cpp:1119-1124`) and
  forces `print_progress=false`. whisper-flow therefore NEVER uses `-of -`; it
  always writes JSON to a temp file via `-of <tmp>/out` and reads it at the end.
- **`-oj`** writes the final authoritative JSON to `<out>.json` AFTER
  `whisper_full` completes (`cli.cpp:1329`); it does not suppress the live
  stdout/stderr streaming.

So the whisper-flow whisper.cpp backend invokes:
`whisper-cli -m <model> -f <wav> -oj -of <tmp>/out -np [-pp] ...`
and streams both pipes line-by-line, forwarding parsed `progress = NN%` (stderr)
and `[ts --> ts]  text` (stdout) events to the notifier. The `-pp` flag is only
added when a progress callback is registered (i.e. when a notifier is present).

For reference: OpenAI's reference `whisper` (Python) uses a single `tqdm` bar
on stderr per 30-second chunk. whisper-flow mirrors the whisper.cpp model
(discrete % lines + segment lines) since it subprocesses `whisper-cli`.

## Sources

- llama.cpp (master): https://github.com/ggml-org/llama.cpp
  - README: https://github.com/ggml-org/llama.cpp/blob/master/README.md
  - Multimodal: https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md
  - Server: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
  - Issue #15291 (open): https://github.com/ggml-org/llama.cpp/issues/15291
  - Issue #21852 (open): https://github.com/ggml-org/llama.cpp/issues/21852
- whisper.cpp (master): https://github.com/ggml-org/whisper.cpp
  - README: https://github.com/ggml-org/whisper.cpp/blob/master/README.md
  - Releases: https://github.com/ggml-org/whisper.cpp/releases
  - Models: https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md
  - CLI: https://github.com/ggml-org/whisper.cpp/blob/master/examples/cli/README.md
  - Stream (mic): https://github.com/ggml-org/whisper.cpp/blob/master/examples/stream/README.md
  - Server: https://github.com/ggml-org/whisper.cpp/blob/master/examples/server/README.md
  - talk-llama (combined): https://github.com/ggml-org/whisper.cpp/blob/master/examples/talk-llama/README.md
- Models:
  - Whisper ggml: https://huggingface.co/ggerganov/whisper.cpp
  - GGUF LLMs: https://huggingface.co/ggml-org
- Bindings (considered, not used):
  - llama-cpp-python: https://llama-cpp-python.readthedocs.io
  - whisper-cpp-python: https://github.com/carloscdias/whisper-cpp-python
- OpenAI-compatible wrappers around whisper.cpp (considered, not used):
  - LocalAI: https://localai.io/features/audio-to-text
  - Lemonade Server: https://lemonade-server.ai/docs/api/openai
- Mic capture reference: https://whynothugo.nl/journal/2024/09/22/transcribing-audio-with-whisper.cpp/
