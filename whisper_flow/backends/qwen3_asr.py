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
                "set it to the path of crispasr.exe / llama-mtmd-cli.exe"
            )
        if shutil.which(bin_path) is None and not os.path.isfile(bin_path):
            raise BinaryNotFoundError(
                bin_path,
                "download CrispASR / llama.cpp release or build from source, "
                "and set transcription.qwen3_asr_bin in config",
            )
        model = self.cfg.qwen3_asr_model
        if not model:
            raise ModelNotFoundError("", kind="Qwen3-ASR GGUF model")
        if not os.path.isfile(model):
            raise ModelNotFoundError(model, kind="Qwen3-ASR GGUF model")
        mmproj = self.cfg.qwen3_asr_mmproj
        if mmproj and not os.path.isfile(mmproj):
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
        is_crispasr = "crispasr" in os.path.basename(c.qwen3_asr_bin).lower()

        if is_crispasr:
            cmd = [
                c.qwen3_asr_bin,
                "-m", c.qwen3_asr_model,
            ]
            if c.language and c.language.lower() != "auto":
                cmd.extend(["-l", c.language])
            cmd.extend(["-t", str(c.threads)])
            cmd.extend(["-bs", "5"])  # beam search — helps produce coherent output
            cmd.extend(["-nt", "-np"])
            # Contextual biasing for proper noun recognition.
            #
            # RESEARCH (see PROGRESS.md):
            # Qwen3-ASR does NOT support --hotwords (that's granite-backend-only,
            # silently ignored by Qwen3). Instead, Qwen3-ASR uses the system prompt
            # for contextual biasing. The technical report says:
            #   "the model learns to utilize the context tokens inside the system
            #    prompt as background knowledge"
            #
            # FORMAT (empirically validated by TypeWhisperer issue #321, 184-run sweep):
            #   "Technical terms: X, Y, Z"  → WER 18.2% (best without phonetic hints)
            #   "X Y Z" (bare space-joined) → WER 33.8% (WORSE than no context!)
            #   "Terms that may appear..."  → verbose, less effective
            #
            # The "Technical terms:" prefix is critical — it tells Qwen3-ASR these
            # are domain vocabulary to bias toward, not text to transcribe.
            # Without it, the model may leak the vocabulary words into the transcript.
            if initial_prompt:
                # Include explicit English instruction in the prompt to prevent
                # Hindi/other language leakage. Qwen3-ASR uses the system prompt
                # as background knowledge, so this guides it to English-only output.
                context = f"Language: English. Technical terms: {initial_prompt}"
                cmd.extend(["--prompt", context])
            cmd.append(audio_path)
            return cmd

        # Fallback for llama-mtmd-cli
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

        # Apply automatic gain control (AGC) before transcription.
        # Qwen3-ASR needs a strong signal — quiet speech gets garbled.
        # AGC amplifies quiet audio to a target RMS level so the model
        # gets consistent volume regardless of how loud the user speaks.
        amplified_path = self._apply_agc(audio_path)

        # Chunk long audio using ADAPTIVE chunking.
        from ..audio import chunk_wav_on_silence
        import os as _os

        chunk_paths = chunk_wav_on_silence(amplified_path)  # adaptive (None)
        is_chunked = len(chunk_paths) > 1

        # Clean up the amplified temp file (if different from original)
        if amplified_path != audio_path:
            try:
                _os.remove(amplified_path)
            except OSError:
                pass

        if is_chunked:
            import sys
            import wave as _wave
            chunk_sizes = []
            for cp in chunk_paths:
                try:
                    with _wave.open(cp, "rb") as _w:
                        chunk_sizes.append(f"{_w.getnframes()/_w.getframerate():.1f}s")
                except Exception:
                    chunk_sizes.append("?s")
            sys.stderr.write(
                f"[qwen3-asr] chunked into {len(chunk_paths)} segments: "
                f"{' + '.join(chunk_sizes)}\n"
            )

        all_texts: list[str] = []
        all_stderr: list[str] = []

        for chunk_idx, chunk_path in enumerate(chunk_paths):
            if self._cancel_requested:
                # Clean up remaining chunks
                for p in chunk_paths[chunk_idx:]:
                    try:
                        _os.remove(p)
                    except OSError:
                        pass
                return TranscriptionResult(text="", segments=[], language=language)

            chunk_text = self._transcribe_single(
                chunk_path, language=language, initial_prompt=initial_prompt,
                on_progress=on_progress if not is_chunked else None,
                on_segment=on_segment,
                chunk_idx=chunk_idx if is_chunked else None,
            )

            # Clean up chunk temp file (chunk files are always temp files
            # created by chunk_wav_on_silence — the original/amplified file
            # is never in chunk_paths when is_chunked=True because chunking
            # creates new files. Safe to always remove.)
            try:
                _os.remove(chunk_path)
            except OSError:
                pass

            if chunk_text:
                all_texts.append(chunk_text)

            if on_progress and is_chunked:
                pct = int((chunk_idx + 1) / len(chunk_paths) * 100)
                on_progress(pct, f"chunk {chunk_idx+1}/{len(chunk_paths)}")

        text = " ".join(t.strip() for t in all_texts if t.strip()).strip()
        text = self._clean_output(text)

        if self.verbose:
            import sys
            sys.stderr.write(f"[qwen3-asr] final: {text!r}\n")

        if on_progress:
            on_progress(100, "Done")

        segment = Segment(text=text, start_ms=0, end_ms=0, language=language)
        return TranscriptionResult(
            text=text,
            segments=[segment],
            language=language,
            raw={"chunked": is_chunked, "num_chunks": len(chunk_paths)},
        )

    def _transcribe_single(self, audio_path: str, *, language: str = "auto",
                           initial_prompt: str = "",
                           on_progress: Optional[ProgressFn] = None,
                           on_segment: Optional[SegmentFn] = None,
                           chunk_idx: Optional[int] = None) -> str:
        """Transcribe a single audio file (one chunk). Returns cleaned text."""
        with self._transcribe_lock:
            cmd = self._build_cmd(audio_path, initial_prompt=initial_prompt)

            if self.verbose:
                import sys
                label = f"chunk {chunk_idx}" if chunk_idx is not None else "audio"
                sys.stderr.write(f"[qwen3-asr] transcribing {label}\n")

            if on_progress and chunk_idx is None:
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
                    "Qwen3-ASR binary (crispasr / llama-mtmd-cli) not found at configured path",
                ) from exc

            stdout_lines = []

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

            try:
                local_proc.stderr.read()
            except Exception:  # noqa: BLE001
                pass

            local_proc.wait()
            self._proc = None

            if self._cancel_requested:
                return ""

            rc = local_proc.returncode
            if rc != 0:
                if self.verbose:
                    import sys
                    sys.stderr.write(f"[qwen3-asr] chunk exited with code {rc}\n")
                return ""

            raw_text = "\n".join(stdout_lines).strip()
            return self._clean_output(raw_text)

    def _apply_agc(self, wav_path: str) -> str:
        """Apply automatic gain control to amplify quiet audio.

        Qwen3-ASR needs a strong signal for accurate transcription. When the
        user speaks quietly, the audio level is too low and the model garbles
        the output. AGC amplifies the audio to a target RMS level so the model
        gets consistent volume regardless of how loud the user speaks.

        Returns the path to the amplified WAV file (may be the same as input
        if no amplification was needed, or a new temp file).
        """
        try:
            import numpy as np
            import wave
            import tempfile
            import os
        except ImportError:
            return wav_path

        try:
            with wave.open(wav_path, "rb") as w:
                sr = w.getframerate()
                nch = w.getnchannels()
                sampwidth = w.getsampwidth()
                nframes = w.getnframes()
                raw = w.readframes(nframes)

            if sampwidth != 2:
                return wav_path  # only handle 16-bit

            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if nch > 1:
                samples = samples[::nch]

            if len(samples) == 0:
                return wav_path

            # Calculate RMS of the speech portion (ignore silence)
            # Use the 90th percentile of frame RMS to estimate speech level
            frame_len = int(0.02 * sr)  # 20ms frames
            n_frames = len(samples) // frame_len
            if n_frames < 2:
                return wav_path

            frames = samples[:n_frames * frame_len].reshape(n_frames, frame_len)
            frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))
            # Speech level = 90th percentile (ignores silence at the bottom)
            speech_rms = float(np.percentile(frame_rms, 90))

            if speech_rms < 0.001:
                return wav_path  # too quiet to amplify meaningfully

            # Target RMS: 0.15 (moderate speech level, not too loud to clip)
            target_rms = 0.15

            # Calculate gain needed (cap at 10x to avoid amplifying noise)
            gain = min(10.0, target_rms / speech_rms)

            # Only amplify if gain > 1.2 (don't attenuate loud speech)
            if gain < 1.2:
                return wav_path  # already loud enough

            # Apply gain
            amplified = samples * gain

            # Clip to prevent distortion
            amplified = np.clip(amplified, -1.0, 1.0)

            # Write to new temp file
            int16_audio = (amplified * 32767.0).astype(np.int16)
            tmp = tempfile.NamedTemporaryFile(
                prefix="wf_agc_", suffix=".wav", delete=False, dir=tempfile.gettempdir()
            )
            out_path = tmp.name
            tmp.close()

            with wave.open(out_path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(int16_audio.tobytes())

            if self.verbose:
                import sys
                sys.stderr.write(
                    f"[qwen3-asr] AGC: gain={gain:.1f}x "
                    f"(speech_rms={speech_rms:.3f} → target={target_rms})\n"
                )

            return out_path

        except Exception:
            return wav_path  # on any error, return original

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
