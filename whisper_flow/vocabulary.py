"""Dynamic Learned Vocabulary manager for whisper_flow.

Automatically extracts proper nouns, technical terms, file names, and camelCase/snake_case
identifiers from user dictations and saves them persistently to
~/.config/whisper-flow/learned_vocabulary.json.
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Set

_DEFAULT_VOCAB_DIR = os.path.join(
    os.path.expanduser("~"), ".config", "whisper-flow"
)
_VOCAB_FILE = "learned_vocabulary.json"


def _vocab_path(vocab_dir: str | None = None) -> str:
    d = vocab_dir or _DEFAULT_VOCAB_DIR
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, _VOCAB_FILE)


def load_learned_vocabulary(vocab_dir: str | None = None) -> List[str]:
    """Load persistently learned vocabulary terms sorted by frequency."""
    path = _vocab_path(vocab_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                # Sort words by frequency (descending)
                sorted_words = sorted(data.keys(), key=lambda k: data[k], reverse=True)
                return sorted_words
            elif isinstance(data, list):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def extract_candidate_terms(text: str) -> Set[str]:
    """Extract candidate proper nouns, file names, and technical terms from text.

    CONSERVATIVE EXTRACTION — only high-confidence terms:
    - File extensions: daemon.py, config.toml (very unlikely to be ASR errors)
    - snake_case: whisper_flow, user_id (very unlikely to be ASR errors)
    - camelCase: WhisperFlow, PyTorch (very unlikely to be ASR errors)
    - ALL-CAPS acronyms: GGUF, CUDA, API (3+ chars, unlikely to be ASR errors)

    NOT extracted (too risky — could be ASR mishearings):
    - Simple capitalized words: "Quan", "Moonshine", "This"
      These could be mishearings (e.g., "Qwen" → "Quan") and would create
      a feedback loop that reinforces errors in the learned vocabulary.
    """
    if not text:
        return set()

    candidates: Set[str] = set()

    # 1. Technical identifiers: file.ext, snake_case, camelCase
    # These patterns are very unlikely to occur in natural speech, so if they
    # appear in the transcript, they're almost certainly correct.
    tech_pattern = (
        r"\b[a-zA-Z0-9_\-]+\.[a-zA-Z0-9]+\b"  # file.ext: daemon.py, config.toml
        r"|\b[a-z]+(?:_[a-z0-9]+)+\b"          # snake_case: whisper_flow, user_id
        r"|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b"  # camelCase: WhisperFlow, PyTorch
    )
    for match in re.findall(tech_pattern, text):
        if len(match) > 2:
            candidates.add(match)

    # 2. ALL-CAPS acronyms (3+ chars): GGUF, CUDA, API, HTTP
    # These are unlikely to be ASR errors because natural speech rarely
    # produces all-caps output. Only match if ALL characters are uppercase.
    for word in text.split():
        clean_word = re.sub(r"^[^\w]+|[^\w]+$", "", word)
        if not clean_word or len(clean_word) < 3:
            continue
        if clean_word.isupper() and clean_word.isalpha():
            candidates.add(clean_word)

    return candidates


def update_learned_vocabulary(text: str, vocab_dir: str | None = None) -> List[str]:
    """Extract terms from text, update frequency counts, and return full vocabulary list."""
    terms = extract_candidate_terms(text)
    if not terms:
        return load_learned_vocabulary(vocab_dir)

    path = _vocab_path(vocab_dir)
    vocab_map: dict[str, int] = {}

    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    vocab_map = data
                elif isinstance(data, list):
                    vocab_map = {w: 1 for w in data}
        except (OSError, json.JSONDecodeError):
            vocab_map = {}

    for term in terms:
        vocab_map[term] = vocab_map.get(term, 0) + 1

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(vocab_map, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

    sorted_words = sorted(vocab_map.keys(), key=lambda k: vocab_map[k], reverse=True)
    return sorted_words
