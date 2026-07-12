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
                   initial_prompt: str = "",
                   on_progress: Optional[ProgressFn] = None,
                   on_segment: Optional[SegmentFn] = None) -> TranscriptionResult:
        from moonshine_voice import load_wav_file

        self.check()
        if not os.path.isfile(audio_path):
            raise TranscriptionError(f"audio file not found: {audio_path!r}")

        self._cancel_requested = False
        transcriber = self._build_transcriber()

        # NOTE: Moonshine Tiny is English-only ("tiny-en"), so the `language`
        # and `initial_prompt` (acoustic biasing) parameters are accepted for
        # interface compatibility with the TranscriptionBackend ABC but are
        # intentionally ignored — Moonshine has no concept of prompt biasing
        # and always outputs English text.

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

        # Extract text and segments from the Transcript object.
        #
        # moonshine-voice API (verified against installed package):
        #   Transcript is a dataclass with a single field: `lines: List[TranscriptLine]`
        #   It does NOT have a `.text` attribute (previous code checked hasattr(transcript, "text")
        #   which was always False → text stayed empty → "empty transcript" error).
        #
        #   TranscriptLine is a dataclass with fields:
        #     text: str
        #     start_time: float    (seconds from start of audio)
        #     duration: float      (seconds)
        #     is_complete: bool
        #     words: Optional[List[WordTiming]]
        #     ... (other metadata)
        #
        #   There is NO `start` or `end` attribute — previous code used getattr(line, "start"/"end")
        #   which silently returned 0 for every segment.
        text = ""
        segments: list[Segment] = []

        if isinstance(transcript, str):
            # Defensive: some future version might return a plain string
            text = transcript.strip()
            if text:
                segments.append(Segment(text=text, start_ms=0, end_ms=0, language="en"))
        elif transcript is not None and hasattr(transcript, "lines"):
            line_texts: list[str] = []
            for line in transcript.lines:
                seg_text = getattr(line, "text", "").strip()
                if not seg_text:
                    continue
                line_texts.append(seg_text)
                # Convert start_time (seconds) + duration (seconds) → ms
                start_time_s = float(getattr(line, "start_time", 0.0) or 0.0)
                duration_s = float(getattr(line, "duration", 0.0) or 0.0)
                start_ms = int(start_time_s * 1000)
                end_ms = int((start_time_s + duration_s) * 1000)
                segments.append(Segment(
                    text=seg_text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    language="en",
                ))
            text = " ".join(line_texts).strip()
        elif transcript is not None and hasattr(transcript, "text"):
            # Defensive: if a future moonshine-voice version adds .text
            text = transcript.text.strip() if transcript.text else ""
            if text:
                segments.append(Segment(text=text, start_ms=0, end_ms=0, language="en"))

        if self.verbose:
            print(f"[moonshine] lines: {len(segments)}, text: {text!r}", flush=True)

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
