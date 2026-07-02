"""whisper.cpp transcription backend.

Wraps the official `whisper-cli` binary via subprocess. Produces JSON output
(`-oj`) and parses segments + timestamps. whisper.cpp handles long audio
internally (30s windows); pre-chunking is done by the audio module if enabled.

Progress feedback mirrors whisper.cpp's own model (confirmed from upstream,
see RESEARCH.md Task ID 3):
  * stderr line  -> `whisper_print_progress_callback: progress = %3d%%`
                   (fired every `progress_step` (=5) percent when `-pp` is passed)
  * stdout line  -> `[HH:MM:SS.mmm --> HH:MM:SS.mmm]  <text>`
                   (one line per closed segment)
  * `-np` silences model-load / system_info / timing noise on stderr but does
    NOT silence the progress fprintf or the segment printf.
  * `-of -` (pipe mode) DISABLES both callbacks — so we never use it here.

We stream both pipes line-by-line and forward parsed events to optional
`on_progress` / `on_segment` callbacks (used by the GUI notifier). The final
authoritative result still comes from the `-oj` JSON file.

Verified against whisper.cpp v1.9.x (ggml-org/whisper.cpp). See RESEARCH.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Optional

from ..config import TranscriptionConfig
from ..errors import BinaryNotFoundError, ConfigError, ModelNotFoundError, TranscriptionError
from .base import ProgressFn, Segment, SegmentFn, TranscriptionBackend, TranscriptionResult

# ---------------------------------------------------------------------------
# Line parsers (standalone so they can be unit-tested without a subprocess).
# Exact formats confirmed from upstream cli.cpp — see module docstring.
# ---------------------------------------------------------------------------

# `whisper_print_progress_callback: progress =  10%`  (%3d right-justifies)
_PROGRESS_RE = re.compile(r"^whisper_print_progress_callback:\s*progress\s*=\s*(\d+)%\s*$")

# `[00:00:00.000 --> 00:00:05.234]  Hello world.`  (two spaces after ']')
_SEGMENT_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})\]\s+(.*)$"
)


def parse_progress_line(line: str) -> Optional[int]:
    """Return the progress percent if `line` is a whisper.cpp progress line, else None."""
    m = _PROGRESS_RE.match(line.rstrip("\r\n"))
    return int(m.group(1)) if m else None


def parse_segment_line(line: str) -> Optional[tuple[str, str, str]]:
    """Return (start_ts, end_ts, text) if `line` is a whisper.cpp segment line, else None."""
    m = _SEGMENT_RE.match(line.rstrip("\r\n"))
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3).strip()


class WhisperCppBackend(TranscriptionBackend):
    name = "whisper.cpp"

    def __init__(self, cfg: TranscriptionConfig, *, verbose: bool = False):
        self.cfg = cfg
        self.verbose = verbose
        self._proc: Optional[subprocess.Popen] = None
        self._cancel_requested = False

    # -- checks --------------------------------------------------------------

    def check(self) -> None:
        # Validate config first (these are user errors, independent of binaries).
        if self.cfg.vad:
            if not self.cfg.vad_model:
                raise ConfigError(
                    "transcription.vad=True but transcription.vad_model is empty; "
                    "set it to a ggml-silero-v*.bin path (run scripts/download_models.sh --vad)"
                )
            if not os.path.isfile(self.cfg.vad_model):
                raise ModelNotFoundError(self.cfg.vad_model, kind="Silero VAD model")
        if not self.cfg.model:
            raise ModelNotFoundError("", kind="Whisper ggml model")
        if not os.path.isfile(self.cfg.model):
            raise ModelNotFoundError(self.cfg.model, kind="Whisper ggml model")
        if shutil.which(self.cfg.whisper_bin) is None:
            raise BinaryNotFoundError(
                self.cfg.whisper_bin,
                "build whisper.cpp (scripts/build.sh) and add build/bin to PATH, "
                "or set transcription.whisper_bin in config",
            )

    # -- cancel --------------------------------------------------------------

    def cancel(self) -> None:
        """Cooperative cancel: set flag, then SIGTERM the subprocess; SIGKILL after 5s."""
        self._cancel_requested = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()  # SIGTERM (POSIX) / TerminateProcess (Windows)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()  # SIGKILL
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass  # last resort: leave it; reader threads will exit on EOF

    # -- transcription -------------------------------------------------------

    def _build_cmd(self, audio_path: str, out_prefix: str, language: str,
                   *, initial_prompt: str = "", want_progress: bool) -> list[str]:
        c = self.cfg
        cmd = [
            c.whisper_bin,
            "-m", c.model,
            "-f", audio_path,
            "-oj",            # JSON output -> <prefix>.json  (final, authoritative)
            "-of", out_prefix,
            "-t", str(c.threads),
            "-np",            # silence model-load / system_info / timing noise on stderr
        ]
        if initial_prompt and initial_prompt.strip():
            cmd += ["--prompt", initial_prompt.strip()]
        if want_progress:
            # enables whisper_print_progress_callback on stderr (every 5%)
            cmd += ["-pp"]
        # language
        lang = (language or c.language or "auto").strip() or "auto"
        if lang and lang != "auto":
            cmd += ["-l", lang]
        # else: whisper.cpp auto-detects by default
        if c.translate:
            cmd += ["-tr"]
        if c.flash_attention:
            cmd += ["-fa"]
        if c.max_len and c.max_len > 0:
            cmd += ["--max-len", str(c.max_len)]
        # VAD: whisper-cli natively supports Silero VAD (cli.cpp:1248-1256).
        # When --vad + -vm <model> are set, whisper_full runs VAD first and
        # skips silence, speeding up mic captures with long pauses.
        if c.vad and c.vad_model:
            cmd += ["--vad", "-vm", c.vad_model]
            if c.vad_threshold > 0:
                cmd += ["-vt", str(c.vad_threshold)]
            if c.vad_min_speech_ms > 0:
                cmd += ["-vspd", str(c.vad_min_speech_ms)]
            if c.vad_min_silence_ms > 0:
                cmd += ["-vsd", str(c.vad_min_silence_ms)]
            if c.vad_max_speech_s > 0:
                cmd += ["-vmsd", str(c.vad_max_speech_s)]
            if c.vad_speech_pad_ms > 0:
                cmd += ["-vp", str(c.vad_speech_pad_ms)]
        # NOTE: GPU backend in whisper.cpp is chosen at BUILD time
        # (-DGGML_CUDA=1 / -DGGML_VULKAN=1 / Metal auto). There is no stable
        # runtime --gpu flag, so cfg.gpu is informational only here.
        # NOTE: we deliberately do NOT pass `-of -` (pipe mode) — it disables
        # both the segment callback and progress (cli.cpp:1119-1124).
        return cmd

    def transcribe(self, audio_path: str, *, language: str = "auto",
                   initial_prompt: str = "",
                   on_progress: Optional[ProgressFn] = None,
                   on_segment: Optional[SegmentFn] = None) -> TranscriptionResult:
        from ..errors import CancelledError  # local import to avoid cycle
        self.check()
        if not os.path.isfile(audio_path):
            raise TranscriptionError(f"audio file not found: {audio_path!r}")

        self._cancel_requested = False
        want_progress = on_progress is not None
        with tempfile.TemporaryDirectory(prefix="whisperflow_") as tmp:
            out_prefix = os.path.join(tmp, "out")
            cmd = self._build_cmd(audio_path, out_prefix, language,
                                  initial_prompt=initial_prompt, want_progress=want_progress)
            self._log(f"running: {' '.join(cmd)}")

            stderr_tail: list[str] = []
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=1,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except FileNotFoundError as exc:
                self._proc = None
                raise BinaryNotFoundError(self.cfg.whisper_bin, str(exc)) from exc

            # Two reader threads so neither pipe can deadlock the other.
            def _read_stderr() -> None:
                assert self._proc is not None and self._proc.stderr is not None
                for line in self._proc.stderr:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    stderr_tail.append(line)
                    self._log(f"[stderr] {line}")
                    if on_progress is not None:
                        pct = parse_progress_line(line)
                        if pct is not None:
                            try:
                                on_progress(pct, "")
                            except Exception:  # noqa: BLE001 — notifier must never break STT
                                pass

            def _read_stdout() -> None:
                assert self._proc is not None and self._proc.stdout is not None
                for line in self._proc.stdout:
                    if on_segment is None:
                        continue
                    parsed = parse_segment_line(line)
                    if parsed is None:
                        continue
                    start_ts, end_ts, text = parsed
                    try:
                        on_segment(text, f"{start_ts} --> {end_ts}")
                    except Exception:  # noqa: BLE001
                        pass

            t_err = threading.Thread(target=_read_stderr, daemon=True)
            t_out = threading.Thread(target=_read_stdout, daemon=True)
            t_err.start()
            t_out.start()
            self._proc.wait()
            t_err.join(timeout=2.0)
            t_out.join(timeout=2.0)
            proc = self._proc
            self._proc = None

            if self._cancel_requested:
                # still try to recover partial output below before raising
                partial = self._try_parse_partial(out_prefix + ".json")
                if partial is not None:
                    partial.raw["_partial"] = True
                    raise CancelledError(
                        f"transcription cancelled by user (partial: {len(partial.segments)} segments)"
                    )
                raise CancelledError("transcription cancelled by user")

            if proc.returncode != 0:
                # crashed subprocess: attempt partial recovery from any JSON written
                partial = self._try_parse_partial(out_prefix + ".json")
                if partial is not None and partial.segments:
                    partial.raw["_partial"] = True
                    self._log(f"whisper-cli crashed (code {proc.returncode}) — recovered {len(partial.segments)} partial segments")
                    return partial
                raise TranscriptionError(
                    f"whisper-cli exited with code {proc.returncode}\n"
                    + "\n".join(stderr_tail[-30:]).strip()
                )

            json_path = out_prefix + ".json"
            if not os.path.isfile(json_path):
                raise TranscriptionError(
                    f"whisper-cli produced no JSON output (expected {json_path}).\n"
                    + "\n".join(stderr_tail[-30:]).strip()
                )

            result = self._parse_json(json_path)
            # empty-transcript guard: exit 0 but no segments → likely silent audio
            if not result.segments and not result.text:
                raise TranscriptionError(
                    "empty transcript — audio may be silent, too quiet, or in an "
                    "unsupported language. Try a different language flag or check the "
                    "recording level."
                )
            return result

    def _try_parse_partial(self, json_path: str) -> Optional[TranscriptionResult]:
        """Best-effort parse of a partial JSON file (whisper-cli may have flushed
        incomplete output before dying). Returns None if not parseable."""
        if not os.path.isfile(json_path):
            return None
        try:
            return self._parse_json(json_path)
        except (TranscriptionError, OSError, ValueError):
            # truncated JSON — try to salvage by closing arrays/objects
            try:
                with open(json_path, encoding="utf-8") as fh:
                    raw = fh.read()
            except OSError:
                return None
            # crude repair: truncate at last complete object + close arrays
            for cut_marker in ("},\n    {", "},\n{", "}\n"):
                idx = raw.rfind(cut_marker)
                if idx > 0:
                    candidate = raw[:idx + 1] + "\n  ]\n}\n"
                    try:
                        data = json.loads(candidate)
                        # synthesize a TranscriptionResult without re-calling _parse_json
                        # (which would re-open the file)
                        return self._result_from_dict(data)
                    except json.JSONDecodeError:
                        continue
            return None

    def _result_from_dict(self, data: dict) -> TranscriptionResult:
        """Build a TranscriptionResult from a parsed whisper.cpp JSON dict.

        Defensive: supports both nested {"result":..., "transcription":[...]}
        and flat {"segments":[...]} shapes.
        """
        segments: list[Segment] = []
        lang = ""
        if isinstance(data.get("result"), dict):
            lang = str(data["result"].get("language", "") or "")

        items = data.get("transcription")
        if not isinstance(items, list):
            items = data.get("segments", [])

        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            offsets = item.get("offsets", {}) or {}
            start_ms = int(offsets.get("from", 0) or 0)
            end_ms = int(offsets.get("to", start_ms) or start_ms)
            if not text:
                continue
            segments.append(Segment(text=text, start_ms=start_ms, end_ms=end_ms, language=lang))

        full_text = " ".join(s.text for s in segments).strip()
        if not full_text:
            full_text = str(data.get("text", "")).strip()

        return TranscriptionResult(
            text=full_text,
            segments=segments,
            language=lang,
            raw=data,
        )

    def _parse_json(self, json_path: str) -> TranscriptionResult:
        try:
            with open(json_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise TranscriptionError(f"failed to parse whisper JSON: {exc}") from exc
        return self._result_from_dict(data)

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[whisper.cpp] {msg}", flush=True)
