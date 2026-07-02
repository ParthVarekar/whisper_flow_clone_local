"""Lightweight Smart Formatting / Backtrack helpers for dictation text."""

from __future__ import annotations

import re


_PUNCT_WORDS = [
    (r"\bquestion mark\b", "?"),
    (r"\bexclamation point\b", "!"),
    (r"\bexclamation mark\b", "!"),
    (r"\bfull stop\b", "."),
    (r"\bperiod\b", "."),
    (r"\bcomma\b", ","),
    (r"\bsemicolon\b", ";"),
    (r"\bsemi colon\b", ";"),
    (r"\bcolon\b", ":"),
]

_NEWLINE_WORDS = [
    (r"\bnew paragraph\b", "\n\n"),
    (r"\bparagraph break\b", "\n\n"),
    (r"\bnew line\b", "\n"),
    (r"\bnext line\b", "\n"),
    (r"\bline break\b", "\n"),
]

_BACKTRACK_MARKERS = (
    "actually",
    "i mean",
    "sorry",
    "scratch that",
    "rather",
    "no wait",
)


def apply_smart_formatting(text: str, *, writing_style: str = "default") -> str:
    """Apply lightweight formatting rules inspired by Wispr Flow behavior."""
    out = str(text or "").strip()
    if not out:
        return ""

    press_enter = bool(re.search(r"\bpress enter\b\s*$", out, flags=re.IGNORECASE))
    out = _apply_newlines(out)
    out = _apply_punctuation_words(out)
    out = _apply_backtrack(out)
    out = _apply_press_enter(out)
    out = _normalize_spacing(out)
    out = _apply_writing_style(out, writing_style)
    out = out.rstrip(" \t")
    if press_enter and not out.endswith("\n"):
        out += "\n"
    return out


def _apply_newlines(text: str) -> str:
    out = text
    for pattern, repl in _NEWLINE_WORDS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def _apply_punctuation_words(text: str) -> str:
    out = text
    for pattern, repl in _PUNCT_WORDS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def _apply_backtrack(text: str) -> str:
    sentences = re.split(r"([.!?]\s+)", text)
    rebuilt: list[str] = []
    for part in sentences:
        lowered = part.lower()
        marker = next((m for m in _BACKTRACK_MARKERS if m in lowered), "")
        if not marker:
            rebuilt.append(part)
            continue
        idx = lowered.find(marker)
        suffix = part[idx + len(marker):].strip(" ,")
        prefix = part[:idx].strip(" ,")
        if len(suffix.split()) >= 2:
            rebuilt.append(suffix)
        else:
            rebuilt.append(prefix)
    return "".join(rebuilt)


def _apply_press_enter(text: str) -> str:
    out = re.sub(r"\bpress enter\b$", "", text, flags=re.IGNORECASE).rstrip()
    if out != text.rstrip():
        return out + "\n"
    return text


def _normalize_spacing(text: str) -> str:
    out = text
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n[ \t]+", "\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([,.;:!?])(?=[^\s\n])", r"\1 ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _apply_writing_style(text: str, writing_style: str) -> str:
    style = str(writing_style or "default").strip().lower()
    out = text.strip()
    if not out:
        return out
    if style in {"formal", "academic"}:
        if out and out[0].islower():
            out = out[0].upper() + out[1:]
        if out[-1] not in ".!?":
            out += "."
        return out
    if style in {"casual", "very_casual"}:
        if out.endswith("."):
            out = out[:-1]
        if style == "very_casual":
            out = out.replace(" do not ", " don't ")
            out = out.replace(" cannot ", " can't ")
            out = out.replace("I am ", "I'm ")
        return out
    if style == "enthusiastic":
        if out and out[0].islower():
            out = out[0].upper() + out[1:]
        if out[-1] == ".":
            out = out[:-1] + "!"
        elif out[-1] not in ".!?":
            out += "!"
        return out
    return out
