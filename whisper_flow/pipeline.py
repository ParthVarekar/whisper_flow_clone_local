"""Pipeline orchestration: audio -> transcription -> (optional) LLM.

Keeps the transcription and LLM backends as separable dependencies. The
pipeline only knows about their abstract interfaces, so either can be swapped
without touching this module.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import TYPE_CHECKING, Optional

import threading

from .audio import capture_mic, chunk_audio, normalize_file, stop_active_capture, stream_mic_chunks
from .backends import LlamaCppBackend, Segment, TranscriptionResult, WhisperCppBackend
from .config import Config
from .notifier import Notifier, NullNotifier
from .prompts import build_prompt

if TYPE_CHECKING:
    from .benchmark import Benchmark


def _fmt_timestamp(ms: int) -> str:
    """Format milliseconds as HH:MM:SS,mmm (SRT/VTT style)."""
    if ms < 0:
        ms = 0
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_timestamp_vtt(ms: int) -> str:
    return _fmt_timestamp(ms).replace(",", ".")


def segments_to_srt(segments: list[Segment]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_timestamp(seg.start_ms)} --> {_fmt_timestamp(seg.end_ms)}")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines)


def segments_to_vtt(segments: list[Segment]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_fmt_timestamp_vtt(seg.start_ms)} --> {_fmt_timestamp_vtt(seg.end_ms)}")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines)


def result_to_json(result: TranscriptionResult) -> str:
    return json.dumps(
        {
            "text": result.text,
            "language": result.language,
            "segments": [asdict(s) for s in result.segments],
        },
        ensure_ascii=False,
        indent=2,
    )


def write_outputs(result: TranscriptionResult, cfg: Config, *, source_name: str) -> list[str]:
    """Write transcript files per cfg.output.format. Returns list of written paths."""
    written = []
    if not cfg.output.write_files:
        return written
    out_dir = cfg.output.out_dir or os.path.dirname(source_name) or "."
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(source_name))[0] or "transcript"
    fmt = cfg.output.format
    formats = ["txt", "json", "srt", "vtt"] if fmt == "all" else [fmt]
    for f in formats:
        path = os.path.join(out_dir, f"{base}.{f}")
        if f == "txt":
            content = result.text
        elif f == "json":
            content = result_to_json(result)
        elif f == "srt":
            content = segments_to_srt(result.segments)
        elif f == "vtt":
            content = segments_to_vtt(result.segments)
        else:
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content + ("\n" if not content.endswith("\n") else ""))
        written.append(path)
    return written


class Pipeline:
    def __init__(self, cfg: Config, notifier: Optional[Notifier] = None,
                 benchmark: Optional[Benchmark] = None):
        self.cfg = cfg
        # default to a quiet headless notifier so callers that don't care
        # still get nothing printed (use NullNotifier(verbose=True) for logs)
        self.notifier: Notifier = notifier if notifier is not None else NullNotifier()
        self.benchmark: Optional[Benchmark] = benchmark
        self._stt: Optional[WhisperCppBackend] = None
        self._llm: Optional[LlamaCppBackend] = None
        self._recording = False
        self._awaiting_start = False
        self._cancel_requested = False
        self._start_evt: Optional[threading.Event] = None
        self._stream_stop_evt: Optional[threading.Event] = None
        # wire the Cancel button to the STT backend's cancel() (registered lazily
        # once the backend is instantiated; safe to call before transcribe).
        self.notifier.register_cancel(self._cancel)
        if hasattr(self.notifier, "register_start"):
            try:
                self.notifier.register_start(self._start)
            except Exception:  # noqa: BLE001
                pass

    @property
    def stt(self) -> WhisperCppBackend:
        if self._stt is None:
            self._stt = WhisperCppBackend(self.cfg.transcription, verbose=self.cfg.verbose)
        return self._stt

    @property
    def llm(self) -> LlamaCppBackend:
        if self._llm is None:
            self._llm = LlamaCppBackend(self.cfg.llm, verbose=self.cfg.verbose)
        return self._llm

    # -- notifier glue -------------------------------------------------------

    def _cancel(self) -> None:
        """GUI button handler.

        During microphone recording, treat the action as "stop recording and
        continue". During transcription, fall back to a true cancel.
        """
        if self._recording:
            if self._stream_stop_evt is not None:
                self._stream_stop_evt.set()
            stop_active_capture()
            return
        if self._awaiting_start:
            self._cancel_requested = True
            if self._start_evt is not None:
                self._start_evt.set()
            return
        if self._stt is not None:
            try:
                self._stt.cancel()
            except Exception:  # noqa: BLE001
                pass

    def _start(self) -> None:
        if self._start_evt is not None:
            self._start_evt.set()

    def _wait_for_manual_start(self) -> None:
        """For GUI indefinite-mic sessions, wait for a Start button click."""
        if not hasattr(self.notifier, "register_start"):
            return
        self._start_evt = threading.Event()
        self._awaiting_start = True
        self._cancel_requested = False
        self.notifier.stage("Ready to record", "click Start")
        self._start_evt.wait()
        self._awaiting_start = False
        if self._cancel_requested:
            from .errors import CancelledError
            raise CancelledError("recording cancelled before start")

    def _stt_callbacks(self, chunk_label: str = ""):
        """Build on_progress/on_segment callables that forward to the notifier."""
        n = self.notifier

        def on_progress(pct: int, _detail: str) -> None:
            n.progress(pct, chunk_label)

        def on_segment(text: str, ts: str) -> None:
            n.segment(text, ts)

        return on_progress, on_segment

    # -- transcription only --------------------------------------------------

    def transcribe_file(self, path: str) -> TranscriptionResult:
        """Normalize + (optionally) chunk + transcribe a file. Merges chunk segments."""
        from .audio import validate_wav  # local import to avoid cycle at module load
        self.notifier.stage("Normalizing audio", os.path.basename(path))
        if self.benchmark:
            self.benchmark.start("preprocess")
        norm = normalize_file(self.cfg.audio, path, verbose=self.cfg.verbose)
        # validate the normalized WAV before handing to whisper-cli (robustness)
        audio_dur = validate_wav(norm)
        if self.benchmark:
            self.benchmark.stop("preprocess")
        # tell the GUI the audio duration + model name (for speed + model label)
        self.notifier.audio_info(audio_dur, os.path.basename(self.cfg.transcription.model))
        chunks = chunk_audio(self.cfg.audio, norm, verbose=self.cfg.verbose)

        merged_segments: list[Segment] = []
        merged_text_parts: list[str] = []
        offset_ms = 0
        detected_lang = ""

        for idx, chunk_path in enumerate(chunks):
            is_chunked = len(chunks) > 1
            label = f"chunk {idx + 1}/{len(chunks)}" if is_chunked else ""
            self.notifier.stage("Transcribing", label) if is_chunked \
                else self.notifier.stage("Transcribing", os.path.basename(path))
            if is_chunked:
                offset_ms = idx * self.cfg.audio.chunk_seconds * 1000
            on_progress, on_segment = self._stt_callbacks(label)
            if self.benchmark:
                self.benchmark.start("transcription")
            res = self.stt.transcribe(
                chunk_path,
                language=self.cfg.transcription.language,
                on_progress=on_progress,
                on_segment=on_segment,
            )
            if self.benchmark:
                self.benchmark.stop("transcription")
            if not detected_lang and res.language:
                detected_lang = res.language
            for seg in res.segments:
                merged_segments.append(
                    Segment(
                        text=seg.text,
                        start_ms=seg.start_ms + offset_ms,
                        end_ms=seg.end_ms + offset_ms,
                        language=seg.language or detected_lang,
                    )
                )
            merged_text_parts.append(res.text)

        self.notifier.progress(100, "")
        return TranscriptionResult(
            text=" ".join(p for p in merged_text_parts if p).strip(),
            segments=merged_segments,
            language=detected_lang,
        )

    def transcribe_mic(self, duration: float) -> TranscriptionResult:
        if duration <= 0:
            return self._transcribe_mic_streaming()
        dur_label = f"{duration:g}s" if duration and duration > 0 else "until Stop"
        self.notifier.stage("Recording from microphone", dur_label)
        if self.benchmark:
            self.benchmark.start("audio_load")
        self._recording = True
        try:
            wav = capture_mic(self.cfg.audio, duration, verbose=self.cfg.verbose)
        finally:
            self._recording = False
        if self.benchmark:
            self.benchmark.stop("audio_load")
        from .audio import validate_wav
        audio_dur = validate_wav(wav)
        self.notifier.audio_info(audio_dur, os.path.basename(self.cfg.transcription.model))
        self.notifier.stage("Transcribing", "microphone")
        on_progress, on_segment = self._stt_callbacks()
        if self.benchmark:
            self.benchmark.start("transcription")
        res = self.stt.transcribe(
            wav,
            language=self.cfg.transcription.language,
            on_progress=on_progress,
            on_segment=on_segment,
        )
        if self.benchmark:
            self.benchmark.stop("transcription")
        self.notifier.progress(100, "")
        return res

    def _transcribe_mic_streaming(self) -> TranscriptionResult:
        self._wait_for_manual_start()
        self.notifier.stage("Recording from microphone", "live")
        self._recording = True
        self._stream_stop_evt = threading.Event()
        offset_ms = 0
        merged_segments: list[Segment] = []
        merged_text_parts: list[str] = []
        model_name = os.path.basename(self.cfg.transcription.model)
        self.notifier.audio_info(0.0, model_name)

        try:
            for wav_path, chunk_dur in stream_mic_chunks(
                self.cfg.audio,
                self.cfg.audio.stream_chunk_s,
                on_amplitude=self.notifier.amplitude,
                stop_event=self._stream_stop_evt,
                verbose=self.cfg.verbose,
            ):
                self.notifier.stage("Transcribing", "live microphone")
                base_offset_ms = offset_ms

                def on_progress(pct: int, _detail: str) -> None:
                    self.notifier.progress(pct, "live microphone")

                def on_segment(text: str, ts: str) -> None:
                    if not ts:
                        self.notifier.segment(text, "")
                        return
                    start_txt, _, end_txt = ts.partition(" --> ")
                    start_ms = _ts_to_ms(start_txt)
                    end_ms = _ts_to_ms(end_txt)
                    self.notifier.segment(
                        text,
                        f"{_fmt_timestamp_vtt(base_offset_ms + start_ms)} --> {_fmt_timestamp_vtt(base_offset_ms + end_ms)}",
                    )

                res = self.stt.transcribe(
                    wav_path,
                    language=self.cfg.transcription.language,
                    on_progress=on_progress,
                    on_segment=on_segment,
                )
                os.path.exists(wav_path) and os.remove(wav_path)
                total_chunk_ms = int(round(chunk_dur * 1000))
                offset_ms += total_chunk_ms
                self.notifier.audio_info(offset_ms / 1000.0, model_name)

                # Ignore explicit blank-audio markers in the growing transcript.
                for seg in res.segments:
                    if not seg.text or seg.text.strip() == "[BLANK_AUDIO]":
                        continue
                    merged_segments.append(
                        Segment(
                            text=seg.text,
                            start_ms=seg.start_ms + base_offset_ms,
                            end_ms=seg.end_ms + base_offset_ms,
                            language=seg.language or res.language,
                        )
                    )
                if res.text and res.text.strip() != "[BLANK_AUDIO]":
                    merged_text_parts.append(res.text.strip())
                if self._stream_stop_evt.is_set():
                    break
                self.notifier.stage("Recording from microphone", "live")
        finally:
            self._recording = False
            self._stream_stop_evt = None

        self.notifier.progress(100, "")
        text = " ".join(p for p in merged_text_parts if p).strip()
        return TranscriptionResult(
            text=text,
            segments=merged_segments,
            language=(merged_segments[0].language if merged_segments else ""),
            raw={},
        )

    # -- transcription + LLM -------------------------------------------------

    def process(self, transcript: str) -> str:
        """Run the configured LLM mode on a transcript string."""
        if self.cfg.mode == "raw":
            return transcript
        self.notifier.stage("LLM processing", self.cfg.mode)
        system, user = build_prompt(self.cfg.mode, transcript)
        if self.benchmark:
            self.benchmark.start("llm")
        out = self.llm.process(
            user,
            system=system,
            max_tokens=self.cfg.llm.max_tokens,
            temperature=self.cfg.llm.temperature,
        )
        if self.benchmark:
            self.benchmark.stop("llm")
        return out

    # -- full flow -----------------------------------------------------------

    def run_file(self, path: str) -> dict:
        """Full flow on a file: transcribe -> (optionally) process -> outputs."""
        from .errors import CancelledError
        result: Optional[TranscriptionResult] = None
        processed = ""
        written: list[str] = []
        try:
            result = self.transcribe_file(path)
            processed = self.process(result.text) if self.cfg.mode != "raw" else result.text
            written = write_outputs(result, self.cfg, source_name=path)
            self.notifier.done("transcription complete")
        except CancelledError:
            # graceful cancel — don't show as error; return whatever we have
            self.notifier.done("canceled")
            return self._partial_result(path, result, processed, written, canceled=True)
        except BaseException as exc:  # noqa: BLE001 — notify then re-raise
            self.notifier.error(str(exc))
            raise
        return {
            "transcript": result.text,
            "segments": [asdict(s) for s in result.segments],
            "language": result.language,
            "processed": processed,
            "mode": self.cfg.mode,
            "written_files": written,
            "source": path,
        }

    def run_mic(self, duration: float) -> dict:
        from .errors import CancelledError
        result: Optional[TranscriptionResult] = None
        processed = ""
        written: list[str] = []
        try:
            result = self.transcribe_mic(duration)
            processed = self.process(result.text) if self.cfg.mode != "raw" else result.text
            written = write_outputs(result, self.cfg, source_name="microphone")
            self.notifier.done("transcription complete")
        except CancelledError:
            self.notifier.done("canceled")
            return self._partial_result("microphone", result, processed, written, canceled=True)
        except BaseException as exc:  # noqa: BLE001
            self.notifier.error(str(exc))
            raise
        return {
            "transcript": result.text,
            "segments": [asdict(s) for s in result.segments],
            "language": result.language,
            "processed": processed,
            "mode": self.cfg.mode,
            "written_files": written,
            "source": "microphone",
        }

    def _partial_result(self, source: str, result: Optional[TranscriptionResult],
                        processed: str, written: list[str], *, canceled: bool) -> dict:
        """Build a result dict from whatever was captured before a cancel/crash."""
        if result is None:
            return {"transcript": "", "segments": [], "language": "",
                    "processed": "", "mode": self.cfg.mode, "written_files": written,
                    "source": source, "canceled": canceled, "partial": True}
        raw = result.raw if isinstance(result.raw, dict) else {}
        return {
            "transcript": result.text,
            "segments": [asdict(s) for s in result.segments],
            "language": result.language,
            "processed": processed,
            "mode": self.cfg.mode,
            "written_files": written,
            "source": source,
            "canceled": canceled,
            "partial": bool(raw.get("_partial", False)),
        }


def _ts_to_ms(ts: str) -> int:
    """Parse HH:MM:SS.mmm into milliseconds."""
    if not ts:
        return 0
    hh, mm, ss_ms = ts.split(":")
    ss, ms = ss_ms.split(".")
    return (
        int(hh) * 3_600_000
        + int(mm) * 60_000
        + int(ss) * 1000
        + int(ms)
    )
