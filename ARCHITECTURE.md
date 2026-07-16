# Architecture note

## Decision

**whisper.cpp for STT + llama.cpp for the LLM stage**, orchestrated by a
Python stdlib-only CLI. The two backends are separate processes driven by the
orchestrator (subprocess for `whisper-cli`, HTTP for `llama-server`).

## Why this is the correct architecture (verified upstream!)

### 1. llama.cpp has no Whisper transcription

llama.cpp's audio support (per `docs/multimodal.md` on master) is for
**multimodal audio-LLMs** — models that ingest audio as a token stream via an
`mmproj` projector and reason over it. The supported audio models are:

- Ultravox 0.5
- Voxtral-Mini-3B
- Qwen3-ASR-0.6B / 1.7B
- Qwen2.5-Omni / Qwen3-Omni (audio + vision)

These are "listen and answer" models, not a Whisper encoder/decoder. **Whisper
is not in the supported model list.** "Whisper inside llama.cpp" is not an
officially supported workflow.

### 2. llama.cpp server has no `/v1/audio/transcriptions`

The OpenAI-compatible transcription endpoint is an **open feature request**,
not a shipped feature:

- Issue #15291 — "add /v1/audio/transcriptions endpoint for local openai STT"
- Issue #21852 — "Support OpenAI speech-to-text interface /v1/audio/transcriptions"

The only audio-related server surface today is the `input_audio` content type
on `/v1/chat/completions`, which routes audio into an audio-LLM — i.e. "describe
this audio" / "answer a question about this audio", not "transcribe this
verbatim".

### 3. whisper.cpp is the stable, official Whisper port

- Actively maintained, latest stable **v1.9.x**
- ggml `.bin` model format (NOT GGUF)
- Broad hardware: CPU, Metal (auto on Apple), CUDA, Vulkan, ROCm, OpenBLAS,
  CoreML, OpenVINO, CANN
- Shipped tools: `whisper-cli` (file), `whisper-stream`/`whisper-command`
  (mic, SDL2), `whisper-server` (HTTP `/inference`), `whisper-talk-llama`
- Outputs: txt, srt, vtt, lrc, csv, **json** (`-oj`), word-level timestamps

### 4. Upstream itself blesses the split

whisper.cpp ships `examples/talk-llama` — a single binary that links **both**
whisper.cpp (for STT) and llama.cpp (for the LLM). The canonical local voice
pipeline from upstream is exactly: **whisper.cpp handles speech, llama.cpp
handles the LLM.** Our orchestrator reproduces that architecture in Python so
the two backends stay cleanly separable and individually upgradeable.

## Tradeoff vs. a single-llama.cpp pipeline

| | whisper.cpp + llama.cpp (chosen) | single llama.cpp audio-LLM |
|---|---|---|
| Whisper quality | ✅ full Whisper, 90+ languages | ⚠️ depends on Qwen3-ASR/Voxtral |
| Stable STT API | ✅ `whisper-cli -oj` JSON | ❌ no `/v1/audio/transcriptions` |
| Backend maturity | ✅ years of production use | ⚠️ audio-LLMs are newer |
| One binary | ❌ two backends | ✅ one binary |
| One model | ❌ ggml `.bin` + GGUF | ✅ one GGUF |
| Reasoning over audio | ❌ text only between stages | ✅ native |

For a general **speech → text → text-LLM** pipeline, the chosen architecture is
correct and stable. A single-llama.cpp pipeline would only be preferable for
"listen to audio and reason about it" tasks, which is a different product.

## Component responsibilities

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Wispr Flow OS Layer                                │
│                                                                             │
│  ┌──────────────────┐    triggers     ┌──────────────────┐                  │
│  │ hotkeys.py       │ ──────────────► │ daemon.py        │  Global Hotkey & │
│  │  Ctrl+Shift+Spc  │                 │  Orchestrator    │  Tray Service    │
│  └──────────────────┘                 └────────┬─────────┘                  │
│           ▲                                    │                            │
│           │ updates & audio chimes             ▼                            │
│  ┌──────────────────┐                 ┌──────────────────┐                  │
│  │ overlay.py       │ ◄────────────── │ intents.py       │  Intent Router & │
│  │  Capsule HUD     │                 │  Mind Reader     │  UI Overlay      │
│  └──────────────────┘                 └────────┬─────────┘                  │
└────────────────────────────────────────────────┼────────────────────────────┘
                                                 │ audio / text
                                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Pipeline & STT/LLM Engines                         │
│                                                                             │
│  ┌──────────────────┐    subprocess / ONNX / HTTP                           │
│  │ audio.py         │ ────────────────► ┌────────────────────────────────┐  │
│  │  capture / VAD   │                   │ TranscriptionBackend (ABCs)    │  │
│  └──────┬───────────┘                   │  ├─ WhisperCppBackend (CUDA)   │  │
│         │ WAV path                      │  ├─ MoonshineBackend (ONNX)    │  │
│         ▼                               │  └─ Qwen3AsrBackend            │  │
│  ┌──────────────────┐                   └────────────────┬───────────────┘  │
│  │ pipeline.py      │                                    │ result / text    │
│  │  format / clean  │ ◄──────────────────────────────────┘                  │
│  └──────┬───────────┘                                                       │
│         │ text                                                              │
│         ▼                                                                   │
│  ┌──────────────────┐    HTTP           ┌────────────────────────────────┐  │
│  │ prompts.py       │ ────────────────► │ LlamaCppBackend (/v1/chat/...) │  │
│  │  formatting      │                   └────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **`backends/base.py`** defines `TranscriptionBackend` and `LLMBackend` ABCs.
  Swap any STT engine (`whisper_cpp`, `moonshine`, `qwen3_asr`) cleanly via configuration without altering pipeline logic.
- **`overlay.py`** delivers an Apple-style rounded capsule HUD using Win32 `-transparentcolor` layered windows, asynchronous scheduled callbacks (`root.after`), and `ctypes` high-DPI scaling.
- **`daemon.py` & `intents.py`** orchestrate background recording, window detection, acoustic prompt biasing (`-p`), and intelligent intent routing (dictation vs command mode).
- **`inserter.py`** safely injects text directly at the active cursor across any OS window using Win32 simulated input (`SendInput`) and clipboard preservation.
- **`audio.py` & `pipeline.py`** handle high-accuracy audio normalization, VAD silence trimming, rule-based formatting (`formatting.py`), and optional LLM refinement (`llama_cpp.py`).

## Why subprocess + HTTP (not Python bindings)?

- **Minimal dependencies**: the orchestrator is Python stdlib-only. No
  `llama-cpp-python`, `whisper-cpp-python`, torch, or ctypes ABI risk.
- **Official builds**: uses the upstream CMake build system verbatim.
- **Debuggability**: each backend is a normal process you can run/inspect
  independently.
- **Separable upgrade**: bump whisper.cpp or llama.cpp without touching the
  other or the orchestrator.

The cost is two processes to manage. For a CLI-first tool this is fine; the
README documents the one-line `llama-server` start. (A future enhancement could
auto-spawn `llama-server` from the orchestrator, but that adds lifecycle
complexity the "smallest correct version" doesn't need.)
