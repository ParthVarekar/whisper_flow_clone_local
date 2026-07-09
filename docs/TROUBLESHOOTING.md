# Troubleshooting

## "required binary not found: 'whisper-cli'" (exit 3)

`whisper-cli` isn't on your PATH. Either:
- Build whisper.cpp and add its build dir to PATH:
  ```bash
  ./scripts/build.sh
  export PATH="$(pwd)/build/whisper/bin:$PATH"
  ```
- Or point config at the binary directly:
  ```bash
  python -m whisper_flow transcribe -f audio.wav --whisper-bin /path/to/whisper-cli ...
  ```

## "required binary not found: 'llama-server'" (exit 3)

Same as above but for llama.cpp:
```bash
export PATH="$(pwd)/build/llama/bin:$PATH"
```

## "model file not found" (exit 4)

Run the model download script:
```bash
./scripts/download_models.sh            # whisper + LLM
./scripts/download_models.sh --vad      # + Silero VAD
```
Or pick a discovered model interactively:
```bash
python -m whisper_flow models --select whisper
```

## "cannot reach llama-server at http://127.0.0.1:8080" (exit 7)

`llama-server` isn't running. Start it in a separate terminal:
```bash
llama-server -m models/gemma-3-1b-it-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8080 -c 2048
```
Verify: `curl http://127.0.0.1:8080/health`.

## "empty transcript — audio may be silent" (exit 6)

whisper-cli exited successfully but produced no segments. Causes:
- Audio is silent or too quiet → check the recording level.
- Wrong language → try `--language en` (or the correct code).
- Audio is music/noise, not speech.
- VAD is too aggressive → raise `--vad-threshold` (e.g. 0.6) or disable VAD.

## "ffmpeg mic capture failed" / "arecord failed"

- **Linux**: list devices with `arecord -l` (ALSA) or `pactl list sources short` (PulseAudio).
  Then `--mic-device plughw:CARD=USB,DEV=0`.
- **macOS**: `ffmpeg -f avfoundation -list_devices true -i ""` to list; default is `:default`.
- **Windows**: `python -m whisper_flow list-devices` to enumerate DirectShow devices,
  then `--mic-device "Microphone (Your Device)"`.

## "sounddevice backend selected but the package is not installed"

You set `--mic-backend sounddevice` without installing it:
```bash
pip install sounddevice  # or: pip install whisper-flow[mic]
```

## Out of memory (OOM) on large models

- Use a smaller Whisper model (`base` instead of `large-v3`).
- Use a smaller / more-quantized GGUF (`Q4_K_M` instead of `F16`).
- Run Whisper and the LLM at different times (not concurrently).
- See [PERFORMANCE.md](PERFORMANCE.md) for RAM estimates.

## GUI window doesn't appear

- Check that `$DISPLAY` is set (Linux): `echo $DISPLAY`.
- Install Tkinter: `sudo apt install python3-tk` (Debian/Ubuntu).
- Force GUI: `--gui`.
- Or use headless mode: `--no-gui` (console progress to stderr).

## Cancel button doesn't stop immediately

The Cancel button sends SIGTERM to whisper-cli, which finishes the current
encoder chunk (1-3 s) before exiting. This is expected — a hard SIGKILL would
corrupt the partial output. The window stays open until the subprocess exits.

## "unknown config key" error

You used a config key that doesn't exist. Run `python -m whisper_flow check`
to see all recognized keys, or check `config.example.json` / `config.example.toml`.

## TOML config not applying top-level keys

In TOML, top-level keys (like `mode = "summarize"`) must come BEFORE any
`[section]` header, otherwise they're absorbed into the preceding section.
See `config.example.toml` for the correct order.
