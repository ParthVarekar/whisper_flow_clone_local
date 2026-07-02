"""Model discovery and selection.

Scans common directories for Whisper ggml .bin models, GGUF LLM models, and
Silero VAD ggml .bin models. Classifies by filename pattern + magic bytes.
Provides a `models` CLI subcommand for listing and interactive selection.

No third-party deps. Classification heuristics:
  - ggml-(tiny|base|small|medium|large|large-v3|large-v3-turbo)[.en]*.bin  -> Whisper
  - ggml-silero-v*.bin                                                    -> Silero VAD
  - *.gguf                                                                -> GGUF LLM
  - other *.bin                                                           -> unknown (warn)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

WHISPER_NAMES = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "small.en-tdrz", "medium", "medium.en", "large-v1", "large-v2",
    "large-v3", "large-v3-turbo", "large-v3-q5_0", "large-v3-turbo-q5_0",
    "large-v2-q5_0",
}
# filename pattern: ggml-<name>.bin  (name may include dots, hyphens, underscores)
_WHISPER_RE = re.compile(r"^ggml-([a-zA-Z0-9._-]+)\.bin$")
_VAD_RE = re.compile(r"^ggml-silero-v[\d.]+\.bin$", re.IGNORECASE)
_GGUF_RE = re.compile(r"\.gguf$", re.IGNORECASE)
# quantization tag inside GGUF filename, e.g. ...-Q4_K_M.gguf
_QUANT_RE = re.compile(r"-Q([0-9A-Za-z_]+)\.gguf$", re.IGNORECASE)
VALID_QUANTS = {
    "F16", "F32", "BF16",
    "Q8_0", "Q6_K", "Q5_K_M", "Q5_K_S", "Q5_0", "Q5_1",
    "Q4_K_M", "Q4_K_S", "Q4_0", "Q4_1",
    "Q3_K_M", "Q3_K_S", "Q3_K_L", "Q2_K",
    "IQ4_NL", "IQ3_M", "IQ2_XXS", "IQ2_XS",
}


@dataclass
class ModelInfo:
    path: str
    kind: str           # "whisper" | "gguf" | "vad" | "unknown"
    name: str           # basename without extension
    size_mb: float
    detail: str = ""    # e.g. "base.en" or "Q4_K_M" or "silero-v6.2.0"
    warning: str = ""   # non-empty if a concern was detected


def _classify(path: str) -> ModelInfo:
    base = os.path.basename(path)
    size_mb = 0.0
    try:
        size_mb = os.path.getsize(path) / (1024 * 1024)
    except OSError:
        pass
    name_no_ext = os.path.splitext(base)[0]

    m = _VAD_RE.match(base)
    if m:
        return ModelInfo(path=path, kind="vad", name=name_no_ext, size_mb=size_mb,
                         detail=base.replace("ggml-", "").replace(".bin", ""))

    m = _WHISPER_RE.match(base)
    if m:
        whisper_name = m.group(1)
        warning = ""
        if whisper_name not in WHISPER_NAMES:
            warning = f"unrecognized Whisper model name {whisper_name!r} (expected one of: {', '.join(sorted(WHISPER_NAMES))})"
        return ModelInfo(path=path, kind="whisper", name=name_no_ext, size_mb=size_mb,
                         detail=whisper_name, warning=warning)

    if _GGUF_RE.search(base):
        qm = _QUANT_RE.search(base)
        quant = qm.group(1) if qm else ""  # without the leading 'Q' (regex captures after Q)
        warning = ""
        if quant:
            quant_with_q = "Q" + quant.upper()
            if quant_with_q not in VALID_QUANTS:
                warning = f"unknown quantization tag {quant_with_q} (filename may be non-standard)"
        return ModelInfo(path=path, kind="gguf", name=name_no_ext, size_mb=size_mb,
                         detail=f"Q{quant}" if quant else "", warning=warning)

    if base.endswith(".bin"):
        return ModelInfo(path=path, kind="unknown", name=name_no_ext, size_mb=size_mb,
                         warning=".bin file that doesn't match Whisper or Silero VAD naming")

    return ModelInfo(path=path, kind="unknown", name=name_no_ext, size_mb=size_mb)


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------

def default_model_dirs() -> list[str]:
    """Directories scanned by default (existing only)."""
    candidates = [
        os.path.join(os.getcwd(), "models"),
        os.path.join(os.getcwd(), "third_party", "whisper.cpp", "models"),
        os.path.expanduser("~/.cache/whisper.cpp"),
        os.path.expanduser("~/.cache/whisper-flow"),
        os.path.expanduser("~/.local/share/whisper-flow/models"),
        os.environ.get("WHISPER_FLOW_MODELS_DIR", ""),
    ]
    seen: set[str] = set()
    dirs: list[str] = []
    for d in candidates:
        if not d:
            continue
        d = os.path.abspath(os.path.expanduser(d))
        if d in seen:
            continue
        seen.add(d)
        if os.path.isdir(d):
            dirs.append(d)
    return dirs


def scan_dirs(dirs: Iterable[str]) -> list[ModelInfo]:
    """Scan directories for model files. Returns flat sorted list."""
    found: list[ModelInfo] = []
    seen_paths: set[str] = set()
    for d in dirs:
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for entry in entries:
            full = os.path.join(d, entry)
            if not os.path.isfile(full):
                continue
            if entry.startswith("."):
                continue
            rp = os.path.realpath(full)
            if rp in seen_paths:
                continue
            seen_paths.add(rp)
            found.append(_classify(full))
    found.sort(key=lambda m: (m.kind, m.name.lower()))
    return found


def list_models(extra_dirs: Optional[list[str]] = None) -> dict[str, list[ModelInfo]]:
    """Return {kind: [ModelInfo, ...]} from default + extra dirs."""
    dirs = default_model_dirs()
    if extra_dirs:
        dirs.extend(extra_dirs)
    models = scan_dirs(dirs)
    by_kind: dict[str, list[ModelInfo]] = {"whisper": [], "gguf": [], "vad": [], "unknown": []}
    for m in models:
        by_kind.setdefault(m.kind, []).append(m)
    return by_kind


# ---------------------------------------------------------------------------
# Interactive selection (CLI helper)
# ---------------------------------------------------------------------------

def pick_interactive(models: list[ModelInfo], prompt: str) -> Optional[ModelInfo]:
    """Numbered list on stdout; read choice from stdin. Returns None if empty/cancelled."""
    if not models:
        return None
    print(prompt)
    for i, m in enumerate(models, 1):
        warn = f"  ⚠ {m.warning}" if m.warning else ""
        print(f"  [{i}] {m.name}  ({m.size_mb:,.1f} MB){warn}")
    print("  [0] cancel")
    try:
        choice = input("choice> ").strip()
    except EOFError:
        return None
    if not choice or choice == "0":
        return None
    try:
        idx = int(choice)
    except ValueError:
        return None
    if 1 <= idx <= len(models):
        return models[idx - 1]
    return None


def render_table(by_kind: dict[str, list[ModelInfo]]) -> str:
    """Pretty-printable table for the `models` subcommand."""
    lines: list[str] = []
    titles = {"whisper": "Whisper (STT) models", "gguf": "GGUF (LLM) models",
              "vad": "VAD models", "unknown": "Unknown"}
    for kind in ("whisper", "gguf", "vad", "unknown"):
        rows = by_kind.get(kind, [])
        if not rows:
            continue
        lines.append(f"== {titles.get(kind, kind)} ==")
        if not rows:
            lines.append("  (none found)")
            continue
        for m in rows:
            detail = f"  [{m.detail}]" if m.detail else ""
            warn = f"  ⚠ {m.warning}" if m.warning else ""
            lines.append(f"  {m.path}  ({m.size_mb:,.1f} MB){detail}{warn}")
        lines.append("")
    if not lines:
        lines.append("No models found. Run scripts/download_models.sh first.")
        lines.append(f"Searched: {', '.join(default_model_dirs())}")
    return "\n".join(lines)
