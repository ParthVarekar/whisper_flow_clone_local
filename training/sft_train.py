#!/usr/bin/env python3
"""Phase 2 supervised fine-tuning — LoRA SFT for Moonshine Tiny.

Fine-tunes Moonshine Tiny (27M params) on ``(audio, cleaned_text)`` pairs
produced by ``prepare_data.py``, following the FormalASR recipe
(arXiv:2605.19266v3). The student model learns to emit cleaned / formatted
text directly, eliminating the need for rule-based post-processing.

Key design choices
------------------
- **LoRA / PEFT**: only adapter weights are trained (~1-2% of params), so
  training fits in 8-12 GB VRAM on a single consumer GPU. Full fine-tuning
  is supported via ``--full-finetune`` but requires ~40 GB VRAM.
- **Streaming dataset**: the JSONL from prepare_data.py is streamed so
  corpora of arbitrary size can be used without OOM.
- **Dynamic audio loading**: audio is loaded and resampled on-the-fly by the
  data collator, so no preprocessing step is needed.
- **CER/WER eval**: a held-out validation set is evaluated every
  ``--eval-steps`` and the best checkpoint is kept.
- **ONNX export**: after training, the adapter is merged into the base model
  and exported to ONNX for deployment via the moonshine-voice runtime
  (see ``export_onnx.py``).

Usage
-----
    # LoRA fine-tuning (recommended, fits in 12 GB VRAM)
    python training/sft_train.py \\
        --train-jsonl /data/train.jsonl \\
        --val-jsonl /data/val.jsonl \\
        --output-dir /data/moonshine-tiny-sft \\
        --base-model tiny-en \\
        --lora-rank 16 \\
        --lora-alpha 32 \\
        --learning-rate 1e-4 \\
        --num-epochs 3 \\
        --batch-size 4 \\
        --grad-accum 4

    # Full fine-tuning (requires ~40 GB VRAM)
    python training/sft_train.py \\
        --train-jsonl /data/train.jsonl \\
        --val-jsonl /data/val.jsonl \\
        --output-dir /data/moonshine-tiny-sft-full \\
        --full-finetune \\
        --learning-rate 5e-5 \\
        --num-epochs 3

Requirements
------------
    pip install torch transformers peft accelerate datasets soundfile
    pip install jiwer  # for WER/CER evaluation

Python 3.10+. GPU strongly recommended (CUDA or MPS).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("sft_train")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@dataclass
class TrainingExample:
    """One (audio_path, cleaned_text) pair with metadata."""
    audio_path: str
    text: str
    raw_text: str = ""
    duration: float = 0.0
    source: str = ""
    augment: str = ""

    @classmethod
    def from_jsonl_line(cls, line: str) -> "TrainingExample":
        d = json.loads(line)
        return cls(
            audio_path=d["audio_path"],
            text=d["text"],
            raw_text=d.get("raw_text", ""),
            duration=d.get("duration", 0.0),
            source=d.get("source", ""),
            augment=d.get("augment", ""),
        )


def stream_jsonl(path: str):
    """Yield TrainingExample objects from a JSONL file (memory-efficient)."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield TrainingExample.from_jsonl_line(line)
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("skipping malformed JSONL line: %s", exc)


def count_jsonl(path: str) -> int:
    """Count records in a JSONL file (for progress bars)."""
    if not path or not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_base_model(model_name: str, device: str = "cuda"):
    """Load the Moonshine Tiny base model and processor.

    Uses the moonshine-voice package to locate the model, then loads it via
    HuggingFace Transformers for fine-tuning. Falls back to loading from the
    HuggingFace hub if the local moonshine-voice assets are not available.
    """
    try:
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
    except ImportError as exc:
        raise SystemExit(
            "transformers not installed. Run:\n"
            "  pip install torch transformers peft accelerate soundfile"
        ) from exc

    # Resolve model path via moonshine-voice (local assets) or HF hub.
    model_path: str
    try:
        from moonshine_voice import get_model_path
        model_path = get_model_path(model_name)
        logger.info("loaded Moonshine model from local assets: %s", model_path)
    except Exception:
        # Fall back to HF hub (useful when moonshine-voice isn't installed
        # on the training machine).
        hf_id = "moonshine-ai/moonshine-tiny" if model_name == "tiny-en" else model_name
        model_path = hf_id
        logger.info("loading Moonshine model from HF hub: %s", model_path)

    import torch
    dtype = torch.float16 if device != "cpu" else torch.float32
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        model_path,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True,
    ).to(device)
    processor = AutoProcessor.from_pretrained(model_path)
    return model, processor


# ---------------------------------------------------------------------------
# PEFT / LoRA
# ---------------------------------------------------------------------------

def attach_lora(
    model,
    rank: int = 16,
    alpha: int = 32,
    dropout: float = 0.05,
    target_modules: Optional[list[str]] = None,
):
    """Attach LoRA adapters to the model for parameter-efficient fine-tuning.

    Default target modules cover the attention projections in Moonshine's
    encoder/decoder transformer blocks. Only ~1-2% of params are trainable.
    """
    try:
        from peft import LoraConfig, get_peft_model, TaskType
    except ImportError as exc:
        raise SystemExit(
            "peft not installed. Run:\n  pip install peft"
        ) from exc

    if target_modules is None:
        # Moonshine uses standard transformer attention names. These cover
        # q_proj, k_proj, v_proj, o_proj in most Whisper/Moonshine variants.
        target_modules = ["q_proj", "k_proj", "v_proj", "o_proj"]

    config = LoraConfig(
        r=rank,
        lora_alpha=alpha,
        lora_dropout=dropout,
        target_modules=target_modules,
        task_type=TaskType.SEQ_2_SEQ_LM,
    )
    model = get_peft_model(model, config)
    model.print_trainable_parameters()
    return model


# ---------------------------------------------------------------------------
# Data collator
# ---------------------------------------------------------------------------

@dataclass
class DataCollator:
    """Batch examples: load audio, tokenize text, pad.

    Audio is loaded on-the-fly using soundfile (or librosa fallback) and
    resampled to 16 kHz mono — the format Moonshine expects.
    """
    processor: Any
    device: str = "cuda"
    sampling_rate: int = 16000

    def __call__(self, batch: list[TrainingExample]) -> dict:
        import numpy as np
        try:
            import soundfile as sf
            read_fn = sf.read
        except ImportError:
            import librosa
            read_fn = lambda p: librosa.load(p, sr=self.sampling_rate)

        audio_arrays: list[np.ndarray] = []
        texts: list[str] = []
        for ex in batch:
            try:
                audio, sr = read_fn(ex.audio_path)
                if sr != self.sampling_rate:
                    import numpy as np
                    # Simple linear resample (good enough for training; for
                    # production use librosa.resample)
                    if sr > 0:
                        n = int(len(audio) * self.sampling_rate / sr)
                        indices = np.linspace(0, len(audio) - 1, n)
                        audio = np.interp(indices, np.arange(len(audio)), audio)
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)  # mono
                audio_arrays.append(audio.astype(np.float32))
                texts.append(ex.text)
            except Exception as exc:
                logger.warning("failed to load %s: %s", ex.audio_path, exc)

        if not audio_arrays:
            return {}

        # Process audio + text via the Moonshine processor
        inputs = self.processor(
            audio_arrays,
            sampling_rate=self.sampling_rate,
            text=texts,
            return_tensors="pt",
            padding=True,
        )
        # Move to device
        import torch
        result = {k: v.to(self.device) if isinstance(v, torch.Tensor) else v
                  for k, v in inputs.items()}
        # Labels for seq2seq: the processor puts text in "labels" when text= is given
        if "labels" not in result and "input_ids" in result:
            # Some processors use input_ids for the target; copy to labels
            result["labels"] = result["input_ids"].clone()
        return result


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def compute_cer_wer(references: list[str], hypotheses: list[str]) -> dict:
    """Compute Character Error Rate and Word Error Rate."""
    if not references:
        return {"cer": 1.0, "wer": 1.0}
    try:
        from jiwer import cer as jiwer_cer, wer as jiwer_wer
        cer_val = float(jiwer_cer(references, hypotheses))
        wer_val = float(jiwer_wer(references, hypotheses))
    except ImportError:
        # Fallback: simple CER without jiwer
        cer_val = _simple_cer(references, hypotheses)
        wer_val = _simple_wer(references, hypotheses)
        logger.warning("jiwer not installed; using simple CER/WER. pip install jiwer for accuracy.")
    return {"cer": cer_val, "wer": wer_val}


def _simple_cer(refs: list[str], hyps: list[str]) -> float:
    """Simple character-level edit distance / reference length."""
    total_edits = 0
    total_chars = 0
    for ref, hyp in zip(refs, hyps):
        total_edits += _edit_distance(ref, hyp)
        total_chars += len(ref)
    return total_edits / max(total_chars, 1)


def _simple_wer(refs: list[str], hyps: list[str]) -> float:
    """Simple word-level edit distance / reference word count."""
    total_edits = 0
    total_words = 0
    for ref, hyp in zip(refs, hyps):
        ref_words = ref.split()
        hyp_words = hyp.split()
        total_edits += _edit_distance(ref_words, hyp_words)
        total_words += len(ref_words)
    return total_edits / max(total_words, 1)


def _edit_distance(a, b) -> int:
    """Levenshtein edit distance (works on sequences of any hashable items)."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            ins = prev[j + 1] + 1
            dele = curr[j] + 1
            sub = prev[j] + (ca != cb)
            curr.append(min(ins, dele, sub))
        prev = curr
    return prev[-1]


def evaluate(model, processor, val_jsonl: str, device: str, max_samples: int = 200) -> dict:
    """Run inference on the validation set and compute CER/WER."""
    import torch
    model.eval()
    refs: list[str] = []
    hyps: list[str] = []
    collator = DataCollator(processor=processor, device=device)
    batch: list[TrainingExample] = []
    for ex in stream_jsonl(val_jsonl):
        batch.append(ex)
        if len(batch) >= 1:
            inputs = collator(batch)
            if not inputs:
                batch = []
                continue
            with torch.no_grad():
                gen = model.generate(
                    inputs.get("input_features") or inputs.get("input_values"),
                    max_length=256,
                    num_beams=1,
                )
            decoded = processor.batch_decode(gen, skip_special_tokens=True)
            for ex_ref, hyp in zip(batch, decoded):
                refs.append(ex_ref.text)
                hyps.append(hyp.strip())
            batch = []
            if len(refs) >= max_samples:
                break
    metrics = compute_cer_wer(refs, hyps)
    metrics["num_samples"] = len(refs)
    return metrics


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def train(args: argparse.Namespace) -> None:
    import torch
    from torch.optim import AdamW

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger.info("=== Phase 2 SFT: Moonshine Tiny ===")
    logger.info("train_jsonl:  %s", args.train_jsonl)
    logger.info("val_jsonl:    %s", args.val_jsonl)
    logger.info("output_dir:   %s", args.output_dir)

    os.makedirs(args.output_dir, exist_ok=True)

    # Load model + processor
    model, processor = load_base_model(args.base_model, device=args.device)
    if not args.full_finetune:
        model = attach_lora(
            model,
            rank=args.lora_rank,
            alpha=args.lora_alpha,
            dropout=args.lora_dropout,
            target_modules=args.lora_target_modules.split(",") if args.lora_target_modules else None,
        )

    # Optimizer
    trainable_params = [p for p in model.parameters() if p.requires_grad]
    optimizer = AdamW(trainable_params, lr=args.learning_rate, weight_decay=args.weight_decay)

    # Data collator
    collator = DataCollator(processor=processor, device=args.device)

    # Count training examples
    n_train = count_jsonl(args.train_jsonl)
    n_val = count_jsonl(args.val_jsonl)
    total_steps = (n_train // (args.batch_size * args.grad_accum)) * args.num_epochs
    logger.info("train examples: %d, val examples: %d", n_train, n_val)
    logger.info("total optimization steps (approx): %d", total_steps)

    # Training loop
    best_cer = float("inf")
    step = 0
    model.train()
    start_time = time.time()

    for epoch in range(args.num_epochs):
        logger.info("=== Epoch %d/%d ===", epoch + 1, args.num_epochs)
        batch: list[TrainingExample] = []
        accum_loss = 0.0

        for ex in stream_jsonl(args.train_jsonl):
            batch.append(ex)
            if len(batch) < args.batch_size:
                continue

            inputs = collator(batch)
            batch = []
            if not inputs:
                continue

            try:
                outputs = model(**{k: v for k, v in inputs.items()
                                   if k in ("input_features", "input_values", "labels",
                                            "attention_mask", "decoder_input_ids")})
                loss = outputs.loss / args.grad_accum
                loss.backward()
                accum_loss += loss.item()
            except Exception as exc:
                logger.warning("forward/backward failed: %s", exc)
                continue

            step += 1
            if step % args.grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(trainable_params, args.max_grad_norm)
                optimizer.step()
                optimizer.zero_grad()

                if step % args.log_steps == 0:
                    elapsed = time.time() - start_time
                    avg_loss = accum_loss / args.log_steps
                    logger.info(
                        "step %d | loss %.4f | elapsed %.0fs | %.1f steps/s",
                        step, avg_loss, elapsed, step / max(elapsed, 1),
                    )
                    accum_loss = 0.0

                # Periodic evaluation
                if step % args.eval_steps == 0 and args.val_jsonl:
                    metrics = evaluate(model, processor, args.val_jsonl, args.device, args.eval_samples)
                    logger.info("eval @ step %d: CER=%.4f WER=%.4f (%d samples)",
                                step, metrics["cer"], metrics["wer"], metrics["num_samples"])
                    if metrics["cer"] < best_cer:
                        best_cer = metrics["cer"]
                        save_checkpoint(model, processor, args.output_dir, step, epoch, is_best=True)
                        logger.info("new best CER=%.4f — saved best checkpoint", best_cer)
                    model.train()

        # End-of-epoch checkpoint
        save_checkpoint(model, processor, args.output_dir, step, epoch, is_best=False)

    # Final evaluation
    if args.val_jsonl:
        metrics = evaluate(model, processor, args.val_jsonl, args.device, args.eval_samples)
        logger.info("final eval: CER=%.4f WER=%.4f", metrics["cer"], metrics["wer"])

    # Save final model
    save_final(model, processor, args.output_dir)
    logger.info("=== Training complete. Output: %s ===", args.output_dir)
    logger.info("Next step: python training/export_onnx.py --input-dir %s", args.output_dir)


def save_checkpoint(model, processor, output_dir: str, step: int, epoch: int, is_best: bool) -> None:
    """Save a training checkpoint (adapter weights for LoRA, full weights for FT)."""
    ckpt_dir = os.path.join(output_dir, f"checkpoint-{step}")
    os.makedirs(ckpt_dir, exist_ok=True)
    try:
        if hasattr(model, "save_pretrained"):
            model.save_pretrained(ckpt_dir)
        else:
            import torch
            torch.save(model.state_dict(), os.path.join(ckpt_dir, "model.pt"))
    except Exception as exc:
        logger.warning("failed to save checkpoint: %s", exc)
    # Save metadata
    with open(os.path.join(ckpt_dir, "metadata.json"), "w") as f:
        json.dump({"step": step, "epoch": epoch, "is_best": is_best}, f, indent=2)
    if is_best:
        best_link = os.path.join(output_dir, "best")
        if os.path.islink(best_link) or os.path.exists(best_link):
            try:
                os.remove(best_link)
            except OSError:
                pass
        try:
            os.symlink(os.path.basename(ckpt_dir), best_link)
        except OSError:
            pass


def save_final(model, processor, output_dir: str) -> None:
    """Save the final model + processor."""
    final_dir = os.path.join(output_dir, "final")
    os.makedirs(final_dir, exist_ok=True)
    try:
        if hasattr(model, "save_pretrained"):
            model.save_pretrained(final_dir)
        processor.save_pretrained(final_dir)
    except Exception as exc:
        logger.warning("failed to save final model: %s", exc)
        import torch
        torch.save(model.state_dict(), os.path.join(final_dir, "model.pt"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 2 SFT: fine-tune Moonshine Tiny on (audio, cleaned_text) pairs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Data
    p.add_argument("--train-jsonl", required=True, help="Path to training JSONL (from prepare_data.py)")
    p.add_argument("--val-jsonl", default="", help="Path to validation JSONL")
    p.add_argument("--output-dir", required=True, help="Output directory for checkpoints")
    # Model
    p.add_argument("--base-model", default="tiny-en", help="Moonshine model name (tiny-en, base, etc.)")
    p.add_argument("--full-finetune", action="store_true", help="Full fine-tune (no LoRA). Needs ~40 GB VRAM.")
    # LoRA
    p.add_argument("--lora-rank", type=int, default=16, help="LoRA rank")
    p.add_argument("--lora-alpha", type=int, default=32, help="LoRA alpha")
    p.add_argument("--lora-dropout", type=float, default=0.05, help="LoRA dropout")
    p.add_argument("--lora-target-modules", default="",
                   help="Comma-separated LoRA target modules (default: q,k,v,o projections)")
    # Training
    p.add_argument("--num-epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--grad-accum", type=int, default=4, help="Gradient accumulation steps")
    p.add_argument("--learning-rate", type=float, default=1e-4)
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--max-grad-norm", type=float, default=1.0)
    # Eval / logging
    p.add_argument("--eval-steps", type=int, default=500)
    p.add_argument("--eval-samples", type=int, default=200)
    p.add_argument("--log-steps", type=int, default=20)
    # Device
    p.add_argument("--device", default="cuda", help="cuda | mps | cpu")
    return p.parse_args()


if __name__ == "__main__":
    train(parse_args())
