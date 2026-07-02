# Demo / Test path

This folder documents the two minimal demo flows required by the spec:
1. transcribe a **short audio file**, and
2. transcribe from the **microphone**.

These require the backends to be built and models downloaded first
(see the main `README.md` → Setup).

## 0. Prerequisites check

```bash
cd /path/to/whisper-flow
export PATH="$(pwd)/build/whisper/bin:$(pwd)/build/llama/bin:$PATH"
python -m whisper_flow check \
  --whisper-model "$(pwd)/models/ggml-base.en.bin" \
  --llm-model    "$(pwd)/models/gemma-3-1b-it-Q4_K_M.gguf"
```

All lines should read `[OK]`. `arecord` may show `MISSING (optional)` — that's
fine, the ffmpeg fallback will be used for mic capture.

## 1. Short audio file

The whisper.cpp repo ships sample WAVs at `third_party/whisper.cpp/samples/`.
If you ran `scripts/download_models.sh`, the clone is already there.

### 1a. Transcribe (STT only)

```bash
# Use a sample that ships with whisper.cpp
SAMPLE="third_party/whisper.cpp/samples/jfk.wav"

python -m whisper_flow transcribe \
  --whisper-model "$(pwd)/models/ggml-base.en.bin" \
  -f "$SAMPLE" --language en
```

Expected (approx):
```
And so my fellow Americans, ask not what your country can do for you,
ask what you can do for your country.
```

### 1b. Transcribe + LLM (summarize)

Start the LLM server in one terminal:

```bash
export PATH="$(pwd)/build/llama/bin:$PATH"
llama-server \
  -m "$(pwd)/models/gemma-3-1b-it-Q4_K_M.gguf" \
  --host 127.0.0.1 --port 8080 -c 2048
```

In another terminal:

```bash
python -m whisper_flow process \
  --whisper-model "$(pwd)/models/ggml-base.en.bin" \
  --llm-model    "$(pwd)/models/gemma-3-1b-it-Q4_K_M.gguf" \
  --llm-host 127.0.0.1 --llm-port 8080 \
  -f "$SAMPLE" --mode summarize --language en
```

### 1c. With timestamps (SRT/VTT/JSON)

```bash
python -m whisper_flow transcribe \
  --whisper-model "$(pwd)/models/ggml-base.en.bin" \
  -f "$SAMPLE" --language en --format srt
```

Or write files next to the source:

```bash
python -m whisper_flow transcribe \
  --whisper-model "$(pwd)/models/ggml-base.en.bin" \
  -f "$SAMPLE" --language en --format all --write-files
```

## 2. Microphone

```bash
# record 5 seconds from the default mic and transcribe (STT only)
python -m whisper_flow mic --duration 5 \
  --whisper-model "$(pwd)/models/ggml-base.en.bin" --language en
```

Speak during those 5 seconds. The transcript prints to stdout.

For continuous capture until Ctrl+C (ffmpeg backend only):

```bash
python -m whisper_flow mic --duration 0 \
  --whisper-model "$(pwd)/models/ggml-base.en.bin" --language en
# press Ctrl+C to stop and transcribe
```

If mic capture fails, check devices:

```bash
arecord -l                 # list ALSA capture devices
pactl list sources short   # list PulseAudio sources
```

Then point at a specific device, e.g.:

```bash
python -m whisper_flow mic --duration 5 \
  --mic-device plughw:CARD=USB,DEV=0 \
  --whisper-model "$(pwd)/models/ggml-base.en.bin"
```

## 3. Optional HTTP server

```bash
python -m whisper_flow serve --port 8090 \
  --whisper-model "$(pwd)/models/ggml-base.en.bin" \
  --llm-model    "$(pwd)/models/gemma-3-1b-it-Q4_K_M.gguf" \
  --llm-host 127.0.0.1 --llm-port 8080
```

Then:

```bash
# health
curl http://127.0.0.1:8090/health

# transcribe an upload
curl -s -F "audio=@$SAMPLE" http://127.0.0.1:8090/transcribe | jq

# transcribe + summarize
curl -s -F "audio=@$SAMPLE" -F "mode=summarize" http://127.0.0.1:8090/process | jq -r .processed

# process already-transcribed text
curl -s -X POST http://127.0.0.1:8090/transcribe/text \
  -H 'Content-Type: application/json' \
  -d '{"text":"remind me to ship the build at five","mode":"command"}' | jq
```
