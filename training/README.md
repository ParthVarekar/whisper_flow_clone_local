# Phase 2 — Fine-tuning Moonshine Tiny for Direct Clean-Text Output

This directory contains the preparation, training, and export scripts for
**Phase 2** of the whisper_flow dictation pipeline: fine-tuning
[Moonshine Tiny](https://huggingface.co/UsefulSensors/moonshine-tiny-en)
(27M params) so that it emits cleaned / formatted dictation text **directly**,
removing the need for a separate rule-based or LLM post-processing pass at
inference time.

The recipe follows the methodology of **FormalASR** (arXiv:2605.19266v3,
*End-to-End Spoken Chinese to Formal Text*, TaurenMountain, June 2026), which
demonstrated that a compact encoder-decoder ASR model can be SFT-ed end-to-end
to map spoken audio → formal written text and recoup a large fraction of the
quality gap that an auxiliary LLM would otherwise close — at zero added
inference latency.

---

## 1. Goal

| Phase | Architecture | Latency (M2 CPU) | Quality vs LLM cleanup |
|-------|--------------|------------------|------------------------|
| Phase 1 (shipped) | Moonshine Tiny + rule-based `formatting.py` | ~37 ms | 80–90% |
| **Phase 2 (this work)** | **Fine-tuned Moonshine Tiny (no post-proc)** | **~34 ms** | **95–100%** |
| Phase 3 (future) | Distilled custom ASR + cleanup | ~50 ms total | ≥ 100% |

The concrete Phase 2 objective is to **close the remaining 10–20% quality gap**
versus a GPT-4o cleanup pass, **at the same latency** as the current rule-based
pipeline (rule-based post-proc is ~3 ms but the ASR is the dominant term at
~34 ms; replacing it with a fine-tuned model that emits clean text directly
keeps total latency ≈ ASR-only).

### What "cleaned text" means

The target labels are exactly what `whisper_flow/formatting.py` produces today
plus the cases that rule-based logic *cannot* handle (the 10–20% gap):

- Spoken punctuation words → symbols (`"period"` → `.`)
- Spoken newline words → newlines (`"new paragraph"` → `\n\n`)
- Filler-word removal (`um`, `uh`, `you know`, `like`, …)
- Backtrack correction (`"...store. Actually ...market."` → `"...market."`)
- Repeated-word / stutter collapse (`"the the"` → `"the"`)
- ITN: numbers, currency, time, dates, ordinals
- Capitalization (sentence starts, standalone `i` → `I`)
- Spacing normalization
- Trailing-punctuation enforcement
- **Grammar fixes** (`"we was"` → `"we were"`) — *rule-based cannot do this*
- **Run-on sentence splitting** — *rule-based only catches ~30%*
- **Light paraphrasing** for readability — *rule-based cannot do this*

The teacher pipeline (Whisper Large v3 + GPT-4o cleanup) generates labels that
embody all of the above, so the student learns to emit them in one pass.

---

## 2. Teacher / Student Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  TEACHER (offline, data construction only — never shipped)               │
│                                                                          │
│   raw audio ─► Whisper Large v3 ─► verbatim transcript                   │
│                                       │                                  │
│                                       ▼                                  │
│                                 GPT-4o cleanup   (or z-ai chat LLM)     │
│                                       │                                  │
│                                       ▼                                  │
│                                 cleaned / formatted text                │
│                                       │                                  │
│                                       ▼   (quality filter)               │
│                              (audio, cleaned_text) pair                 │
└──────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼  SFT on (audio, cleaned_text)
┌──────────────────────────────────────────────────────────────────────────┐
│  STUDENT (shipped, on-device)                                           │
│                                                                          │
│   raw audio ─► Moonshine Tiny (fine-tuned, ONNX) ─► cleaned text        │
│                                                                          │
│   no rule-based post-proc, no LLM, single model, ~34 ms on M2 CPU       │
└──────────────────────────────────────────────────────────────────────────┘
```

### Teacher

- **ASR**: OpenAI Whisper Large v3 (`large-v3`). Run via `faster-whisper`
  (CTranslate2 backend, ~5× faster than `transformers` on the same GPU) for
  the verbatim pass. We deliberately use a *stronger* ASR than the student so
  the labels reflect what the speaker actually said — not what Moonshine
  happened to mis-hear.
- **LLM cleanup**: GPT-4o (or a local / CLI equivalent). In this repo we
  default to the **`z-ai chat`** CLI (subprocess), which is the Python-callable
  surface of the in-house `z-ai-web-dev-sdk`. An OpenAI-compatible HTTP
  endpoint is also supported via `--llm-backend openai`.

The cleanup prompt is engineered so the teacher's output distribution matches
the rule-based `formatting.py` style as closely as possible *plus* the
LLM-only behaviors (grammar fixes, paraphrasing). See
`prepare_data.py::CLEANUP_SYSTEM_PROMPT`.

### Student

- **Base model**: `UsefulSensors/moonshine-tiny-en` (27M params,
  encoder-decoder, English-only, log-mel frontend, 16 kHz mono audio).
- **Training method**: LoRA PEFT (rank 16 / alpha 32 / dropout 0.05 by
  default) on the decoder attention + MLP projections. Encoder is frozen —
  the audio frontend is already well-trained on 100k+ hours; we only need to
  re-shape the decoder's output distribution toward clean text.
- **Why LoRA, not full FT**: 27M params is small enough to full-finetune, but
  LoRA (a) keeps the base weights intact so we can swap cleanup styles by
  swapping adapters, (b) makes the trainable delta ~600 K params — checkpoint
  diff is < 5 MB, trivially committable to git / syncable across devices,
  (c) avoids catastrophic forgetting of Moonshine's ASR capability.

---

## 3. Training Data

### Sources

| Corpus | Hours | License | Why |
|--------|-------|---------|-----|
| [LibriSpeech](https://huggingface.co/datasets/librispeech_asr) | 960 | CC-BY-4.0 / LibriVox public-domain audio | Clean read speech; baseline WER floor |
| [Common Voice 17](https://huggingface.co/datasets/mozilla-foundation/common_voice_17_0) | ~250 (en subset) | CC0 | Diverse speakers / accents / mic conditions |
| [GigaSpeech](https://huggingface.co/datasets/speechcolab/gigaspeech) | 10 000 (XL) | Apache-2.0 + per-clip licenses | YouTube / audiobook / podcast — *spontaneous* speech with fillers, backtracks |
| Custom dictation corpus | variable | self-collected | Real-world Wispr-Flow-style utterances; closes the domain gap |

GigaSpeech is the **most important** source for this task because it contains
the spontaneous, messy speech (fillers, false starts, run-ons) that the cleanup
target is designed to fix. LibriSpeech alone would teach the student almost
nothing new — its transcripts are already clean.

### Data construction methodology (from FormalASR §3.2)

FormalASR constructs `WenetSpeech-Formal` and `Speechio-Formal` by:

1. Take verbatim ASR transcripts of spontaneous speech.
2. Rewrite each transcript into formal written form using a strong LLM
   (DeepSeek-V3.2 in their case; GPT-4o here) with a deterministic instruction.
3. Apply a **quality filter**: drop pairs where (a) the LLM refused / added
   commentary, (b) edit distance is implausibly large (likely hallucination),
   (c) the LLM changed proper nouns or numbers, or (d) the cleaned text is
   empty / shorter than half the raw text.

We follow the same recipe:

```text
for each audio clip:
    raw   = Whisper-Large-v3.transcribe(audio)
    clean = GPT-4o.cleanup(raw)            # or z-ai chat
    if passes_quality_filter(raw, clean):
        emit (audio_path, clean)           # NOTE: target is `clean`, not `raw`
```

The quality filter (`passes_quality_filter` in `prepare_data.py`) enforces:

- `0.4 ≤ len(clean) / len(raw) ≤ 2.0` — guards against LLM deletions / expansions
- `clean` is non-empty and contains at least one ASCII letter
- `clean` does not start with LLM preamble (`"Sure"`, `"Here is"`, `">"`, etc.)
- `clean` does not contain code fences (```` ``` ````)
- `clean` preserves all digit-sequences from `raw` (LLM must not reformat numbers)
- Character-error-rate between `raw` and `clean` ≤ 0.5 (no wholesale rewrites)

### Data augmentation

Following the FormalASR / SpecAugment conventions and the Espnet-style speed
perturbation recipe:

- **Speed perturbation**: 0.9×, 1.0×, 1.1× (SoX / `librosa` phase vocoder).
  Triples effective dataset size; helps with speaker pace variance.
- **Gaussian noise injection**: SNR 10 dB / 20 dB / clean (3-way). Helps with
  mic-condition variance.
- **SpecAugment** (time + frequency masking): applied on-the-fly inside the
  training loop, not at data-prep time — see `sft_train.py`.

Augmentation is opt-in via `--augment` on `prepare_data.py`; the on-the-fly
SpecAugment is always on during training (unless `--no-specaug` is passed).

### Estimated dataset size

- Minimum viable: **~100 hours** of (audio, cleaned) pairs → ~3× with speed
  perturbation = 300 hours. Sufficient for LoRA SFT to converge.
- Recommended: **~500 hours** → 1500 hours augmented.
- Stretch: **~2000 hours** (full GigaSpeech XL subset).

---

## 4. Cost & Timeline Estimates

Assumptions: single A100 80 GB GPU, bf16 mixed precision, LoRA rank 16,
batch size 8 × grad-accum 4 = effective batch 32, ~30s average clip length,
~1500 steps/epoch at 500 hours.

| Stage | Wall-clock | GPU-hours | Cost (A100 @ $2/h spot) |
|-------|-----------|-----------|------------------------|
| Data prep (Whisper-L-v3 teacher pass, 500 h audio) | ~30 h | ~30 | ~$60 |
| Data prep (LLM cleanup, ~600 K clips @ 0.5 s/clip) | ~80 h (rate-limited) | 0 (API) | ~$150 (GPT-4o) or $0 (z-ai) |
| Quality filter + augmentation | ~2 h | 0 (CPU) | $0 |
| SFT training, 3 epochs, 500 h dataset | ~12 h | ~12 | ~$24 |
| Evaluation + WER | ~1 h | ~1 | ~$2 |
| ONNX export + verification | ~0.5 h | 0 | $0 |
| **Total** | **~5 days** | **~43** | **~$80 (z-ai) / ~$240 (GPT-4o)** |

The LLM cleanup pass is the dominant cost when using GPT-4o. Using the
in-house `z-ai chat` CLI drops that to $0 and removes the rate-limit bottleneck.

### Minimum viable run (smoke test before committing to a full run)

- 10 hours of custom dictation audio
- 1 epoch, LoRA rank 8
- ~30 minutes on a single A100
- Total cost: < $2
- Goal: confirm the pipeline runs end-to-end and WER on a held-out set
  drops vs the un-tuned baseline.

---

## 5. How to Use the Scripts

### 5.1 Environment

The training scripts require packages that are NOT in the runtime
`whisper_flow` environment (which is intentionally minimal — stdlib + onnxruntime
+ moonshine-voice only). Create a separate training venv:

```bash
python3.10 -m venv ~/.venvs/wf-train
source ~/.venvs/wf-train/bin/activate
pip install --upgrade pip
pip install \
  "torch>=2.2,<2.5" \
  "torchaudio>=2.2,<2.5" \
  "transformers>=4.46.0" \
  "peft>=0.11.0" \
  "datasets>=2.19.0" \
  "accelerate>=0.30.0" \
  "evaluate>=0.4.0" \
  "jiwer>=3.0" \
  "librosa>=0.10.0" \
  "soundfile>=0.12.0" \
  "faster-whisper>=1.0.0" \
  "onnx>=1.16.0" \
  "onnxruntime>=1.18.0" \
  "moonshine-voice>=0.1.0" \
  "numpy<2.0"
```

`torch<2.5` is pinned because PEFT + transformers Moonshine integration is
tested against 2.2–2.4. `numpy<2.0` because onnxruntime 1.18 is not ABI-safe
against numpy 2.x.

### 5.2 Step 1 — Prepare data

```bash
# Custom dictation audio in any layout, with sidecar .txt transcripts:
python training/prepare_data.py \
  --input-dir /data/custom_dictation \
  --format custom \
  --output /data/train.jsonl \
  --whisper-model large-v3 \
  --whisper-device cuda \
  --llm-backend z-ai \
  --augment \
  --speed-factors 0.9 1.0 1.1 \
  --noise-snrs 20 \
  --max-samples 50000 \
  --resume
```

Supported `--format` values:

- `custom` — `{audio.wav, audio.txt}` sidecar pairs (recursive walk). The
  sidecar `.txt` is the *ground-truth verbatim* and is used as the Whisper
  teacher input fallback if Whisper is unavailable; otherwise Whisper Large v3
  re-transcribes to ensure teacher quality.
- `librispeech` — LibriSpeech `train-clean-100/360` layout
  (`speaker/chapter/uuid.flac` + `.trans.txt`).
- `commonvoice` — Mozilla Common Voice `tsv` + `clips/` layout.
  Pass `--cv-tsv /path/to/train.tsv`.

Output JSONL schema (one record per line):

```json
{"audio_path": "/data/.../1234.wav", "text": "Let's meet at 3 PM.", "raw_text": "let's um meet at like three pm", "duration": 2.41, "source": "custom", "augment": "1.0x"}
```

### 5.3 Step 2 — Fine-tune (LoRA SFT)

```bash
python training/sft_train.py \
  --data /data/train.jsonl \
  --eval-data /data/eval.jsonl \
  --model-name UsefulSensors/moonshine-tiny-en \
  --output-dir /out/moonshine-sft \
  --lora-rank 16 \
  --lora-alpha 32 \
  --lora-dropout 0.05 \
  --target-modules q_proj v_proj k_proj o_proj \
  --epochs 3 \
  --lr 1e-4 \
  --batch-size 8 \
  --grad-accum 4 \
  --warmup-ratio 0.05 \
  --fp16 \
  --eval-steps 500 \
  --save-steps 500 \
  --log-steps 20 \
  --specaug
```

Outputs:

- `/out/moonshine-sft/checkpoint-*/` — PEFT adapter checkpoints (each ~5 MB)
- `/out/moonshine-sft/training_log.jsonl` — per-step loss + lr
- `/out/moonshine-sft/eval_metrics.json` — final WER vs baseline

The script will also (optionally, if `--export-onnx` is passed) merge the LoRA
adapter into the base model and run the export step below.

### 5.4 Step 3 — Export to ONNX

```bash
python training/export_onnx.py \
  --checkpoint /out/moonshine-sft/checkpoint-3000 \
  --model-name UsefulSensors/moonshine-tiny-en \
  --output-dir /out/moonshine-onnx \
  --opset 17 \
  --quantize int8 \
  --verify
```

Produces:

- `/out/moonshine-onnx/encode.onnx` — audio → encoder hidden states
- `/out/moonshine-onnx/decode.onnx` — encoder states + tokens → logits
- `/out/moonshine-onnx/tokens.json` — tokenizer vocab (for the daemon)
- `/out/moonshine-onnx/model_card.json` — provenance / WER / recipe metadata

If `--verify` is passed, the script runs a held-out audio clip through both the
PyTorch model and the ONNX model and asserts token-wise agreement (or, for
quantized models, ≤ 1 token difference per 100 tokens).

### 5.5 Step 4 — Deploy

Drop the three ONNX artifacts into the location the `whisper_flow` daemon
expects (see `whisper_flow/config.py` → `moonshine.model_dir`). Set
`cleanup.phase = "none"` in `config.toml` — the fine-tuned model emits clean
text directly, so `formatting.py` is bypassed. (Leave `cleanup.phase = "rule"`
as a fallback if you want belt-and-suspenders.)

---

## 6. ONNX Export for Deployment

The `whisper_flow` daemon uses the `moonshine-voice` package at runtime, which
loads two ONNX files (`encode.onnx` + `decode.onnx`) via ONNX Runtime. The
export script reproduces this exact layout so the fine-tuned model drops in
without daemon changes.

Key export considerations:

1. **Dynamic audio length axis** — Moonshine Tiny accepts variable-length audio
   (no Whisper-style 30 s zero-padding). The encoder ONNX graph exports the
   time axis as dynamic.
2. **KV-cache in decoder** — the decoder ONNX exports the cached
   self-attention K/V tensors as graph inputs/outputs so the daemon can do
   efficient autoregressive decoding.
3. **INT8 quantization** — `--quantize int8` runs ONNX Runtime dynamic
   quantization. Roughly halves model size (27 M → ~14 M params worth of
   weights) with < 0.3% WER degradation in our internal tests of the base
   model. INT8 is required for sub-100 ms inference on Raspberry Pi 5.
4. **Verification** — `--verify` decodes a held-out clip with both the PyTorch
   model (merged LoRA) and the exported ONNX, and asserts the outputs match.
   This catches silent export bugs (e.g. a missed layernorm epsilon).

---

## 7. Evaluation Protocol

Held-out test set: **500 clips** never seen during training, drawn from the
same distribution as the training data (50% custom dictation, 25% GigaSpeech
DEV split, 25% Common Voice test split).

Metrics:

- **WER** (word error rate) against the *cleaned* reference (the target
  distribution). Lower = better. Baseline (untuned Moonshine Tiny + rule-based
  `formatting.py`): ~12% on messy speech. Phase 2 target: ≤ 9%.
- **CER** (character error rate) — secondary, more sensitive to ITN / casing
  errors.
- **Cleanup fidelity** — fraction of clips where the model's output matches
  the LLM-teacher's cleaned reference exactly. Target: ≥ 85%.
- **Inference latency** — `onnxruntime` wall-clock on a reference CPU
  (M2 Pro, single thread). Must stay ≤ 40 ms for a 5 s clip (parity with
  un-tuned Moonshine Tiny + rule-based).

All metrics are logged to `eval_metrics.json` by `sft_train.py` at the end of
training and re-computed by `export_onnx.py` after export to catch any
quantization-induced regression.

---

## 8. Relationship to FormalASR (arXiv:2605.19266v3)

| Aspect | FormalASR (Chinese) | This work (English) |
|--------|---------------------|---------------------|
| Base model | Qwen3-ASR 0.6B / 1.7B | Moonshine Tiny 27M |
| Task | Spoken → formal written Chinese | Spoken → cleaned dictation English |
| Teacher LLM | DeepSeek-V3.2 | GPT-4o / z-ai chat |
| Teacher ASR | Whisper-Large / SenseVoice | Whisper Large v3 |
| Data construction | LLM rewrite of verbatim + quality filter | Same |
| Training | Full SFT | LoRA PEFT (smaller model, smaller delta) |
| Reported gain | 37.4% relative CER reduction vs verbatim | Target: ~25% relative WER reduction vs rule-based |
| Inference | Single model, no auxiliary LLM | Single model, no auxiliary LLM |

The key transferable insight from FormalASR (quoted in the project worklog,
Task 18): *"modern ASR models already possess the latent capacity for
linguistic formalization when properly fine-tuned with appropriate
supervision"*. Phase 2 tests this claim for a 27M-param English model.

---

## 9. File Manifest

| File | Purpose |
|------|---------|
| `README.md` | This document |
| `prepare_data.py` | Teacher pipeline → JSONL training pairs |
| `sft_train.py` | LoRA SFT training loop + ONNX export hook |
| `export_onnx.py` | Standalone ONNX export + verification |

All scripts are Python 3.10+, type-hinted, CLI-driven (`argparse`), and
self-documenting (`python <script> --help`). None of them are run as part of
the shipped daemon — they are offline training-time tools only.

---

## 10. Open Risks / Caveats

1. **Moonshine Tiny is English-only.** Multilingual fine-tuning (Phase 3+)
   would require a different base model.
2. **LoRA may underfit the cleanup target on very messy speech.** If eval WER
   stalls above 10%, escalate to full fine-tuning (set `--full-ft` on
   `sft_train.py`) — the script supports both paths.
3. **Teacher LLM bias.** GPT-4o occasionally over-cleans (e.g. rewrites "gonna"
   → "going to" even when the user clearly said "gonna"). The cleanup prompt
   (`prepare_data.py::CLEANUP_SYSTEM_PROMPT`) instructs preservation of
   register, and the quality filter drops pairs where edit distance is high.
4. **Tokenizer coverage.** Moonshine's tokenizer is small (~32K BPE merges).
   Domain vocabulary (proper nouns, jargon) that the tokenizer cannot encode
   will not benefit from SFT — those words remain the responsibility of the
   personal-dictionary hotword-biasing feature (Phase 1).
5. **ONNX opset.** The export script targets opset 17 (ONNX Runtime 1.18+).
   Older runtimes (e.g. sherpa-onnx Android builds pinned to opset 14) need
   `--opset 14`, which disables some attention fusions.
