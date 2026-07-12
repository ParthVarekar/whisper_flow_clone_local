#!/usr/bin/env python3
"""Phase 2 data preparation — teacher pipeline for Moonshine Tiny SFT.

Runs the **teacher** pipeline over a corpus of raw audio and emits paired
``(audio_path, cleaned_text)`` training examples in JSONL format, suitable
for consumption by ``sft_train.py``.

Teacher pipeline (FormalASR recipe, arXiv:2605.19266v3):

    raw audio
        │
        ▼
    Whisper Large v3  ──►  verbatim transcript  (raw_text)
        │
        ▼
    LLM cleanup       ──►  cleaned / formatted text  (text)  ← SFT target
        │
        ▼
    quality filter    ──►  drop bad pairs
        │
        ▼
    (optional) augmentation  ──►  speed perturbation + noise injection
        │
        ▼
    JSONL record: {audio_path, text, raw_text, duration, source, augment}

The LLM cleanup step uses the in-house ``z-ai chat`` CLI by default (the
Python-callable surface of ``z-ai-web-dev-sdk``); an OpenAI-compatible HTTP
endpoint is also supported via ``--llm-backend openai``.

This script is **offline** — it is run once to build the training set and is
not part of the shipped daemon.

Usage
-----
    python training/prepare_data.py \\
        --input-dir /data/custom_dictation \\
        --format custom \\
        --output /data/train.jsonl \\
        --whisper-model large-v3 \\
        --whisper-device cuda \\
        --llm-backend z-ai \\
        --augment \\
        --speed-factors 0.9 1.0 1.1 \\
        --noise-snrs 20 \\
        --max-samples 50000 \\
        --resume

Python 3.10+. See ``training/README.md`` §5.1 for the required training venv.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterable,
    Iterator,
    Optional,
    Sequence,
    Tuple,
)

LOG = logging.getLogger("prepare_data")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_SAMPLE_RATE = 16_000  # Moonshine frontend sample rate

# Cleanup system prompt — engineered to mirror whisper_flow/formatting.py
# behavior plus the LLM-only behaviors (grammar fixes, paraphrasing).
# Kept in sync with the rule-based rules so the SFT target distribution is a
# strict superset of what formatting.py already produces.
CLEANUP_SYSTEM_PROMPT = """You are a dictation cleanup engine. You receive a \
verbatim speech-to-text transcript of a single dictation utterance and must \
output the cleaned, formatted text that the speaker intended to write.

Apply these cleanup rules:
1. Remove filler words: um, uh, hmm, er, ah, like (when used as filler), \
you know, I mean, sort of, kind of, basically, literally, honestly, \
obviously, essentially, frankly.
2. Remove and repair backtracks: if the speaker says "X. Actually Y." or \
"I mean Y" or "sorry, Y" or "scratch that, Y", output ONLY the final \
intended version (Y). Do not include the abandoned phrase (X).
3. Collapse stutter / repeated words: "the the" -> "the", "I I I" -> "I".
4. Convert spoken punctuation words to symbols: "period" -> ".", \
"comma" -> ",", "question mark" -> "?", "exclamation point"/"exclamation \
mark" -> "!", "semicolon" -> ";", "colon" -> ":", "dash" -> "—", \
"open quote"/"close quote" -> '"', "open paren"/"close paren" -> "(" ")", \
"new paragraph" -> "\\n\\n", "new line"/"next line"/"line break" -> "\\n", \
"tab" -> "\\t".
5. Inverse text normalization (ITN): write numbers as digits \
("twenty five" -> "25"), currency as symbols ("twenty dollars" -> "$20"), \
times as digits ("three thirty pm" -> "3:30 PM"), dates and ordinals as \
written ("march fifth" -> "March 5th").
6. Fix capitalization: sentence starts, standalone "i" -> "I", proper nouns.
7. Fix spacing: no double spaces, no space before punctuation, single space \
after punctuation.
8. Ensure the text ends with appropriate terminal punctuation (., ?, !) \
unless it ends with a newline or already has terminal punctuation.
9. Fix simple grammar errors ("we was" -> "we were", "he don't" -> "he \
doesn't").
10. If a run-on sentence is clearly two sentences, split it with a period \
and capitalize.

CRITICAL constraints — do NOT violate these:
- Output ONLY the cleaned text. No preamble, no explanation, no quotes \
around the result, no markdown fences.
- Do NOT change the speaker's meaning. Do NOT add information. Do NOT \
remove information the speaker clearly intended.
- Preserve the speaker's register: if they said "gonna" leave it as "gonna"; \
do NOT upscale to "going to" unless they explicitly said so.
- Preserve all numbers, dates, proper nouns, and code-like tokens exactly \
as spoken.
- If the transcript is already clean, return it unchanged.
- If the transcript is empty or only filler, return the empty string."""

# Markers that indicate the LLM added commentary instead of just the cleaned
# text. Pairs whose `clean` starts with any of these are rejected by the
# quality filter.
_LLM_PREAMBLE_PREFIXES: Tuple[str, ...] = (
    "sure",
    "here is",
    "here's",
    "certainly",
    "of course",
    "the cleaned",
    "cleaned text",
    "output:",
    "result:",
    ">",
    "```",
    "note:",
    "i'll clean",
    "i'll rewrite",
    "i've cleaned",
    "i've rewritten",
    "let me clean",
    "let me rewrite",
)

# Maximum character-error-rate between raw and cleaned text. Pairs above
# this are likely LLM hallucinations / over-rewrites and are dropped.
#
# Note: this is intentionally lenient (0.75, not the FormalASR paper's 0.5
# which was tuned for Chinese where character-level edits are smaller).
# English cleanup routinely produces large CER deltas — e.g. "twenty five"
# (10 chars) → "25" (2 chars), or "um uh so I went to the store" (25 chars)
# → "I went to the store." (19 chars, CER ~0.4) — all of which are *valid*
# cleanup we want to KEEP. A hallucinated full-sentence rewrite typically
# scores CER ≥ 0.85, so 0.75 cleanly separates the two regimes.
_MAX_CER = 0.75

# Minimum / maximum length ratio (cleaned / raw) — guards against the LLM
# deleting everything or expanding wildly.
_MIN_LEN_RATIO = 0.40
_MAX_LEN_RATIO = 2.00

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TrainingExample:
    """A single (audio, cleaned_text) training example."""

    audio_path: str
    text: str  # cleaned / formatted text — SFT target
    raw_text: str  # verbatim Whisper transcript
    duration: float  # seconds
    source: str  # dataset name
    augment: str = "1.0x"  # augmentation descriptor, e.g. "0.9x", "1.0x+noise20"

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class AudioClip:
    """A discovered raw audio clip + its verbatim transcript (if any)."""

    audio_path: Path
    raw_transcript: Optional[str]  # ground-truth verbatim, if available
    source: str


# ---------------------------------------------------------------------------
# Audio discovery — per-format walkers
# ---------------------------------------------------------------------------


def discover_clips(
    input_dir: Path,
    fmt: str,
    cv_tsv: Optional[Path] = None,
) -> Iterator[AudioClip]:
    """Walk an input directory and yield :class:`AudioClip` records.

    Parameters
    ----------
    input_dir
        Root of the corpus.
    fmt
        One of ``"custom"``, ``"librispeech"``, ``"commonvoice"``.
    cv_tsv
        Path to the Common Voice ``train.tsv`` (only used when
        ``fmt == "commonvoice"``).

    Yields
    ------
    AudioClip
        Each clip with its verbatim transcript if the format provides one.
    """
    if fmt == "custom":
        yield from _discover_custom(input_dir)
    elif fmt == "librispeech":
        yield from _discover_librispeech(input_dir)
    elif fmt == "commonvoice":
        if cv_tsv is None:
            raise ValueError("--cv-tsv is required when --format commonvoice")
        yield from _discover_common_voice(input_dir, cv_tsv)
    else:
        raise ValueError(f"unknown --format: {fmt!r}")


_AUDIO_EXTS = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus"}


def _discover_custom(input_dir: Path) -> Iterator[AudioClip]:
    """Walk a custom directory of ``{name.<audio>, name.txt}`` pairs.

    The sidecar ``.txt`` (if present) is the *ground-truth verbatim*
    transcript. It is used as a fallback if Whisper is unavailable; otherwise
    Whisper Large v3 is run on the audio to produce the teacher verbatim
    (the sidecar may be lower-quality than Whisper-L-v3, and the FormalASR
    recipe calls for the strongest available ASR as the teacher).
    """
    for audio_path in sorted(input_dir.rglob("*")):
        if audio_path.suffix.lower() not in _AUDIO_EXTS:
            continue
        if not audio_path.is_file():
            continue
        sidecar = audio_path.with_suffix(".txt")
        raw = sidecar.read_text(encoding="utf-8").strip() if sidecar.is_file() else None
        yield AudioClip(
            audio_path=audio_path,
            raw_transcript=raw,
            source="custom",
        )


def _discover_librispeech(input_dir: Path) -> Iterator[AudioClip]:
    """Walk a LibriSpeech directory tree.

    Expected layout::

        <root>/<split>/<speaker>/<chapter>/<uuid>.flac
        <root>/<split>/<speaker>/<chapter>/<speaker>-<chapter>.trans.txt

    The ``.trans.txt`` file contains ``<uuid> <transcript>`` lines for every
    clip in the chapter.
    """
    for trans_txt in sorted(input_dir.rglob("*.trans.txt")):
        chapter_dir = trans_txt.parent
        try:
            entries = _parse_librispeech_trans(trans_txt)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("skipping malformed %s: %s", trans_txt, exc)
            continue
        for uuid, text in entries:
            audio_path = chapter_dir / f"{uuid}.flac"
            if not audio_path.is_file():
                continue
            yield AudioClip(
                audio_path=audio_path,
                raw_transcript=text,
                source="librispeech",
            )


def _parse_librispeech_trans(trans_txt: Path) -> list[Tuple[str, str]]:
    out: list[Tuple[str, str]] = []
    for line in trans_txt.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        uuid, _, text = line.partition(" ")
        if not uuid or not text:
            continue
        out.append((uuid, text.strip()))
    return out


def _discover_common_voice(input_dir: Path, cv_tsv: Path) -> Iterator[AudioClip]:
    """Walk a Mozilla Common Voice dataset.

    Expected layout::

        <root>/clips/<uuid>.mp3
        <cv_tsv> = <root>/<split>.tsv  (columns: path, sentence, ...)

    Only the ``path`` and ``sentence`` columns are read.
    """
    clips_dir = input_dir / "clips"
    if not clips_dir.is_dir():
        # Allow input_dir itself to be the clips dir.
        clips_dir = input_dir
    with cv_tsv.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if "path" not in (reader.fieldnames or []) or "sentence" not in (
            reader.fieldnames or []
        ):
            raise ValueError(
                f"{cv_tsv}: expected columns 'path' and 'sentence', "
                f"got {reader.fieldnames!r}"
            )
        for row in reader:
            rel = row["path"].strip()
            sentence = (row.get("sentence") or "").strip()
            if not rel:
                continue
            audio_path = clips_dir / rel
            if not audio_path.is_file():
                continue
            yield AudioClip(
                audio_path=audio_path,
                raw_transcript=sentence or None,
                source="commonvoice",
            )


# ---------------------------------------------------------------------------
# Teacher ASR — Whisper Large v3 via faster-whisper
# ---------------------------------------------------------------------------


class WhisperTeacher:
    """Wraps ``faster_whisper.WhisperModel`` for the verbatim teacher pass.

    ``faster-whisper`` is used (rather than ``transformers``) because it is
    ~5× faster on the same GPU via CTranslate2, which matters when processing
    hundreds of hours of audio. The model is lazily loaded on first use so
    that ``--help`` and dry-run discovery don't require a GPU.
    """

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model: Any = None  # faster_whisper.WhisperModel

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "faster-whisper is not installed. Install it in the training "
                "venv:  pip install faster-whisper"
            ) from exc
        LOG.info("loading whisper-%s on %s (%s)…", self.model_size, self.device, self.compute_type)
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        return self._model

    def transcribe(self, audio_path: Path) -> Tuple[str, float]:
        """Transcribe an audio file and return ``(text, duration_seconds)``.

        Duration is read from the file header (cheap) — we do not trust the
        Whisper segment timestamps for total duration because of VAD padding.
        """
        model = self._load()
        duration = _audio_duration(audio_path)
        segments, _info = model.transcribe(
            str(audio_path),
            beam_size=5,
            language="en",
            vad_filter=True,
            # Disable all Whisper-internal post-processing — we want verbatim.
            condition_on_previous_text=False,
            initial_prompt=None,
            word_timestamps=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text, duration


def _audio_duration(path: Path) -> float:
    """Return audio duration in seconds, cheaply.

    Uses the standard-library ``wave`` module for WAV files (no deps) and
    falls back to ``mutagen`` / ``librosa`` if installed for other formats.
    Returns 0.0 on failure rather than raising — duration is metadata only.
    """
    suffix = path.suffix.lower()
    if suffix == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                return wf.getnframes() / float(wf.getframerate() or TARGET_SAMPLE_RATE)
        except Exception:  # noqa: BLE001
            return 0.0
    # Fall back to librosa if available.
    try:
        import librosa  # type: ignore

        return float(librosa.get_duration(path=str(path)))
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# Teacher LLM cleanup — z-ai CLI or OpenAI-compatible HTTP
# ---------------------------------------------------------------------------


class LLMCleaner:
    """LLM cleanup teacher, callable interface.

    Two backends are supported:

    - ``z-ai`` (default): shells out to the ``z-ai chat`` CLI, which is the
      Python-callable surface of the in-house ``z-ai-web-dev-sdk``. No API
      key required in the sandboxed environment.
    - ``openai``: HTTP POST to an OpenAI-compatible ``/v1/chat/completions``
      endpoint (set ``--llm-base-url`` and ``--llm-api-key``).
    """

    def __init__(
        self,
        backend: str = "z-ai",
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        self.backend = backend
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    def cleanup(self, raw_text: str) -> str:
        """Return the cleaned version of ``raw_text``."""
        raw_text = (raw_text or "").strip()
        if not raw_text:
            return ""
        if self.backend == "z-ai":
            return self._cleanup_via_zai_cli(raw_text)
        if self.backend == "openai":
            return self._cleanup_via_openai(raw_text)
        raise ValueError(f"unknown --llm-backend: {self.backend!r}")

    # --- z-ai CLI backend -------------------------------------------------

    def _cleanup_via_zai_cli(self, raw_text: str) -> str:
        """Invoke ``z-ai chat`` via subprocess and return the cleaned text.

        We write the raw transcript to a temp file rather than passing it on
        the command line to avoid shell-escaping issues with quotes /
        backslashes / newlines, and to stay within argv length limits.
        """
        cmd = [
            "z-ai",
            "chat",
            "--system",
            CLEANUP_SYSTEM_PROMPT,
            "--prompt",
            raw_text,
        ]
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "the 'z-ai' CLI was not found on PATH. Install the "
                    "z-ai-web-dev-sdk CLI or use --llm-backend openai."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                last_exc = exc
                LOG.warning("z-ai chat timed out (attempt %d/%d)", attempt, self.max_retries)
                time.sleep(1.0 * attempt)
                continue
            if proc.returncode != 0:
                last_exc = RuntimeError(
                    f"z-ai chat exited {proc.returncode}: "
                    f"{proc.stderr.strip()[:500]}"
                )
                LOG.warning("z-ai chat failed (attempt %d/%d): %s", attempt, self.max_retries, last_exc)
                time.sleep(1.0 * attempt)
                continue
            # The z-ai CLI prints a banner + the response. The response is
            # the last non-empty line(s) after the "🚀 Initializing" banner.
            cleaned = _extract_zai_response(proc.stdout)
            return cleaned
        if last_exc is not None:
            raise last_exc
        return ""

    # --- OpenAI-compatible HTTP backend -----------------------------------

    def _cleanup_via_openai(self, raw_text: str) -> str:
        """POST to ``/v1/chat/completions`` and return the assistant message."""
        import urllib.error
        import urllib.request

        if not self.base_url:
            raise ValueError("--llm-base-url is required when --llm-backend openai")
        if not self.api_key:
            # Allow env-var fallback so secrets don't appear in `ps`.
            self.api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        if not self.api_key:
            raise ValueError(
                "--llm-api-key (or OPENAI_API_KEY env var) is required "
                "when --llm-backend openai"
            )

        url = self.base_url.rstrip("/") + "/v1/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": CLEANUP_SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                last_exc = exc
                LOG.warning("openai call failed (attempt %d/%d): %s", attempt, self.max_retries, exc)
                time.sleep(1.0 * attempt)
        if last_exc is not None:
            raise last_exc
        return ""


def _extract_zai_response(stdout: str) -> str:
    """Parse the ``z-ai chat`` CLI stdout to recover the assistant reply.

    The CLI prints a banner (``🚀 Initializing Z-AI SDK…``) followed by the
    model's reply. We strip the banner and any leading/trailing whitespace,
    and drop a trailing ```` ``` ```` fence if present.
    """
    # Drop everything up to and including the last banner line.
    lines = stdout.splitlines()
    cut = 0
    for i, line in enumerate(lines):
        if "Initializing Z-AI SDK" in line or "Chat Completion" in line:
            cut = i + 1
    body = "\n".join(lines[cut:]).strip()
    # Strip a single surrounding code fence if the model added one despite
    # the prompt forbidding it.
    if body.startswith("```") and body.endswith("```"):
        body = body[3:-3].strip()
    elif body.startswith("```"):
        # Opening fence with no close — drop the first line.
        body = "\n".join(body.splitlines()[1:]).strip()
    return body


# ---------------------------------------------------------------------------
# Quality filter (FormalASR §3.2)
# ---------------------------------------------------------------------------


def _char_error_rate(ref: str, hyp: str) -> float:
    """Levenshtein character error rate. Pure-Python, no deps."""
    if not ref:
        return 0.0 if not hyp else 1.0
    r, h = ref, hyp
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, start=1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, start=1):
            cost = 0 if rc == hc else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1] / len(ref)


def passes_quality_filter(raw: str, cleaned: str) -> Tuple[bool, str]:
    """Return ``(ok, reason)`` for a teacher-produced pair.

    Implements the FormalASR quality filter: drop pairs whose cleaned text is
    implausibly short / long, contains LLM preamble, has a code fence,
    preserves no digit overlap, or whose character-error-rate vs raw exceeds
    :data:`_MAX_CER`.
    """
    if not cleaned:
        return False, "empty_cleaned"
    if not any(c.isascii() and c.isalpha() for c in cleaned):
        return False, "no_letters"
    low = cleaned.lower()
    for prefix in _LLM_PREAMBLE_PREFIXES:
        if low.startswith(prefix):
            return False, f"preamble:{prefix}"
    if "```" in cleaned:
        return False, "code_fence"
    ratio = len(cleaned) / max(len(raw), 1)
    if ratio < _MIN_LEN_RATIO:
        return False, f"too_short:{ratio:.2f}"
    if ratio > _MAX_LEN_RATIO:
        return False, f"too_long:{ratio:.2f}"
    # Digit preservation: every digit-run in `raw` must appear in `cleaned`.
    raw_digits = set(re.findall(r"\d[\d,.\-:]*", raw))
    for run in raw_digits:
        if run not in cleaned:
            return False, f"digit_dropped:{run}"
    cer = _char_error_rate(raw, cleaned)
    if cer > _MAX_CER:
        return False, f"cer_too_high:{cer:.2f}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Augmentation — speed perturbation + noise injection
# ---------------------------------------------------------------------------


def _load_audio_pcm(path: Path, target_sr: int = TARGET_SAMPLE_RATE) -> Tuple[Any, int]:
    """Load audio as a float32 mono numpy array at ``target_sr``."""
    import librosa  # type: ignore

    y, sr = librosa.load(str(path), sr=target_sr, mono=True)
    return y, sr


def _write_wav(path: Path, y: Any, sr: int) -> None:
    """Write a float32 numpy array to a 16-bit PCM WAV file."""
    import numpy as np  # type: ignore
    import soundfile as sf  # type: ignore

    y = np.clip(y, -1.0, 1.0)
    sf.write(str(path), y, sr, subtype="PCM_16")


def augment_clip(
    audio_path: Path,
    out_dir: Path,
    speed_factors: Sequence[float],
    noise_snrs: Sequence[float],
    rng_seed: int = 0,
) -> list[Tuple[Path, str]]:
    """Produce augmented variants of ``audio_path``.

    Returns a list of ``(augmented_audio_path, augment_descriptor)`` tuples.
    The original (1.0×, clean) is always included as the first element if
    ``1.0`` is in ``speed_factors``.

    Augmentation uses ``librosa`` for resampling (phase vocoder) and additive
    Gaussian noise scaled to the target SNR.
    """
    import numpy as np  # type: ignore

    if not speed_factors and not noise_snrs:
        return [(audio_path, "1.0x")]

    out_dir.mkdir(parents=True, exist_ok=True)
    y, sr = _load_audio_pcm(audio_path)
    rng = np.random.default_rng(rng_seed)

    results: list[Tuple[Path, str]] = []
    stem = audio_path.stem

    # If no speed factors given, default to 1.0 only.
    factors = list(speed_factors) if speed_factors else [1.0]
    snrs = list(noise_snrs) if noise_snrs else [None]  # type: ignore

    for factor in factors:
        import librosa  # type: ignore

        if abs(factor - 1.0) < 1e-3:
            y_speed = y
            speed_tag = "1.0x"
        else:
            y_speed = librosa.resample(
                y, orig_sr=sr, target_sr=int(round(sr * factor)), res_type="kaiser_fast"
            )
            # librosa.resample changes length; tag reflects the factor.
            speed_tag = f"{factor:.2f}x"
        for snr in snrs:
            if snr is None:
                y_aug = y_speed
                tag = speed_tag
            else:
                # Additive Gaussian noise at target SNR.
                signal_power = float(np.mean(y_speed ** 2)) + 1e-12
                noise_power = signal_power / (10.0 ** (float(snr) / 10.0))
                noise = rng.normal(0.0, np.sqrt(noise_power), size=y_speed.shape).astype(
                    y_speed.dtype
                )
                y_aug = y_speed + noise
                tag = f"{speed_tag}+noise{int(snr)}"
            out_path = out_dir / f"{stem}__{tag.replace('.', 'p').replace('+', '_')}.wav"
            _write_wav(out_path, y_aug, sr)
            results.append((out_path, tag))
    return results


# ---------------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------------


def _load_processed_keys(output_path: Path) -> set[str]:
    """Return the set of audio paths already present in ``output_path``."""
    if not output_path.is_file():
        return set()
    seen: set[str] = set()
    with output_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = rec.get("audio_path", "")
            if key:
                seen.add(key)
    return seen


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """Entry point — returns a process exit code."""
    logging.basicConfig(
        level=logging.INFO if not args.quiet else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    input_dir = Path(args.input_dir).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_dir.is_dir():
        LOG.error("--input-dir %s does not exist or is not a directory", input_dir)
        return 2

    augment_dir: Optional[Path] = None
    if args.augment:
        augment_dir = output_path.parent / f"{output_path.stem}_augmented"
        augment_dir.mkdir(parents=True, exist_ok=True)

    # Teacher components — lazily constructed so --help / dry-runs are cheap.
    whisper: Optional[WhisperTeacher] = None
    if not args.no_whisper:
        whisper = WhisperTeacher(
            model_size=args.whisper_model,
            device=args.whisper_device,
            compute_type=args.whisper_compute,
        )
    cleaner = LLMCleaner(
        backend=args.llm_backend,
        model=args.llm_model,
        base_url=args.llm_base_url,
        api_key=args.llm_api_key,
        timeout=args.llm_timeout,
        max_retries=args.llm_retries,
    )

    seen = _load_processed_keys(output_path) if args.resume else set()
    if seen:
        LOG.info("resume: %d clips already processed", len(seen))

    # Open output in append mode so resume works.
    mode = "a" if (args.resume and output_path.is_file()) else "w"
    n_written = 0
    n_skipped = 0
    n_failed = 0
    LOG.info("discovering clips in %s (format=%s)…", input_dir, args.format)
    with output_path.open(mode, encoding="utf-8") as out_fh:
        for clip in discover_clips(input_dir, args.format, args.cv_tsv):
            if args.max_samples is not None and n_written >= args.max_samples:
                LOG.info("reached --max-samples=%d, stopping", args.max_samples)
                break
            key = str(clip.audio_path)
            if key in seen:
                n_skipped += 1
                continue

            try:
                example = _process_one_clip(
                    clip=clip,
                    whisper=whisper,
                    cleaner=cleaner,
                    augment=args.augment,
                    augment_dir=augment_dir,
                    speed_factors=args.speed_factors,
                    noise_snrs=args.noise_snrs,
                    use_sidecar_if_no_whisper=args.use_sidecar_if_no_whisper,
                )
            except Exception as exc:  # noqa: BLE001
                n_failed += 1
                LOG.warning("failed %s: %s", clip.audio_path, exc)
                continue

            if example is None:
                n_skipped += 1
                continue

            for ex in example:
                out_fh.write(ex.to_jsonl() + "\n")
                out_fh.flush()
                n_written += 1
            if n_written % 50 == 0:
                LOG.info(
                    "progress: written=%d skipped=%d failed=%d (last=%s)",
                    n_written,
                    n_skipped,
                    n_failed,
                    clip.audio_path.name,
                )

    LOG.info(
        "done: written=%d skipped=%d failed=%d → %s",
        n_written,
        n_skipped,
        n_failed,
        output_path,
    )
    return 0


def _process_one_clip(
    *,
    clip: AudioClip,
    whisper: Optional[WhisperTeacher],
    cleaner: LLMCleaner,
    augment: bool,
    augment_dir: Optional[Path],
    speed_factors: Sequence[float],
    noise_snrs: Sequence[float],
    use_sidecar_if_no_whisper: bool,
) -> Optional[list[TrainingExample]]:
    """Process a single clip through the teacher pipeline.

    Returns a list of :class:`TrainingExample` (one per augmentation variant,
    or a single-element list if augmentation is disabled), or ``None`` if the
    clip was filtered out.
    """
    # 1. Verbatim teacher ASR pass.
    if whisper is not None:
        raw_text, duration = whisper.transcribe(clip.audio_path)
    elif use_sidecar_if_no_whisper and clip.raw_transcript:
        raw_text = clip.raw_transcript
        duration = _audio_duration(clip.audio_path)
    else:
        raise RuntimeError(
            "no Whisper teacher and no sidecar transcript available; "
            "pass --use-sidecar-if-no-whisper or install faster-whisper"
        )

    raw_text = (raw_text or "").strip()
    if not raw_text:
        return None

    # 2. LLM cleanup teacher pass.
    cleaned = cleaner.cleanup(raw_text).strip()
    if not cleaned:
        return None

    # 3. Quality filter.
    ok, reason = passes_quality_filter(raw_text, cleaned)
    if not ok:
        LOG.debug("filter %s: %s (raw=%r clean=%r)", clip.audio_path.name, reason, raw_text, cleaned)
        return None

    # 4. Augmentation (optional).
    if augment and augment_dir is not None:
        variants = augment_clip(
            clip.audio_path,
            augment_dir,
            speed_factors=speed_factors,
            noise_snrs=noise_snrs,
            rng_seed=hash(str(clip.audio_path)) & 0xFFFFFFFF,
        )
    else:
        variants = [(clip.audio_path, "1.0x")]

    return [
        TrainingExample(
            audio_path=str(ap),
            text=cleaned,
            raw_text=raw_text,
            duration=duration,
            source=clip.source,
            augment=tag,
        )
        for ap, tag in variants
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prepare_data.py",
        description="Phase 2 teacher pipeline: build (audio, cleaned_text) "
        "JSONL training pairs for Moonshine Tiny SFT.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # I/O
    p.add_argument("--input-dir", required=True, help="Root of the audio corpus.")
    p.add_argument(
        "--format",
        choices=["custom", "librispeech", "commonvoice"],
        default="custom",
        help="Corpus layout. See discover_clips() for the expected directory "
        "structure of each.",
    )
    p.add_argument("--cv-tsv", default=None, help="Common Voice TSV (required for --format commonvoice).")
    p.add_argument("--output", required=True, help="Output JSONL path.")

    # Whisper teacher
    p.add_argument("--whisper-model", default="large-v3", help="faster-whisper model size.")
    p.add_argument("--whisper-device", default="cuda", choices=["cuda", "cpu", "auto"])
    p.add_argument("--whisper-compute", default="float16", help="CTranslate2 compute type.")
    p.add_argument(
        "--no-whisper",
        action="store_true",
        help="Skip Whisper teacher pass; require sidecar transcripts. "
        "Useful for LibriSpeech where ground-truth is higher quality than "
        "Whisper-L-v3 re-transcription.",
    )
    p.add_argument(
        "--use-sidecar-if-no-whisper",
        action="store_true",
        help="If --no-whisper is set, fall back to sidecar transcripts.",
    )

    # LLM cleanup teacher
    p.add_argument(
        "--llm-backend",
        choices=["z-ai", "openai"],
        default="z-ai",
        help="LLM cleanup backend. 'z-ai' shells out to the z-ai-web-dev-sdk "
        "CLI; 'openai' hits an OpenAI-compatible HTTP endpoint.",
    )
    p.add_argument("--llm-model", default="gpt-4o", help="Model name (OpenAI backend only).")
    p.add_argument("--llm-base-url", default=None, help="OpenAI-compatible base URL.")
    p.add_argument("--llm-api-key", default=None, help="API key (or OPENAI_API_KEY env var).")
    p.add_argument("--llm-timeout", type=float, default=60.0)
    p.add_argument("--llm-retries", type=int, default=3)

    # Augmentation
    p.add_argument("--augment", action="store_true", help="Enable offline augmentation.")
    p.add_argument(
        "--speed-factors",
        nargs="+",
        type=float,
        default=[0.9, 1.0, 1.1],
        help="Speed-perturbation factors. 1.0 = original pace.",
    )
    p.add_argument(
        "--noise-snrs",
        nargs="+",
        type=float,
        default=[20.0],
        help="Gaussian-noise SNRs in dB. Empty list = no noise injection.",
    )

    # Misc
    p.add_argument("--max-samples", type=int, default=None, help="Stop after writing N examples.")
    p.add_argument("--resume", action="store_true", help="Append to --output, skipping already-written audio paths.")
    p.add_argument("--quiet", action="store_true")

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
