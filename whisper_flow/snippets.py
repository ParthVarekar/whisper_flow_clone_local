"""Voice-triggered text expansion (Snippets).

Wispr Flow's Snippets feature: say a trigger phrase during dictation and the
corresponding pre-defined text is expanded in its place.

Example config:
    [snippets]
    "my email" = "parth@example.com"
    "my address" = "123 Main Street, Anytown, USA 12345"
    "br" = "Best regards,\nParth"
"""

from __future__ import annotations

import re
from typing import Dict


def expand_snippets(text: str, snippets: Dict[str, str]) -> str:
    """Replace any snippet trigger phrases found in the text with their expansions.

    Matching is case-insensitive and whole-phrase (word-bounded).
    Longer trigger phrases are matched first to avoid partial matches.

    Args:
        text: The transcribed text to scan for triggers.
        snippets: Mapping of trigger phrase → expansion text.

    Returns:
        Text with triggers replaced by their expansions.
    """
    if not text or not snippets:
        return text

    result = text
    # Sort by length descending so longer phrases match first
    for trigger in sorted(snippets.keys(), key=len, reverse=True):
        expansion = snippets[trigger]
        # Case-insensitive whole-word match
        pattern = r"\b" + re.escape(trigger) + r"\b"
        result = re.sub(pattern, expansion, result, flags=re.IGNORECASE)

    return result
