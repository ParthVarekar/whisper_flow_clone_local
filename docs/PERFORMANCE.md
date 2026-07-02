# Performance tuning

## Model size vs RAM

| Component | Model | Size on disk | RAM (approx) |
|---|---|---|---|
| Whisper | tiny / tiny.en | 75 MB | ~0.5 GB |
| Whisper | base / base.en | 142 MB | ~0.7 GB |
| Whisper | small / small.en | 466 MB | ~1.2 GB |
| Whisper | medium / medium.en | 1.5 GB | ~2.5 GB |
| Whisper | large-v3 | 2.9 GB | ~4 GB |
| Whisper | large-v3-turbo | 1.5 GB | ~2.5 GB |
| Silero VAD | silero-v6.2.0 | 864 KB | ~50 MB |
| LLM (GGUF) | gemma-3-1b-it Q4_K_M | ~1 GB | ~1.5 GB |
| LLM (GGUF) | llama-3-8b Q4_K_M | ~5 GB | ~6 GB |

**Total RAM** ≈ Whisper RAM + LLM RAM + ~200 MB overhead. For a laptop with
8 GB RAM, `base.en` + `gemma-3-1b-it-Q4_K_M` fits comfortably in ~2.5 GB.

## GPU acceleration

GPU is chosen at **build time** for both backends (no runtime flag).

### whisper.cpp
```bash
GPU_BACKEND=cuda   ./scripts/build.sh   # NVIDIA
GPU_BACKEND=vulkan ./scripts/build.sh   # cross-vendor
GPU_BACKEND=rocm   ./scripts/build.sh   # AMD
# Metal is automatic on Apple Silicon (no flag needed)
```

### llama.cpp (LLM stage)
GPU offload is a runtime flag: `--gpu-layers N` (or `-ngl N` in llama-server).
- `0` = CPU only
- `N > 0` = offload N layers to GPU
- `-1` = offload all layers

```bash
llama-server -m model.gguf --host 127.0.0.1 --port 8080 -ngl 99 -c 2048
```

## Thread tuning

- `--threads N` sets whisper.cpp's CPU threads. Default 4.
- For CPU-only, set to your physical core count (not hyperthread count).
- For GPU builds, threads matter less (GPU does the heavy lifting); 4 is fine.

## Chunking long audio

whisper.cpp processes audio in 30-second windows internally, so chunking is
OFF by default. For extremely long recordings (>1 hour), pre-chunking lets
each chunk fail independently and enables parallel processing:

```bash
python -m whisper_flow transcribe -f long.mp3 --chunk-seconds 600 ...
```

## VAD for faster mic transcription

Enabling Silero VAD skips silence before transcription, speeding up mic
captures with long pauses:

```bash
python -m whisper_flow mic --duration 30 --vad --vad-model models/ggml-silero-v6.2.0.bin ...
```

## Benchmarking

Measure your setup:
```bash
python -m whisper_flow bench -f sample.wav --out benchmarks/ \
  --whisper-model models/ggml-base.en.bin
cat benchmarks/benchmark.md
```

Key metrics:
- **realtime factor** > 1.0 = faster than realtime (good)
- **approx tokens/sec** = LLM throughput (char-proxy)

## LLM context length

`-c 2048` (default) is enough for most transcripts. For very long transcripts
+ summarization, raise to `-c 4096` or `-c 8192` (uses more RAM).
