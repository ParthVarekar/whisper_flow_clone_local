"""Moonshine ASR backend — ultra-fast single-model speech recognition.

Uses Moonshine Tiny (27M params, ONNX Runtime) for sub-50ms transcription.
This is the recommended ASR backend for sub-100ms pipeline targets.

No external binaries needed — Moonshine runs in-process via ONNX Runtime.
No GPU required — runs on CPU with NEON/AVX2 optimizations.
Mobile-ready — same ONNX models work on Android/iOS via sherpa-onnx.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from ..config import TranscriptionConfig
from ..errors import BinaryNotFoundError, ModelNotFoundError, TranscriptionError
from .base import ProgressFn, Segment, SegmentFn, TranscriptionBackend, TranscriptionResult


class MoonshineBackend(TranscriptionBackend):
    """Moonshine ASR backend (ONNX Runtime, in-process, no subprocess)."""

    name = "moonshine"

    def __init__(self, cfg: TranscriptionConfig, *, verbose: bool = False):
        self.cfg = cfg
        self.verbose = verbose
        self._transcriber = None
        self._cancel_requested = False

    def check(self) -> None:
        try:
            from moonshine_voice import Transcriber, get_model_path, ModelArch
        except ImportError as exc:
            raise BinaryNotFoundError(
                "moonshine-voice",
                "pip install moonshine-voice",
            ) from exc

        # Model is bundled with the package — no separate download needed
        # (unlike whisper.cpp which requires manual ggml model download)
        try:
            get_model_path("tiny-en")
        except Exception as exc:
            raise ModelNotFoundError("", kind="Moonshine model") from exc

    def cancel(self) -> None:
        self._cancel_requested = True

    def _build_transcriber(self):
        if self._transcriber is not None:
            return self._transcriber
        from moonshine_voice import Transcriber, get_model_path, ModelArch
        model_path = get_model_path("tiny-en")
        self._transcriber = Transcriber(
            model_path=model_path,
            model_arch=ModelArch.TINY,
            update_interval=0.5,
        )
        return self._transcriber

    def transcribe(self, audio_path: str, *, language: str = "auto",
                   on_progress: Optional[ProgressFn] = None,
                   on_segment: Optional[SegmentFn] = None) -> TranscriptionResult:
        from moonshine_voice import load_wav_file

        self.check()
        if not os.path.isfile(audio_path):
            raise TranscriptionError(f"audio file not found: {audio_path!r}")

        self._cancel_requested = False
        transcriber = self._build_transcriber()

        # Load audio
        t0 = time.perf_counter()
        audio_data, sample_rate = load_wav_file(audio_path)
        load_ms = (time.perf_counter() - t0) * 1000

        if self.verbose:
            print(f"[moonshine] audio loaded: {len(audio_data)} samples, {load_ms:.1f}ms", flush=True)

        # Transcribe
        t1 = time.perf_counter()
        try:
            transcript = transcriber.transcribe_without_streaming(audio_data, sample_rate)
        except Exception as exc:
            raise TranscriptionError(f"Moonshine transcription failed: {exc}") from exc
        infer_ms = (time.perf_counter() - t1) * 1000

        if self.verbose:
            print(f"[moonshine] inference: {infer_ms:.1f}ms", flush=True)

        if self._cancel_requested:
            from ..errors import CancelledError
            raise CancelledError("transcription cancelled by user")

        # Extract text
        text = ""
        if hasattr(transcript, "text"):
            text = transcript.text.strip()
        elif isinstance(transcript, str):
            text = transcript.strip()

        # Build segments (Moonshine may provide word timings)
        segments: list[Segment] = []
        if hasattr(transcript, "lines") and transcript.lines:
            for line in transcript.lines:
                seg_text = getattr(line, "text", "").strip()
                if not seg_text:
                    continue
                start_ms = int(getattr(line, "start", 0) * 1000) if hasattr(line, "start") else 0
                end_ms = int(getattr(line, "end", 0) * 1000) if hasattr(line, "end") else 0
                segments.append(Segment(
                    text=seg_text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    language="en",
                ))
        elif text:
            segments.append(Segment(text=text, start_ms=0, end_ms=0, language="en"))

        # Call segment callback
        if on_segment and segments:
            for seg in segments:
                try:
                    on_segment(seg.text, f"{seg.start_ms/1000:.3f} --> {seg.end_ms/1000:.3f}")
                except Exception:
                    pass

        if on_progress:
            try:
                on_progress(100, "")
            except Exception:
                pass

        return TranscriptionResult(
            text=text,
            segments=segments,
            language="en",
            raw={"backend": "moonshine", "inference_ms": infer_ms, "load_ms": load_ms},
        )

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[moonshine] {msg}", flush=True)
