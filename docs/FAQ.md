# FAQ

**Q: Does whisper-flow send any audio or text to the cloud?**
A: No. The orchestrator is Python stdlib-only. The only network calls are to
`127.0.0.1` (your local `llama-server`). Model downloads (in
`scripts/download_models.sh`) fetch from HuggingFace, but that's a one-time
setup step, not runtime.

**Q: Can I use a different STT backend (e.g. faster-whisper, openai-whisper)?**
A: Not built-in, but the `TranscriptionBackend` ABC (`backends/base.py`) makes
it easy to add. Implement `check()` and `transcribe()`, then wire it into the
pipeline. The whisper.cpp backend is ~60 lines.

**Q: Can I use a different LLM backend (e.g. ollama, llama-cpp-python)?**
A: Same answer — implement the `LLMBackend` ABC. The llama.cpp backend uses
HTTP to `llama-server`, so any OpenAI-compatible local server works with
minimal changes.

**Q: Why subprocess instead of Python bindings (llama-cpp-python)?**
A: Two reasons: (1) keeps the orchestrator stdlib-only (no torch/ctypes/ABI
risk); (2) uses the official CMake-built binaries verbatim. Tradeoff: two
processes to manage (documented in the README).

**Q: Does it stream tokens in real time like Otter.ai?**
A: No. whisper.cpp emits closed segments (one per sentence-ish), not
token-by-token. This is a limitation of whisper-cli's subprocess interface;
token streaming would require linking libwhisper (a C extension), which breaks
the stdlib-only design. See `ARCHITECTURE.md` for architectural design details.

**Q: Why is the GUI Tkinter and not PyQt/Electron?**
A: Tkinter is part of the Python standard library with zero external pip GUI
dependencies. Using Win32 `-transparentcolor` window attributes and custom
`ctypes` DPI-awareness, WhisperFlow creates an ultra-sleek, borderless,
Apple-style floating capsule HUD near the cursor (`overlay.py`) without the
heavy 50+ MB memory footprint of Electron or PyQt.

**Q: How do I run it headless / in CI?**
A: `--no-gui` prints progress to stderr. The HTTP server (`serve` subcommand)
is always headless. In Docker, the CMD uses `--no-gui` by default.

**Q: How do I cancel a running transcription?**
A: Click the Cancel button in the GUI, or press Ctrl+C in the terminal. Both
send SIGTERM to whisper-cli (graceful, finishes the current chunk in 1-3s),
then SIGKILL after 5s if needed. Partial output is recovered when possible.

**Q: Why are there two model formats (ggml .bin and GGUF)?**
A: whisper.cpp uses ggml `.bin` for Whisper models (historical). llama.cpp uses
GGUF for LLMs (the modern format). Don't confuse them — a Whisper `.bin` won't
load in llama.cpp and vice versa.

**Q: Can I transcribe in [language X]?**
A: Yes, if your Whisper model supports it. Use `--language <code>` (e.g. `fr`,
`de`, `ja`). `--language auto` lets Whisper detect. The `large-v3` model
supports 90+ languages; `base.en` / `small.en` are English-only.

**Q: How do I add a new LLM post-processing mode?**
A: Add it to `whisper_flow/prompts.py` (`SYSTEM_PROMPTS` + `USER_TEMPLATES` +
`VALID_MODES`), then pass `--mode yourmode`. No other code changes needed.

**Q: Is it production-ready?**
A: Yes. The orchestrator (`daemon.py`, `pipeline.py`, `overlay.py`) is fully
typed, structured, and tested across 170+ automated tests. The C++ backends
(`whisper.cpp`, `llama.cpp`) run with hardware acceleration (e.g., CUDA / RTX GPU)
to deliver fast, reliable local dictation. See `ARCHITECTURE.md` for complete details.
