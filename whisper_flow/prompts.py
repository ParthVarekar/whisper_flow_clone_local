"""Prompt templates for the LLM post-processing stage.

Each mode maps to a (system, user_prompt_template) pair. The user template
receives the transcript via `.format(transcript=...)`.

Modes:
  summarize  - concise summary of the transcript
  correct    - fix disfluencies, punctuation, filler words; preserve meaning
  polish     - rewrite into more confident, structured prose
  command    - extract a single shell-style command / structured intent
  assistant  - free-form assistant reply to the transcript
  raw        - no LLM (handled by pipeline, not here)
"""

from __future__ import annotations

SYSTEM_PROMPTS = {
    "summarize": (
        "You are a precise local transcription assistant. Summarize the provided "
        "speech transcript concisely in the same language as the transcript. "
        "Do not add information that is not in the transcript."
    ),
    "correct": (
        "You are a transcription cleanup assistant. Rewrite the provided transcript "
        "into clean, well-punctuated prose. Remove filler words and disfluencies "
        "(um, uh, like) and fix obvious recognition errors, but preserve the "
        "speaker's meaning and language. Output only the cleaned text."
    ),
    "polish": (
        "You are a speech polishing assistant. Rewrite the provided transcript into "
        "clear, confident, well-structured prose. Remove stuttering, filler words, "
        "false starts, and repetition, while preserving the speaker's meaning, "
        "intent, and language. Output only the polished text."
    ),
    "medium": (
        "You are a dictation cleanup assistant. Rewrite the provided transcript into "
        "clear, concise, natural prose. Remove filler words, false starts, minor "
        "recognition noise, and repetition while preserving the speaker's meaning, "
        "tone, and structure. Output only the cleaned text."
    ),
    "command": (
        "You are a command extraction assistant. From the provided transcript, "
        "extract the single most likely shell command or a short JSON intent "
        "object that captures what the user wants to do. If the transcript is "
        "not a command, reply with exactly: NO_COMMAND. Output only the result."
    ),
    "assistant": (
        "You are a helpful local assistant. The user spoke the following "
        "transcript. Respond helpfully and concisely in the same language."
    ),
}

USER_TEMPLATES = {
    "summarize": "Transcript:\n\"\"\"\n{transcript}\n\"\"\"\n\nProvide a concise summary.",
    "correct": "Transcript:\n\"\"\"\n{transcript}\n\"\"\"\n\nProvide the cleaned transcript.",
    "polish": "Transcript:\n\"\"\"\n{transcript}\n\"\"\"\n\nProvide the polished transcript.",
    "medium": "Transcript:\n\"\"\"\n{transcript}\n\"\"\"\n\nProvide the cleaned transcript.",
    "command": "Transcript:\n\"\"\"\n{transcript}\n\"\"\"\n\nExtract the command or intent.",
    "assistant": "Transcript:\n\"\"\"\n{transcript}\n\"\"\"",
}

ALIASES = {
    "none": "raw",
    "light": "correct",
    "high": "polish",
    "summary": "summarize",
}

VALID_MODES = {"summarize", "correct", "polish", "medium", "command", "assistant", "raw",
               "none", "light", "high", "summary"}


def resolve_mode(mode: str) -> str:
    return ALIASES.get(mode, mode)


def build_prompt(mode: str, transcript: str) -> tuple[str, str]:
    """Return (system, user) prompt strings for the given mode."""
    mode = resolve_mode(mode)
    if mode == "raw":
        return "", transcript
    if mode not in SYSTEM_PROMPTS:
        raise ValueError(f"unknown mode: {mode!r}")
    system = SYSTEM_PROMPTS[mode]
    user = USER_TEMPLATES[mode].format(transcript=transcript)
    return system, user
