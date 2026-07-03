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
    """Extract candidate proper nouns, file names, and technical terms from text."""
    if not text:
        return set()

    candidates: Set[str] = set()

    # 1. Technical identifiers: file.ext, snake_case, camelCase (e.g. daemon.py, user_id, WhisperFlow)
    tech_pattern = r"\b[a-zA-Z0-9_\-]+\.[a-zA-Z0-9]+\b|\b[a-z]+(?:_[a-z0-9]+)+\b|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b"
    for match in re.findall(tech_pattern, text):
        if len(match) > 2:
            candidates.add(match)

    # 2. Capitalized Proper Nouns (excluding sentence start if simple word)
    words = text.split()
    for i, word in enumerate(words):
        clean_word = re.sub(r"^[^\w]+|[^\w]+$", "", word)
        if not clean_word or len(clean_word) < 3:
            continue
        # Check if capitalized or acronym (e.g., PyTorch, GGUF, CUDA, Antigravity)
        if clean_word[0].isupper() or clean_word.isupper():
            # If it's the first word of a sentence, only include if mixed case or acronym
            if i == 0 or (i > 0 and words[i - 1].endswith((".", "!", "?"))):
                if any(c.isupper() for c in clean_word[1:]) or clean_word.isupper():
                    candidates.add(clean_word)
            else:
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
