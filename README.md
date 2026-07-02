# whisper-flow

A **fully local, offline** speech-to-text → LLM processing pipeline.

```
audio (file or mic)
   │
   ▼
whisper.cpp  (whisper-cli)        ── STT backend, ggml .bin models
   │  text + segment timestamps
   ▼
llama.cpp    (llama-server)       ── LLM backend, GGUF models
   │  summarized / corrected / command / assistant reply
   ▼
stdout  (or files / HTTP)
```

No cloud. No OpenAI. No external model services. No audio leaves the machine.

The orchestrator is a small **Python stdlib-only** CLI (no pip dependencies)
that drives the two official C++ backends via subprocess + HTTP.

---

## Why this architecture

**whisper.cpp (STT) + llama.cpp (LLM)** is the correct local architecture today.
llama.cpp does **not** support Whisper transcription — its audio support is for
multimodal audio-LLMs (Qwen2.5-Omni, Ultravox, Voxtral, Qwen3-ASR) and it has
**no** OpenAI-compatible `/v1/audio/transcriptions` endpoint (open issues
#15291, #21852). whisper.cpp is the stable, official Whisper port and is
co-blessed by upstream via `examples/talk-llama`. Full reasoning + citations in
[`ARCHITECTURE.md`](ARCHITECTURE.md) and [`RESEARCH.md`](RESEARCH.md).

---

## Requirements

- **Python** 3.8+
- **git**, **cmake** ≥ 3.14, a C/C++ compiler (gcc/clang/MSVC)
- **ffmpeg** (audio normalization + fallback mic capture)
- **arecord** (ALSA utils) — optional, used for mic capture if present
- Optional, for GPU acceleration: CUDA toolkit / Vulkan SDK / ROCm / (Metal is
  automatic on Apple Silicon)
- Optional, for live-mic streaming demos inside whisper.cpp itself: SDL2
  (`apt-get install libsdl2-dev`). Not required by whisper-flow, which captures
  mic via arecord/ffmpeg.

### Linux one-liner (Debian/Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake git ffmpeg alsa-utils curl
```

### macOS

```bash
brew install cmake git ffmpeg curl
# Xcode command line tools provide the compiler:
xcode-select --install
```

### Windows

Run the shell scripts from Git Bash (for example,
`C:\Program Files\Git\bin\bash.exe`) and make sure a C/C++ compiler is on
`PATH` before running setup. The current scripts require `gcc`; install MSYS2
MinGW-w64 or another GCC toolchain, then verify it with:

```bash
gcc --version
cmake --version
ffmpeg -version
git --version
```

---

## Setup

```bash
cd whisper-flow

# 1. Build whisper.cpp + llama.cpp and download default models.
#    Default CPU build. For CUDA: GPU_BACKEND=cuda ./scripts/setup.sh
./scripts/setup.sh

# 2. Put the freshly built binaries on your PATH.
export PATH="$(pwd)/build/whisper/bin:$(pwd)/build/llama/bin:$PATH"

# 3. (Optional) copy the example config and edit model paths.
cp config.example.json config.json
```

`scripts/setup.sh` runs `scripts/build.sh` then `scripts/download_models.sh`.
You can run them separately. Defaults:

| Component | Default | Size |
|---|---|---|
| Whisper model | `base.en` | ~142 MiB |
| LLM model | `gemma-3-1b-it-Q4_K_M.gguf` | ~1 GB |

Override via env vars, e.g. `WHISPER_MODEL=medium ./scripts/download_models.sh`.

### Verify the install

```bash
python -m whisper_flow check \
  --whisper-model "$(pwd)/models/ggml-base.en.bin" \
  --llm-model    "$(pwd)/models/gemma-3-1b-it-Q4_K_M.gguf"
```

---

## Usage

### Transcribe an audio file (STT only)

```bash
python -m whisper_flow transcribe \
  --whisper-model models/ggml-base.en.bin \
  -f path/to/audio.wav --language en
```

Any input format ffmpeg understands is fine (mp3, m4a, flac, …). It is
normalized to 16 kHz mono WAV automatically.

### Transcribe from the microphone

```bash
# record 5 s and transcribe
python -m whisper_flow mic --duration 5 \
  --whisper-model models/ggml-base.en.bin --language en

# record until Ctrl+C (ffmpeg backend)
python -m whisper_flow mic --duration 0 \
  --whisper-model models/ggml-base.en.bin
```

### Full flow: transcribe + LLM

First, start the local LLM server (one terminal):

```bash
llama-server -m models/gemma-3-1b-it-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8080 -c 2048
```

Then run the pipeline (another terminal):

```bash
# summarize
python -m whisper_flow process \
  --whisper-model models/ggml-base.en.bin \
  --llm-model    models/gemma-3-1b-it-Q4_K_M.gguf \
  --llm-host 127.0.0.1 --llm-port 8080 \
  -f path/to/audio.wav --mode summarize

# modes: summarize | correct | command | assistant | raw
# raw = transcription only (skip the LLM)

# from mic, extracting a command
python -m whisper_flow process --mic --duration 8 --mode command \
  --whisper-model models/ggml-base.en.bin \
  --llm-model    models/gemma-3-1b-it-Q4_K_M.gguf \
  --llm-host 127.0.0.1 --llm-port 8080
```

### GUI progress notifier

By default, `transcribe`, `mic`, and `process` open a **live GUI progress window**
that mirrors whisper.cpp's own feedback model (researched from upstream — see
`RESEARCH.md` Task ID 3):

- a **stage label** that walks through phases
  (`Normalizing audio` → `Transcribing` → `LLM processing` → `Done ✓`)
- a **progress bar** driven by whisper.cpp's `whisper_print_progress_callback`
  (`progress = NN%` on stderr, every 5%)
- a **live segment log** that streams each `[HH:MM:SS.mmm --> HH:MM:SS.mmm]  text`
  line as whisper.cpp decodes it (stdout segment callback)

This is the "the tool is working" indicator — you see exactly what whisper sees,
in real time, without watching a terminal.

```bash
# default: GUI window if a display is available
python -m whisper_flow transcribe -f audio.wav \
  --whisper-model models/ggml-base.en.bin

# also pop a desktop notification (notify-send) on start / done / error
python -m whisper_flow process -f audio.wav --mode summarize --notify

# headless / CI: console progress only
python -m whisper_flow transcribe -f audio.wav --no-gui \
  --whisper-model models/ggml-base.en.bin
```

Flags:
- `--gui` — force the GUI window even when no `$DISPLAY` is detected
- `--no-gui` — disable the window; print `[whisper-flow] stage/progress/segment`
  lines to stderr instead (headless / piped / CI friendly)
- `--notify` — additionally fire `notify-send` desktop notifications (Linux,
  requires `libnotify`; no-op if absent)

The window is implemented with **Tkinter (Python stdlib)** — no extra deps. If
Tkinter is unavailable or there's no display, it silently falls back to the
console notifier so the tool never breaks in a headless environment. The window
auto-closes ~2 s after success; on error it stays open so you can read the
message. Either way, the final transcript/processed text is still printed to
stdout exactly as without the GUI.

### Output formats

```bash
--format text   # default: prints transcript (or processed text) to stdout
--format json   # full result with segments + timestamps
--format srt    # subtitles with timestamps
--format vtt    # WebVTT
--format all    # write .txt/.json/.srt/.vtt next to source (use with --write-files)
--json          # print the full result object as JSON to stdout
```

### Long audio

whisper.cpp already processes long audio in 30-second windows internally. For
extremely long recordings, optional pre-chunking avoids one giant pass:

```bash
python -m whisper_flow transcribe -f long.mp3 --chunk-seconds 600 \
  --whisper-model models/ggml-base.en.bin
```

Chunk transcripts are merged with corrected timestamp offsets.

### Configuration

All knobs can be set via (later wins):

1. defaults (see `whisper_flow/config.py`)
2. JSON config file (`--config config.json` or `WHISPER_FLOW_CONFIG=...`)
3. environment variables (`WHISPER_FLOW_<SECTION>__<FIELD>`, e.g.
   `WHISPER_FLOW_TRANSCRIPTION__MODEL=...`)
4. CLI flags (`--whisper-model`, `--llm-model`, `--language`, `--mode`,
   `--gpu`, `--chunk-seconds`, `--mic-device`, `--temperature`, …)

See [`config.example.json`](config.example.json) for the full schema.

### Optional HTTP server

```bash
python -m whisper_flow serve --port 8090 \
  --whisper-model models/ggml-base.en.bin \
  --llm-model    models/gemma-3-1b-it-Q4_K_M.gguf \
  --llm-host 127.0.0.1 --llm-port 8080
```

Endpoints (stdlib `http.server`, no framework):

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | – | `{"ok": true}` |
| POST | `/transcribe` | multipart `audio` | `{transcript, segments, language}` |
| POST | `/process` | multipart `audio`, form `mode` | `{..., processed}` |
| POST | `/transcribe/text` | JSON `{text, mode}` | `{transcript, mode, processed}` |

---

## Project layout

```
whisper-flow/
├── README.md                  this file
├── ARCHITECTURE.md            why whisper.cpp + llama.cpp (not llama.cpp-only)
├── RESEARCH.md                research checklist + caveats
├── config.example.json        full config schema
├── whisper_flow/              Python orchestrator (stdlib only)
│   ├── __main__.py            `python -m whisper_flow`
│   ├── cli.py                 argparse subcommands
│   ├── config.py              JSON + env + CLI config
│   ├── errors.py              typed exceptions
│   ├── audio.py               mic capture + normalize + chunk
│   ├── pipeline.py            orchestration + output formats
│   ├── prompts.py             LLM mode templates
│   ├── notifier.py            GUI/console progress notifier (Tkinter + notify-send)
│   ├── server.py              optional minimal HTTP server
│   └── backends/
│       ├── base.py            TranscriptionBackend / LLMBackend ABCs + progress cb types
│       ├── whisper_cpp.py     STT: subprocess whisper-cli (streams -pp/-np output)
│       └── llama_cpp.py       LLM: HTTP llama-server (+cli fallback)
├── scripts/
│   ├── setup.sh               build + download models
│   ├── build.sh               build whisper.cpp + llama.cpp
│   └── download_models.sh     ggml Whisper + GGUF LLM
└── demo/
    └── README.md              minimal file + mic demos
```

The transcription and LLM backends are **separable modules** behind abstract
interfaces (`backends/base.py`), so you can swap either without touching the
pipeline.

---

## Acceptance criteria

- [x] Run a local command that transcribes an audio file
      (`whisper-flow transcribe -f audio.wav`)
- [x] Run a local command that listens to the mic and transcribes
      (`whisper-flow mic --duration 5`)
- [x] Transcript can be passed into a local llama.cpp model
      (`whisper-flow process ... --mode summarize`, via `llama-server`)
- [x] No cloud calls are made (orchestrator is stdlib-only; backends are local
      binaries; no `api.openai.com` or similar anywhere in the code)
- [x] Reproducible from the README (`scripts/setup.sh` + commands above)

See [`RESEARCH.md`](RESEARCH.md) for the full research checklist and known
caveats.

---

## License

The orchestrator code in this repo is yours to use/modify. The upstream
backends (whisper.cpp, llama.cpp) and models retain their own licenses — see
their respective repositories.
