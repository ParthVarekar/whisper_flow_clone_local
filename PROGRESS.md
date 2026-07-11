# WhisperFlow — Progress & Decisions Log

**Last updated:** July 12, 2026
**Purpose:** Track what works, what doesn't, and why. Prevent repeating mistakes.

---

## Current Working State ✅

**Config:** `config.llama4.toml`
- `backend = "qwen3_asr"` — Qwen3-ASR 1.7B via CrispASR
- `mode = "auto"` — LLM cleanup ON (gemma-4 via llama-server, port 8081)
- `language = "auto"` — auto-detect (NOT forced to "en")
- `flash_attention = false`

**CrispASR command (working):**
```
crispasr.exe -m <model> -l auto -t 8 -bs 5 -nt -np --hotwords <vocabulary> <audio.wav>
```
- `-bs 5` — beam search (HELPS, do not remove)
- `--hotwords` — vocabulary biasing (HELPS proper noun recognition, do not remove)

**LLM cleanup:** mode="auto" uses llama-server + gemma-4-E4B-it for post-processing.
The LLM polishes the raw Qwen3-ASR transcript — fixes grammar, removes fillers,
formats text. This was the key to good output quality.

**Audio preprocessing:** NONE. The `audio_preprocess.py` module was an experiment
that added silence trimming, noise gate, high-pass filter, normalization.
It did NOT improve accuracy and added ~50ms latency. Currently disabled.

**Live preview:** ENABLED. Shows partial transcript in the overlay during recording.
Works well with Qwen3-ASR because it gives visual feedback while speaking.

---

## History — What We Tried and Learned

### Phase 0: Original Qwen3-ASR setup (WORKED WELL) ✅
**Commits:** `cd84c79` through `a530e9e`
- Backend: Qwen3-ASR 1.7B via CrispASR
- Mode: "auto" (LLM cleanup with gemma-4)
- Language: "auto"
- Beam search: `-bs 5`
- Hotwords: `--hotwords <vocabulary>`
- **Result:** Captured proper nouns well, good accuracy
- **Why it worked:** LLM cleanup polished the raw transcript + hotwords helped
  with vocabulary + beam search produced coherent output

### Phase 1: Moonshine experiment (FAILED) ❌
**Commits:** `ae2b89b` through `f4d0918`
- Switched to Moonshine Tiny (27M) for sub-100ms latency
- Disabled LLM cleanup (mode="raw")
- **Result:** Complete hallucinations — transcripts unrelated to actual speech
- **Root cause:** Moonshine Tiny (27M) too small for desktop/distant mic
- **Lesson:** Speed is meaningless if output is garbage. 27M models can't handle
  real-world mic conditions.

### Phase 2: whisper.cpp base.en (FAILED) ❌
**Commit:** `c3d7b08`
- Switched to whisper.cpp with ggml-base.en (74M)
- **Result:** Still garbled — "test 3" → "destitute", "pronunciation" → "constitution"
- **Root cause:** 74M still too small for this mic setup
- **Lesson:** Need at least 1B+ params for accurate desktop mic transcription

### Phase 3: Back to Qwen3-ASR but WITHOUT LLM cleanup (PARTIAL) ⚠️
**Commits:** `6310878` through `7b5d53d`
- Switched back to Qwen3-ASR (good)
- BUT kept mode="raw" (NO LLM cleanup) — missing the polishing step
- Removed `-bs 5` and `--hotwords` (MISTAKE — these were helping)
- Added audio preprocessing (silence trim, noise gate, filter, normalize)
- **Result:** Better than Moonshine/base.en but worse than the original
- **Root cause:** No LLM cleanup + no hotwords + no beam search = raw, unpolished output
- **Lesson:** The LLM cleanup step was doing heavy lifting. Removing it degraded quality.

### Phase 4: Revert to working state (CURRENT) ✅
**Commit:** (this commit)
- Reverted config to match the working `a530e9e` state:
  - mode = "auto" (LLM cleanup ON)
  - language = "auto"
  - Restored `-bs 5` and `--hotwords` in qwen3_asr.py
- Removed audio preprocessing (not needed, adds latency)
- Re-enabled live preview
- **Expected result:** Back to the quality level the user had before Moonshine

---

## Architecture (Current)

```
User holds Ctrl+Shift+Space
  → LiveMicCapture (sounddevice, 16kHz mono int16)
  → Live preview loop (transcribes rolling window, shows in overlay)
User releases Ctrl+Shift+Space
  → snapshot_full() → WAV file
  → Qwen3AsrBackend.transcribe()
      → crispasr.exe -m <model> -l auto -t 8 -bs 5 -nt -np --hotwords <vocab> <audio>
      → raw transcript
  → apply_smart_formatting() (rule-based: fillers, ITN, capitalization, etc.)
  → LLM cleanup (mode="auto"):
      → llama-server (gemma-4-E4B-it, port 8081) polishes transcript
      → graceful fallback to raw if LLM server is down
  → insert_text() (Windows clipboard + SendInput Ctrl+V)
  → save_dictation() (history log)
```

**Latency:** ~1-2s (Qwen3-ASR) + ~1-2s (LLM cleanup) = ~2-4s total
This is slower than Moonshine's theoretical 50ms, but the output is actually correct.

---

## Dependencies (requirements.txt)

```
moonshine-voice    # ASR backend option (in-process ONNX, 27M — too small for desktop mic)
sounddevice        # Mic capture (PortAudio binding)
pynput             # Global hotkeys (Ctrl+Shift+Space) — CRITICAL
pystray            # System tray icon
Pillow             # Image support for tray icon
numpy              # Audio arrays
pyperclip          # Clipboard fallback
tomli              # TOML config (Python <3.11 only)
```

For Qwen3-ASR: needs `crispasr.exe` + `qwen3-asr-1.7b-q4_k.gguf` (not pip — separate install)
For LLM cleanup: needs `llama-server.exe` + `gemma-4-E4B-it.gguf` (not pip — separate install)

---

## Key Decisions (LOCKED)

1. **Qwen3-ASR is the ASR backend.** Do not switch to Moonshine or small Whisper models.
   They cannot handle desktop/distant mic capture. 1.7B+ params required.

2. **LLM cleanup (mode="auto") is REQUIRED for good output.** The raw Qwen3-ASR transcript
   is decent but has errors. The LLM (gemma-4) polishes it into clean, formatted text.
   Do not set mode="raw" unless you have a specific reason and accept lower quality.

3. **Beam search (`-bs 5`) and hotwords (`--hotwords`) HELP.** Do not remove them.
   They were incorrectly identified as bugs in Phase 3 — they were actually helping.

4. **Audio preprocessing is NOT needed.** The silence trim + noise gate + filter + normalize
   pipeline did not improve accuracy and added latency. Qwen3-ASR handles noise well enough
   on its own.

5. **Live preview is useful.** It gives visual feedback during recording. Keep it enabled.

---

## How to Run

```powershell
cd C:\Users\Parth\Desktop\whisper
git pull origin main

# Start llama-server first (for LLM cleanup):
D:\llama4\llama-server.exe -hf unsloth/gemma-4-E4B-it-GGUF:UD-Q4_K_XL --host 127.0.0.1 --port 8081 --ctx-size 32768 --n-gpu-layers 999 --parallel 2 --alias gemma-4-e4b-it --reasoning off --reasoning-budget 0

# Then start WhisperFlow:
start.bat
```

If llama-server is not running, the daemon falls back to raw transcript (mode="auto"
with graceful LLM fallback). Output will be lower quality but still functional.

---

## Future Work (NOT started — evaluate before doing)

- **Phase 2 fine-tuning:** `training/` has scripts to fine-tune Moonshine Tiny on
  cleaned text. This would let us use the fast 27M model with good quality.
  Requires GPU + training data. NOT a priority — current setup works.

- **Whisper medium.en (769M):** Could try as an alternative to Qwen3-ASR.
  Would need to download the model (~500MB). NOT a priority — Qwen3-ASR works.

- **Mobile (Phase 3):** sherpa-onnx for Android, CoreML for iOS. NOT started.
  Current focus is desktop.

---

## Commit History (key commits)

| Commit | Description | Verdict |
|---|---|---|
| `a530e9e` | Last commit before Moonshine — WORKING STATE | ✅ Reference |
| `ae2b89b` | Moonshine experiment started | ❌ Failed |
| `899df96` | Fixed Moonshine empty transcription bug | Good fix, wrong backend |
| `4f92b51` | Phase 1 rule-based cleanup (66 tests) | ✅ Keep (formatting.py is good) |
| `c3d7b08` | Switch to whisper.cpp base.en | ❌ Failed (too small) |
| `6310878` | Switch to Qwen3-ASR (good) but mode="raw" (bad) | ⚠️ Partial |
| `7b5d53d` | Removed -bs 5 and --hotwords (MISTAKE) | ❌ Reverted |
| `(this)` | Revert to working config + document | ✅ Current |
