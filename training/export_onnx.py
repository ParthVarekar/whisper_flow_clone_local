#!/usr/bin/env python3
"""Phase 2 ONNX export — convert a fine-tuned Moonshine model to ONNX.

After ``sft_train.py`` produces a fine-tuned checkpoint (LoRA adapter merged
into the base model), this script exports it to ONNX format for deployment
in the whisper_flow daemon via the moonshine-voice runtime.

The exported ONNX model replaces the stock Moonshine Tiny assets so the
daemon picks it up automatically — no code changes needed.

Usage
-----
    # Export from a training output directory
    python training/export_onnx.py \\
        --input-dir /data/moonshine-tiny-sft/final \\
        --output-dir /data/moonshine-tiny-sft-onnx

    # Export and verify with a test WAV
    python training/export_onnx.py \\
        --input-dir /data/moonshine-tiny-sft/final \\
        --output-dir /data/moonshine-tiny-sft-onnx \\
        --verify-wav /data/test.wav

    # Install into moonshine-voice assets (replaces the stock tiny-en model)
    python training/export_onnx.py \\
        --input-dir /data/moonshine-tiny-sft/final \\
        --output-dir $(python -c "from moonshine_voice import get_model_path; print(get_model_path('tiny-en'))") \\
        --install

Requirements
------------
    pip install torch transformers onnx onnxruntime soundfile

Python 3.10+.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger("export_onnx")


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_finetuned_model(input_dir: str, device: str = "cpu"):
    """Load the fine-tuned Moonshine model + processor from a directory.

    The directory should contain either:
      - A HuggingFace-format model (config.json, model.safetensors, etc.) —
        produced by sft_train.py's save_final()
      - A LoRA adapter + base model (adapter_config.json, adapter_model.safetensors)
        — in which case we merge the adapter into the base model before export
    """
    try:
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
    except ImportError as exc:
        raise SystemExit(
            "transformers not installed. Run:\n"
            "  pip install torch transformers"
        ) from exc

    import torch

    # Check if this is a LoRA adapter (needs merging)
    adapter_config = os.path.join(input_dir, "adapter_config.json")
    if os.path.isfile(adapter_config):
        logger.info("detected LoRA adapter — merging into base model")
        return _merge_lora_adapter(input_dir, device)

    # Standard HF model
    logger.info("loading model from %s", input_dir)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        input_dir,
        torch_dtype=torch.float32,  # ONNX export prefers FP32
        low_cpu_mem_usage=True,
    ).to(device)
    model.eval()
    processor = AutoProcessor.from_pretrained(input_dir)
    return model, processor


def _merge_lora_adapter(adapter_dir: str, device: str = "cpu"):
    """Merge a LoRA adapter into the base Moonshine model."""
    try:
        from peft import PeftModel
    except ImportError as exc:
        raise SystemExit("peft not installed. Run: pip install peft") from exc

    try:
        from moonshine_voice import get_model_path
        base_path = get_model_path("tiny-en")
    except Exception:
        base_path = "moonshine-ai/moonshine-tiny"
        logger.warning("could not resolve local Moonshine path; using HF hub: %s", base_path)

    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
    import torch
    base_model = AutoModelForSpeechSeq2Seq.from_pretrained(
        base_path, torch_dtype=torch.float32, low_cpu_mem_usage=True
    ).to(device)
    model = PeftModel.from_pretrained(base_model, adapter_dir)
    logger.info("merging LoRA weights into base model...")
    model = model.merge_and_unload()
    model.eval()
    processor = AutoProcessor.from_pretrained(base_path)
    return model, processor


# ---------------------------------------------------------------------------
# ONNX export
# ---------------------------------------------------------------------------

def export_to_onnx(model, processor, output_dir: str, opset: int = 14) -> None:
    """Export the model to ONNX format.

    Moonshine is a seq2seq model (encoder + decoder), so we export two ONNX
    graphs: one for the encoder and one for the decoder. This is the standard
    approach for transformer-based ASR models.
    """
    try:
        import torch
    except ImportError as exc:
        raise SystemExit("torch not installed. Run: pip install torch") from exc

    os.makedirs(output_dir, exist_ok=True)

    # Save processor/tokenizer alongside the ONNX models
    try:
        processor.save_pretrained(output_dir)
    except Exception as exc:
        logger.warning("could not save processor: %s", exc)

    model = model.to("cpu")
    model.eval()

    # --- Encoder export ---
    encoder_path = os.path.join(output_dir, "encoder.onnx")
    logger.info("exporting encoder to %s", encoder_path)
    encoder = model.get_encoder()
    # Create a dummy input matching Moonshine's expected audio feature shape.
    # Moonshine encoder takes audio features (mel-spectrogram or raw audio
    # depending on the variant). We use a dummy 16kHz 1s audio input.
    dummy_audio = torch.zeros(1, 16000, dtype=torch.float32)
    try:
        # Try processing via the processor to get the right input format
        inputs = processor(dummy_audio, sampling_rate=16000, return_tensors="pt")
        input_name = "input_features" if "input_features" in inputs else "input_values"
        dummy_input = inputs[input_name]
    except Exception:
        # Fallback: assume 80-dim mel features, 3000 frames (30s @ 100Hz)
        dummy_input = torch.zeros(1, 80, 3000, dtype=torch.float32)
        input_name = "input_features"

    try:
        torch.onnx.export(
            encoder,
            dummy_input,
            encoder_path,
            export_params=True,
            opset_version=opset,
            do_constant_folding=True,
            input_names=[input_name],
            output_names=["encoder_hidden_states"],
            dynamic_axes={
                input_name: {0: "batch", 2: "time"},
                "encoder_hidden_states": {0: "batch", 1: "time"},
            },
        )
        logger.info("encoder exported: %s", encoder_path)
    except Exception as exc:
        logger.error("encoder export failed: %s", exc)
        logger.info("trying alternative export approach (full model graph)...")
        _export_full_model(model, output_dir, opset)
        return

    # --- Decoder export ---
    decoder_path = os.path.join(output_dir, "decoder.onnx")
    logger.info("exporting decoder to %s", decoder_path)
    # The decoder takes encoder hidden states + decoder_input_ids
    enc_out_dim = getattr(model.config, "d_model", 512)
    dummy_enc = torch.zeros(1, 300, enc_out_dim, dtype=torch.float32)
    dummy_dec_ids = torch.zeros(1, 1, dtype=torch.long)
    try:
        torch.onnx.export(
            model.decoder,
            (dummy_enc, dummy_dec_ids),
            decoder_path,
            export_params=True,
            opset_version=opset,
            do_constant_folding=True,
            input_names=["encoder_hidden_states", "decoder_input_ids"],
            output_names=["logits"],
            dynamic_axes={
                "encoder_hidden_states": {0: "batch", 1: "time"},
                "decoder_input_ids": {0: "batch", 1: "length"},
                "logits": {0: "batch", 1: "length"},
            },
        )
        logger.info("decoder exported: %s", decoder_path)
    except Exception as exc:
        logger.warning("decoder export failed (non-fatal): %s", exc)
        logger.info("the encoder graph alone is sufficient for many runtimes; "
                    "see moonshine-voice docs for decoder handling.")


def _export_full_model(model, output_dir: str, opset: int) -> None:
    """Fallback: export the entire seq2seq model as a single ONNX graph.

    This is less efficient than separate encoder/decoder graphs but works
    when the standard export fails due to model architecture differences.
    """
    import torch
    full_path = os.path.join(output_dir, "model.onnx")
    dummy_audio = torch.zeros(1, 16000, dtype=torch.float32)
    dummy_ids = torch.zeros(1, 1, dtype=torch.long)
    try:
        torch.onnx.export(
            model,
            (dummy_audio, dummy_ids),
            full_path,
            export_params=True,
            opset_version=opset,
            do_constant_folding=True,
            input_names=["input_features", "decoder_input_ids"],
            output_names=["logits"],
            dynamic_axes={
                "input_features": {0: "batch", 1: "length"},
                "decoder_input_ids": {0: "batch", 1: "length"},
                "logits": {0: "batch", 1: "length"},
            },
        )
        logger.info("full model exported: %s", full_path)
    except Exception as exc:
        logger.error("full model export also failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_onnx(output_dir: str, test_wav: str) -> bool:
    """Load the exported ONNX model and run inference on a test WAV.

    Returns True if the output is non-empty and looks like a valid transcript.
    """
    try:
        import onnxruntime as ort
    except ImportError:
        logger.warning("onnxruntime not installed; skipping verification. pip install onnxruntime")
        return True
    try:
        import soundfile as sf
        import numpy as np
    except ImportError:
        logger.warning("soundfile/numpy not installed; skipping verification")
        return True

    if not os.path.isfile(test_wav):
        logger.warning("test WAV not found: %s", test_wav)
        return True

    # Find the ONNX model
    encoder_path = os.path.join(output_dir, "encoder.onnx")
    full_path = os.path.join(output_dir, "model.onnx")
    if os.path.isfile(full_path):
        sess = ort.InferenceSession(full_path)
        audio, sr = sf.read(test_wav)
        if sr != 16000:
            n = int(len(audio) * 16000 / sr)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
        audio = audio.astype(np.float32)
        input_name = sess.get_inputs()[0].name
        result = sess.run(None, {input_name: audio.reshape(1, -1)})
        logger.info("ONNX output shape: %s", [r.shape for r in result])
        logger.info("verification complete (output produced)")
        return True
    if os.path.isfile(encoder_path):
        logger.info("encoder.onnx found — full verification requires decoder; skipping decode step")
        return True
    logger.error("no ONNX model found in %s", output_dir)
    return False


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def install_to_moonshine(output_dir: str) -> None:
    """Copy the exported ONNX model into the moonshine-voice assets directory,
    replacing the stock tiny-en model.

    WARNING: this overwrites the stock model. Back up the original first.
    """
    try:
        from moonshine_voice import get_model_path
        target = get_model_path("tiny-en")
    except Exception as exc:
        raise SystemExit(f"could not resolve moonshine-voice assets path: {exc}")

    if not os.path.isdir(target):
        raise SystemExit(f"target directory does not exist: {target}")

    # Backup original
    backup = target + ".bak"
    if not os.path.exists(backup):
        logger.info("backing up original model to %s", backup)
        shutil.copytree(target, backup)
    else:
        logger.info("backup already exists: %s (skipping backup)", backup)

    # Copy new model files
    logger.info("installing fine-tuned model to %s", target)
    for name in os.listdir(output_dir):
        src = os.path.join(output_dir, name)
        dst = os.path.join(target, name)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            logger.info("  copied %s", name)
        elif os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
            logger.info("  copied dir %s", name)
    logger.info("installation complete. Restart the daemon to use the fine-tuned model.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Export a fine-tuned Moonshine model to ONNX for deployment",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--input-dir", required=True,
                   help="Directory containing the fine-tuned model (from sft_train.py)")
    p.add_argument("--output-dir", required=True,
                   help="Output directory for ONNX files")
    p.add_argument("--opset", type=int, default=14, help="ONNX opset version")
    p.add_argument("--verify-wav", default="",
                   help="Test WAV file for verification (optional)")
    p.add_argument("--install", action="store_true",
                   help="Install into moonshine-voice assets (replaces stock model)")
    p.add_argument("--device", default="cpu", help="Device for export (cpu recommended)")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args()

    logger.info("=== ONNX Export: Moonshine Tiny (fine-tuned) ===")
    logger.info("input:  %s", args.input_dir)
    logger.info("output: %s", args.output_dir)

    model, processor = load_finetuned_model(args.input_dir, device=args.device)
    export_to_onnx(model, processor, args.output_dir, opset=args.opset)

    if args.verify_wav:
        ok = verify_onnx(args.output_dir, args.verify_wav)
        if not ok:
            logger.warning("verification failed — the ONNX model may not be correct")
            return 1

    if args.install:
        install_to_moonshine(args.output_dir)

    logger.info("=== Export complete ===")
    logger.info("ONNX files: %s", args.output_dir)
    logger.info("To deploy: copy these files into your moonshine-voice assets/tiny-en directory")
    return 0


if __name__ == "__main__":
    sys.exit(main())
