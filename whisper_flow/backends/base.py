"""Backend abstractions.

The transcription and LLM backends are intentionally separable: each implements
a small interface so you can swap whisper.cpp for another STT, or llama.cpp for
another LLM, without touching the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

# Progress callback: (percent 0..100, detail_str). percent may be -1 for
# indeterminate stages. detail is a short human string (e.g. latest segment).
ProgressFn = Callable[[int, str], None]
# Segment callback: (text, timestamp_str) where timestamp_str is
# "HH:MM:SS.mmm --> HH:MM:SS.mmm" (mirrors whisper.cpp stdout) or "".
SegmentFn = Callable[[str, str], None]


@dataclass
class Segment:
    """One transcribed segment with timestamps."""

    text: str
    start_ms: int = 0  # milliseconds from start of audio
    end_ms: int = 0
    language: str = ""  # detected language (filled by backend if known)


@dataclass
class TranscriptionResult:
    text: str  # full concatenated transcript
    segments: list[Segment] = field(default_factory=list)
    language: str = ""
    raw: dict = field(default_factory=dict)  # raw parsed backend output, for debugging


class TranscriptionBackend(ABC):
    """Speech-to-text backend interface."""

    name: str = "abstract"

    @abstractmethod
    def check(self) -> None:
        """Verify binaries + model exist. Raise BinaryNotFoundError / ModelNotFoundError."""

    @abstractmethod
    def transcribe(self, audio_path: str, *, language: str = "auto",
                   initial_prompt: str = "",
                   on_progress: Optional[ProgressFn] = None,
                   on_segment: Optional[SegmentFn] = None) -> TranscriptionResult:
        """Transcribe a 16kHz mono WAV file (or any file the backend accepts).

        If `on_progress`/`on_segment` are provided, the backend should call them
        in real time as the subprocess streams output (mirrors whisper.cpp's
        stderr progress + stdout segment callbacks — see ARCHITECTURE.md).
        """


class LLMBackend(ABC):
    """Text-processing LLM backend interface."""

    name: str = "abstract"

    @abstractmethod
    def check(self) -> None:
        """Verify binaries + model exist / server reachable."""

    @abstractmethod
    def process(self, prompt: str, *, system: str = "", max_tokens: int = 512,
                temperature: float = 0.3) -> str:
        """Run the LLM on `prompt` with an optional `system` instruction.

        Returns the generated text.
        """
