# Second Brain — Tauri v2 Desktop Shell

This directory contains the Tauri v2 desktop wrapper that turns the Next.js
web app into a native desktop application with:

- **Global hotkey** `Ctrl+Shift+B` — brain capture (works from any app)
- **Global hotkey** `Ctrl+Shift+F` — brain search (focuses the search bar)
- **System tray icon** with quick actions (capture, search, show, quit)
- **Capture overlay** — a small borderless window that appears on hotkey press
- **Python sidecar** — Moonshine ASR from WhisperFlow, running locally

## Prerequisites

1. **Rust** 1.70+ — https://rustup.rs
2. **Node.js / Bun** — for the Next.js frontend
3. **Python 3.10+** — for the WhisperFlow sidecar (ASR)
4. **Moonshine Voice** — `pip install moonshine-voice` (27M Tiny, English-only, ONNX)

On Windows you also need the **MSVC C++ build tools** (Visual Studio Build Tools).

## Setup

```bash
# From the second-brain/ directory
bun install          # Next.js deps
bun run db:push      # create/migrate the SQLite DB
bun run dev          # start the Next.js dev server (port 3000)

# In another terminal, start the Python sidecar:
python src-tauri/binaries/whisper-sidecar.py --port 5001

# In a third terminal, build + run the Tauri app:
cd src-tauri
cargo tauri dev      # development (hot-reload)
# OR
cargo tauri build    # production .msi / .exe
```

## How it works

```
User presses Ctrl+Shift+B (anywhere on the system)
         │
         ▼
   Tauri Rust backend detects global hotkey
         │
         ▼
   Capture overlay window appears (borderless, always-on-top)
         │
         ▼
   Overlay records audio via MediaRecorder (web API)
         │
         ▼
   On stop: overlay calls Tauri command `capture_from_audio`
         │
         ▼
   Rust sends audio to Python sidecar (http://127.0.0.1:5001/transcribe)
         │
         ▼
   Sidecar transcribes via Moonshine ASR (local, no cloud)
         │
         ▼
   Rust POSTs transcript to http://localhost:3000/api/brain/capture
         │
         ▼
   Next.js classifies + tags + extracts tasks + stores in SQLite + writes .md
         │
         ▼
   Capture overlay shows "✓ Captured: [title]" → fades out
```

## File structure

```
src-tauri/
├── Cargo.toml              Rust dependencies
├── tauri.conf.json         Tauri config (windows, hotkeys, tray, bundle)
├── build.rs                Tauri build script
├── capabilities/
│   └── default.json        Plugin permissions
├── src/
│   ├── main.rs             Entry point (calls lib::run())
│   └── lib.rs              App logic (hotkeys, tray, capture flow, sidecar comms)
├── binaries/
│   └── whisper-sidecar.py  Python HTTP server wrapping Moonshine ASR
└── icons/                  App icons (add 32x32.png, 128x128.png, icon.ico, etc.)
```

## The Python sidecar

`binaries/whisper-sidecar.py` is a stdlib-only HTTP server that wraps
WhisperFlow's Moonshine ASR backend. It:

1. Listens on `127.0.0.1:5001`
2. Accepts `POST /transcribe` with a multipart `audio` file
3. Transcribes using Moonshine (local, no cloud, ~27M model)
4. Returns `{"transcript": "..."}`

It imports `whisper_flow.backends.moonshine` from the parent repo. If
Moonshine isn't installed, it falls back to `faster-whisper`.

## Status

This is a **scaffold** — the Rust code compiles on a machine with Rust
installed, but hasn't been compiled in the sandbox (no Rust toolchain).
The user should run `cargo tauri dev` on their Windows machine to build
and test the desktop app.

The capture overlay's frontend (`/capture` route) needs to be added to
the Next.js app — it should be a minimal page with a "Recording..." state,
a stop button, and logic to call the Tauri `capture_from_audio` command.
