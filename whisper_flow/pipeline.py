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

from .audio import LiveMicCapture, capture_mic, chunk_audio, normalize_file, stop_active_capture
from .backends import LlamaCppBackend, Qwen3AsrBackend, Segment, TranscriptionBackend, TranscriptionResult, WhisperCppBackend
from .config import Config
from .formatting import apply_smart_formatting
from .notifier import Notifier, NullNotifier
from .prompts import build_prompt, resolve_mode

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
        self._stt: Optional[TranscriptionBackend] = None
        self._llm: Optional[LlamaCppBackend] = None
        self._recording = False
        self._awaiting_start = False
        self._cancel_requested = False
        self._start_evt: Optional[threading.Event] = None
        self._stream_stop_evt: Optional[threading.Event] = None
        self._recording_stop_cb = None
        # wire the Cancel button to the STT backend's cancel() (registered lazily
        # once the backend is instantiated; safe to call before transcribe).
        self.notifier.register_cancel(self._cancel)
        if hasattr(self.notifier, "register_start"):
            try:
                self.notifier.register_start(self._start)
            except Exception:  # noqa: BLE001
                pass

    @property
    def stt(self) -> TranscriptionBackend:
        if self._stt is None:
            backend = getattr(self.cfg.transcription, "backend", "whisper_cpp")
            if backend == "qwen3_asr":
                self._stt = Qwen3AsrBackend(self.cfg.transcription, verbose=self.cfg.verbose)
            else:
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
            if callable(self._recording_stop_cb):
                try:
                    self._recording_stop_cb()
                except Exception:  # noqa: BLE001
                    pass
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
        self._sync_mode_from_notifier()
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

    def _sync_mode_from_notifier(self) -> None:
        getter = getattr(self.notifier, "get_selected_mode", None)
        if not callable(getter):
            selected = ""
        else:
            try:
                selected = str(getter() or "").strip()
            except Exception:  # noqa: BLE001
                selected = ""
        if selected:
            self.cfg.mode = selected

        mic_getter = getattr(self.notifier, "get_selected_mic", None)
        if callable(mic_getter):
            try:
                mic = str(mic_getter() or "").strip()
            except Exception:  # noqa: BLE001
                mic = ""
            if mic:
                self.cfg.audio.mic_device = mic

        style_getter = getattr(self.notifier, "get_selected_writing_style", None)
        if callable(style_getter):
            try:
                style = str(style_getter() or "").strip()
            except Exception:  # noqa: BLE001
                style = ""
            if style:
                self.cfg.writing_style = style

    def _stt_callbacks(self, chunk_label: str = ""):
        """Build on_progress/on_segment callables that forward to the notifier."""
        n = self.notifier

        def on_progress(pct: int, _detail: str) -> None:
            n.progress(pct, chunk_label)

        def on_segment(text: str, ts: str) -> None:
            n.segment(text, ts)

        return on_progress, on_segment

    # -- transcription only --------------------------------------------------

    def transcribe_file(self, path: str, *, initial_prompt: str = "") -> TranscriptionResult:
        """Normalize + (optionally) chunk + transcribe a file. Merges chunk segments."""
        from .audio import validate_wav  # local import to avoid cycle at module load
        self.notifier.stage("Normalizing audio", os.path.basename(path))
        if self.benchmark:
            self.benchmark.start("preprocess")
        norm = normalize_file(self.cfg.audio, path, verbose=self.cfg.verbose)
        audio_dur = validate_wav(norm)
        if self.benchmark:
            self.benchmark.stop("preprocess")
        self.notifier.audio_info(audio_dur, os.path.basename(self.cfg.transcription.model))

        is_chunked = bool(self.cfg.audio.chunk_seconds > 0 and audio_dur > self.cfg.audio.chunk_seconds)
        chunks = (
            chunk_audio(self.cfg.audio, norm, verbose=self.cfg.verbose)
            if is_chunked
            else [norm]
        )
        total_chunks = len(chunks)

        merged_segments: list[Segment] = []
        merged_text_parts: list[str] = []
        detected_lang = ""

        for idx, chunk_path in enumerate(chunks):
            label = f"chunk {idx + 1}/{total_chunks}" if is_chunked else ""
            if label:
                self.notifier.stage("Transcribing", label)
            offset_ms = 0
            if is_chunked:
                offset_ms = idx * self.cfg.audio.chunk_seconds * 1000
            on_progress, on_segment = self._stt_callbacks(label)
            if self.benchmark:
                self.benchmark.start("transcription")
            res = self.stt.transcribe(
                chunk_path,
                language=self.cfg.transcription.language,
                initial_prompt=initial_prompt,
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

    def transcribe_mic(self, duration: float, *, initial_prompt: str = "") -> TranscriptionResult:
        if duration <= 0:
            return self._transcribe_mic_streaming(initial_prompt=initial_prompt)
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
            initial_prompt=initial_prompt,
            on_progress=on_progress,
            on_segment=on_segment,
        )
        if self.benchmark:
            self.benchmark.stop("transcription")
        self.notifier.progress(100, "")
        return res

    def _transcribe_mic_streaming(self, *, initial_prompt: str = "") -> TranscriptionResult:
        self._wait_for_manual_start()
        self.notifier.stage("Recording from microphone", "live")
        self._recording = True
        self._stream_stop_evt = threading.Event()
        model_name = os.path.basename(self.cfg.transcription.model)
        self.notifier.audio_info(0.0, model_name)
        stable_tail_ms = max(1200, int(self.cfg.audio.stream_chunk_s * 1000))
        preview_poll_s = max(0.4, float(self.cfg.audio.stream_chunk_s) * 0.5)

        capture = LiveMicCapture(
            self.cfg.audio,
            max_window_seconds=self.cfg.audio.stream_max_s,
            on_amplitude=self._on_amplitude,
            verbose=self.cfg.verbose,
        )
        capture.start()

        def _emit_preview(text: str) -> None:
            cb = getattr(self.notifier, "on_stream_preview", None)
            if callable(cb):
                try:
                    cb(text)
                except Exception:
                    pass

        try:
            capture.wait_until_audio(timeout=2.0)
            while not self._stream_stop_evt.is_set():
                capture.sleep(preview_poll_s)
                total_ms = int(round(capture.total_duration_sec * 1000))
                self.notifier.audio_info(total_ms / 1000.0, model_name)
                if total_ms < 700:
                    continue
                wav_path = ""
                try:
                    wav_path, _window_dur, offset_ms = capture.snapshot_window()
                    preview_res = self.stt.transcribe(
                        wav_path,
                        language=self.cfg.transcription.language,
                        initial_prompt=initial_prompt,
                    )
                finally:
                    if wav_path and os.path.exists(wav_path):
                        os.remove(wav_path)

                absolute_segments: list[Segment] = []
                for seg in preview_res.segments:
                    text = seg.text.strip()
                    if not text:
                        continue
                    abs_start = seg.start_ms + offset_ms
                    abs_end = seg.end_ms + offset_ms
                    if abs_end < (total_ms - stable_tail_ms):
                        continue
                    absolute_segments.append(
                        Segment(
                            text=text,
                            start_ms=abs_start,
                            end_ms=abs_end,
                            language=seg.language,
                        )
                    )
                if absolute_segments:
                    preview_text = " ".join(s.text for s in absolute_segments).strip()
                    if preview_text:
                        _emit_preview(preview_text)
            self.notifier.stage("Finalizing transcription", "full pass")
            final_wav = ""
            final_wav, total_dur, _ = capture.snapshot_full()
            self.notifier.audio_info(total_dur, model_name)
        finally:
            _emit_preview("")
            self._recording = False
            self._recording_stop_cb = None
            self._stream_stop_evt = None
            capture.close()

        try:
            on_progress, _ = self._stt_callbacks("final")
            res = self.stt.transcribe(
                final_wav,
                language=self.cfg.transcription.language,
                initial_prompt=initial_prompt,
                on_progress=on_progress,
            )
        finally:
            if final_wav and os.path.exists(final_wav):
                os.remove(final_wav)
        self.notifier.progress(100, "")
        return res

    # -- transcription + LLM -------------------------------------------------

    def process(self, transcript: str) -> str:
        """Run the configured LLM mode on a transcript string."""
        self._sync_mode_from_notifier()
        self.cfg.mode = resolve_mode(self.cfg.mode)
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

    def _format_transcript(self, transcript: str) -> str:
        self._sync_mode_from_notifier()
        if not self.cfg.smart_formatting:
            return transcript
        return apply_smart_formatting(transcript, writing_style=self.cfg.writing_style)

    # -- full flow -----------------------------------------------------------

    def run_file(self, path: str) -> dict:
        """Full flow on a file: transcribe -> (optionally) process -> outputs."""
        from .errors import CancelledError
        result: Optional[TranscriptionResult] = None
        processed = ""
        written: list[str] = []
        try:
            result = self.transcribe_file(path)
            result.text = self._format_transcript(result.text)
            processed = self.process(result.text) if self.cfg.mode != "raw" else result.text
            written = write_outputs(result, self.cfg, source_name=path)
            self._publish_results(result.text, processed)
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
            result.text = self._format_transcript(result.text)
            processed = self.process(result.text) if self.cfg.mode != "raw" else result.text
            written = write_outputs(result, self.cfg, source_name="microphone")
            self._publish_results(result.text, processed)
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

    def _publish_results(self, transcript: str, processed: str) -> None:
        cb = getattr(self.notifier, "result", None)
        if not callable(cb):
            return
        try:
            cb("transcript", transcript)
            cb("processed", processed)
        except Exception:  # noqa: BLE001
            pass


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
