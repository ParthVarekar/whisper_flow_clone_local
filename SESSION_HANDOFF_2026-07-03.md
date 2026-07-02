# WhisperFlow Clone Handoff

## User goal

Make this local `whisper_flow` app behave as closely as possible to the original Wispr Flow, at least functionally:

- explicit Start / Stop GUI
- indefinite microphone session instead of fixed-duration recording
- live visible transcription while speaking
- post-processing modes closer to Flow-style cleanup levels
- local llama.cpp model integration
- easy local testing

## Local paths and runtime

- Repo: `C:\Users\Parth\Desktop\whisper`
- llama.cpp folders:
  - `D:\llama4`
  - `D:\llama`
- Active llama server:
  - binary: `D:\llama4\llama-server.exe`
  - host: `127.0.0.1`
  - port: `8081`
  - model: `unsloth/gemma-4-E4B-it-GGUF:UD-Q4_K_XL`
- WhisperFlow HTTP server:
  - `http://127.0.0.1:8090`
  - `/health` returns OK

## Current config

File: [config.llama4.toml](C:/Users/Parth/Desktop/whisper/config.llama4.toml)

Important current values:

- `mode = "high"`
- `verbose = false`
- Whisper model: `C:\Users\Parth\Desktop\whisper\models\ggml-base.en.bin`
- Whisper binary: `C:\Users\Parth\Desktop\whisper\third_party\whisper.cpp-bin\whisper-bin-x64\Release\whisper-cli.exe`
- Mic backend: `sounddevice`
- `stream_chunk_s = 1`
- `stream_max_s = 12`
- llama host/port: `127.0.0.1:8081`

## What changed

### 1. Live mic session overhaul

- Added `LiveMicCapture` in [whisper_flow/audio.py](C:/Users/Parth/Desktop/whisper/whisper_flow/audio.py)
- `duration 0` is now a real Start/Stop session
- Live mode keeps a rolling preview window for low-latency transcript updates
- Stop triggers one final full-session transcription pass for better quality

### 2. GUI rebuild

Replaced the old notifier GUI in [whisper_flow/notifier.py](C:/Users/Parth/Desktop/whisper/whisper_flow/notifier.py):

- Start button
- Stop button
- Cancel button
- live transcript pane
- separate output pane
- mode dropdown
- microphone picker
- writing style picker
- timer
- meter
- progress bar
- save/copy/close actions

### 3. Flow-style cleanup levels

Added mode aliases and behavior closer to Wispr Flow:

- `none` -> raw transcript
- `light` -> gentle cleanup
- `medium` -> cleaner and more concise rewrite
- `high` -> strongest cleanup / polish

Still supported:

- `summarize`
- `command`
- `assistant`
- legacy `raw`, `correct`, `polish`

### 4. Smart Formatting / Backtrack layer

Added a lightweight local formatting layer in [whisper_flow/formatting.py](C:/Users/Parth/Desktop/whisper/whisper_flow/formatting.py) and wired it through the pipeline:

- spoken punctuation like `comma`, `period`, `question mark`
- `new line` / `new paragraph`
- `press enter`
- simple backtrack markers like `actually`, `i mean`, `scratch that`
- writing style shaping:
  - `default`
  - `casual`
  - `very_casual`
  - `formal`

This is now applied before the LLM post-processing stage.

Changed files:

- [whisper_flow/prompts.py](C:/Users/Parth/Desktop/whisper/whisper_flow/prompts.py)
- [whisper_flow/pipeline.py](C:/Users/Parth/Desktop/whisper/whisper_flow/pipeline.py)
- [whisper_flow/cli.py](C:/Users/Parth/Desktop/whisper/whisper_flow/cli.py)
- [whisper_flow/server.py](C:/Users/Parth/Desktop/whisper/whisper_flow/server.py)
- [whisper_flow/config.py](C:/Users/Parth/Desktop/whisper/whisper_flow/config.py)

## Validation already done

- `python -m py_compile ...` passed for edited modules
- `python -m pytest C:\Users\Parth\Desktop\whisper\tests -q` passed
- `python -m whisper_flow check --config config.llama4.toml` passed
- `http://127.0.0.1:8090/health` OK
- `http://127.0.0.1:8081/health` OK
- direct smoke checks for Smart Formatting behavior passed in Python

## What is currently running

At handoff time:

- `llama-server` listening on `8081`
- local `whisper_flow` HTTP server listening on `8090`
- a visible PowerShell session was launched to run:

```powershell
python -m whisper_flow process --config config.llama4.toml --mic --duration 0 --gui
```

## Known remaining risk

The code is validated and the services are up, but the newest rebuilt GUI/live path still needs human interactive confirmation on this exact machine:

- does live preview text appear quickly enough while speaking?
- does Stop feel natural?
- is the transcript quality good enough with `base.en`?
- are the `none/light/medium/high` levels satisfying in real use?
- does the new mic picker behave correctly across all Windows devices?
- does the writing style control feel close enough to real Wispr Flow output?

If quality is still weak, the next likely upgrades are:

1. use a stronger Whisper model than `ggml-base.en.bin`
2. tune rolling window / stabilization thresholds
3. add richer Flow-like transforms beyond cleanup levels
4. add interactive transform prompts / diff-style compare behavior closer to Wispr Flow transforms

## Original user pain points that drove the overhaul

- timed recording was not acceptable
- there was no proper Start / Stop
- GUI felt broken and unclear
- no visible live transcription while speaking
- user wanted something functionally very close to original Wispr Flow
- user asked for a summary that another session could continue from
