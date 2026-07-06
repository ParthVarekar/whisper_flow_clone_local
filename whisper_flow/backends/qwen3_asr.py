"""Qwen3-ASR transcription backend using llama-mtmd-cli.

Wraps the llama.cpp multimodal CLI (`llama-mtmd-cli`) to run Qwen3-ASR GGUF
models for speech-to-text. This replaces whisper-cli with a more accurate
speech-LLM that leverages the Qwen3-Omni architecture.

Unlike whisper.cpp, Qwen3-ASR:
  - Uses a multimodal projector (mmproj) to encode audio
  - Generates text via LLM decoding (not CTC/attention decoder)
  - Returns plain text output (not JSON segments)
  - Supports vocabulary biasing through the system prompt
"""

from __future__ import annotations

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


class Qwen3AsrBackend(TranscriptionBackend):
    name = "qwen3_asr"

    def __init__(self, cfg: TranscriptionConfig, *, verbose: bool = False):
        self.cfg = cfg
        self.verbose = verbose
        self._proc: Optional[subprocess.Popen] = None
        self._cancel_requested = False
        self._transcribe_lock = threading.Lock()

    # -- checks --------------------------------------------------------------

    def check(self) -> None:
        bin_path = self.cfg.qwen3_asr_bin
        if not bin_path:
            raise ConfigError(
                "transcription.qwen3_asr_bin is not set; "
                "set it to the path of llama-mtmd-cli.exe"
            )
        if shutil.which(bin_path) is None and not os.path.isfile(bin_path):
            raise BinaryNotFoundError(
                bin_path,
                "download llama.cpp release or build from source, "
                "and set transcription.qwen3_asr_bin in config",
            )
        model = self.cfg.qwen3_asr_model
        if not model:
            raise ModelNotFoundError("", kind="Qwen3-ASR GGUF model")
        if not os.path.isfile(model):
            raise ModelNotFoundError(model, kind="Qwen3-ASR GGUF model")
        mmproj = self.cfg.qwen3_asr_mmproj
        if not mmproj:
            raise ConfigError(
                "transcription.qwen3_asr_mmproj is not set; "
                "set it to the path of mmproj-Qwen3-ASR-*.gguf"
            )
        if not os.path.isfile(mmproj):
            raise ModelNotFoundError(mmproj, kind="Qwen3-ASR mmproj")

    # -- cancel --------------------------------------------------------------

    def cancel(self) -> None:
        self._cancel_requested = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

    # -- transcription -------------------------------------------------------

    def _build_cmd(self, audio_path: str, *, initial_prompt: str = "") -> list[str]:
        c = self.cfg
        cmd = [
            c.qwen3_asr_bin,
            "-m", c.qwen3_asr_model,
            "--mmproj", c.qwen3_asr_mmproj,
            "--audio", audio_path,
            "-ngl", "99",           # offload all layers to GPU
            "-t", str(c.threads),
        ]

        # Build the transcription prompt with optional vocabulary biasing
        prompt = "Transcribe the audio."
        if initial_prompt:
            prompt = (
                f"Transcribe the audio. Use these exact spellings for names "
                f"and terms when they occur: {initial_prompt}"
            )
        cmd.extend(["-p", prompt])

        return cmd

    def transcribe(self, audio_path: str, *, language: str = "auto",
                   initial_prompt: str = "",
                   on_progress: Optional[ProgressFn] = None,
                   on_segment: Optional[SegmentFn] = None) -> TranscriptionResult:
        self.check()
        self._cancel_requested = False

        with self._transcribe_lock:
            cmd = self._build_cmd(audio_path, initial_prompt=initial_prompt)

            if self.verbose:
                import sys
                sys.stderr.write(f"[qwen3-asr] cmd: {' '.join(cmd)}\n")

            if on_progress:
                on_progress(-1, "Transcribing with Qwen3-ASR...")

            try:
                local_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self._proc = local_proc
            except FileNotFoundError as exc:
                raise BinaryNotFoundError(
                    self.cfg.qwen3_asr_bin,
                    "llama-mtmd-cli not found at configured path",
                ) from exc

            stdout_lines = []
            stderr_lines = []

            # Read stdout line by line for streaming
            try:
                for line in local_proc.stdout:
                    if self._cancel_requested:
                        local_proc.terminate()
                        break
                    stripped = line.rstrip("\r\n")
                    if stripped:
                        stdout_lines.append(stripped)
                        if on_segment:
                            on_segment(stripped, "")
            except Exception:  # noqa: BLE001
                pass

            # Read stderr
            try:
                stderr_text = local_proc.stderr.read()
                stderr_lines = stderr_text.splitlines()
            except Exception:  # noqa: BLE001
                pass

            local_proc.wait()
            self._proc = None

            if self._cancel_requested:
                return TranscriptionResult(text="", segments=[], language=language)

            rc = local_proc.returncode
            if rc != 0:
                err_text = "\n".join(stderr_lines[-10:]) if stderr_lines else "(no stderr)"
                raise TranscriptionError(
                    f"llama-mtmd-cli exited with code {rc}: {err_text}"
                )

            # Parse output — Qwen3-ASR outputs plain text (may include system tokens)
            raw_text = "\n".join(stdout_lines).strip()

            # Clean up common artifacts from multimodal output
            text = self._clean_output(raw_text)

            if self.verbose:
                import sys
                sys.stderr.write(f"[qwen3-asr] raw output: {raw_text!r}\n")
                sys.stderr.write(f"[qwen3-asr] cleaned: {text!r}\n")

            if on_progress:
                on_progress(100, "Done")

            segment = Segment(text=text, start_ms=0, end_ms=0, language=language)
            return TranscriptionResult(
                text=text,
                segments=[segment],
                language=language,
                raw={"stdout": raw_text, "stderr": "\n".join(stderr_lines)},
            )

    @staticmethod
    def _clean_output(text: str) -> str:
        """Clean up Qwen3-ASR multimodal output."""
        t = text.strip()
        # Remove prompt echo if CLI displays prompt
        t = re.sub(r'^Transcribe the audio\.(?:\s*Use these exact spellings[^\n]*: [^\n]+)?\s*', '', t, flags=re.IGNORECASE).strip()
        # Remove Qwen3-ASR language header (e.g. "language English<asr_text>", "<asr_text>")
        t = re.sub(r'^(?:language\s+[A-Za-z0-9_\-]+\s*)?<\s*asr_text\s*>\s*', '', t, flags=re.IGNORECASE).strip()
        # Remove any system/special tokens that may leak through
        t = re.sub(r'<\|[^|]+\|>', '', t).strip()
        # Remove leading "assistant\n" if present (chat template artifact)
        t = re.sub(r'^assistant\s*\n?', '', t, flags=re.IGNORECASE).strip()
        # Remove trailing end tokens
        t = re.sub(r'<end_of_turn>|<\|endoftext\|>|\[end\]', '', t, flags=re.IGNORECASE).strip()
        return t
