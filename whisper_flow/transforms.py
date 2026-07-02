"""Command Mode / Transforms for whisper-flow.

Implements Wispr Flow's "Command Mode": select text → hotkey → speak instruction
→ text is rewritten in place using the LLM.

Built-in transforms:
  - Polish: improve clarity and conciseness
  - Concise: make shorter
  - Bullet points: convert to bullet list
  - Formal: rewrite in formal tone
  - Casual: rewrite casually

Custom transforms can be defined in config.
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Built-in transform definitions
# ---------------------------------------------------------------------------

BUILTIN_TRANSFORMS = {
    "polish": {
        "system": (
            "You are a writing assistant. Improve the clarity, conciseness, and "
            "readability of the provided text while preserving the original meaning "
            "and voice. Output only the improved text."
        ),
        "user": "Text to improve:\n\"\"\"\n{selected}\n\"\"\"\n\nProvide the polished version.",
    },
    "concise": {
        "system": (
            "You are a conciseness assistant. Rewrite the provided text to be as "
            "short and clear as possible while preserving all essential meaning. "
            "Output only the shortened text."
        ),
        "user": "Text to shorten:\n\"\"\"\n{selected}\n\"\"\"\n\nProvide the concise version.",
    },
    "bullet_points": {
        "system": (
            "You are a formatting assistant. Convert the provided text into a "
            "clear, well-organized bullet point list. Output only the bullet points."
        ),
        "user": "Text to convert:\n\"\"\"\n{selected}\n\"\"\"\n\nProvide the bullet point version.",
    },
    "formal": {
        "system": (
            "You are a writing assistant. Rewrite the provided text in a formal, "
            "professional tone while preserving the meaning. Output only the "
            "rewritten text."
        ),
        "user": "Text to formalize:\n\"\"\"\n{selected}\n\"\"\"\n\nProvide the formal version.",
    },
    "casual": {
        "system": (
            "You are a writing assistant. Rewrite the provided text in a casual, "
            "friendly, conversational tone while preserving the meaning. "
            "Output only the rewritten text."
        ),
        "user": "Text to make casual:\n\"\"\"\n{selected}\n\"\"\"\n\nProvide the casual version.",
    },
}


def build_transform_prompt(
    selected_text: str,
    voice_instruction: str,
    *,
    transform_name: Optional[str] = None,
    custom_transforms: Optional[dict] = None,
) -> tuple[str, str]:
    """Build (system, user) prompt for a transform.

    If `transform_name` matches a built-in or custom transform, use its template.
    Otherwise, use the voice instruction as a free-form editing command.

    Args:
        selected_text: The text currently selected in the user's app.
        voice_instruction: What the user said (e.g., "make this shorter").
        transform_name: Optional name of a built-in/custom transform to use.
        custom_transforms: Optional dict of user-defined transforms from config.

    Returns:
        (system_prompt, user_prompt) tuple.
    """
    # Check if voice instruction matches a built-in transform name
    instr_lower = voice_instruction.strip().lower()
    for name, tmpl in BUILTIN_TRANSFORMS.items():
        if name in instr_lower or instr_lower.startswith(name):
            return (
                tmpl["system"],
                tmpl["user"].format(selected=selected_text),
            )

    # Check custom transforms
    if custom_transforms:
        for name, tmpl in custom_transforms.items():
            if name.lower() in instr_lower:
                return (
                    tmpl.get("system", "You are a helpful writing assistant. Output only the result."),
                    tmpl.get("user", "Text:\n\"\"\"\n{selected}\n\"\"\"\n\n{instruction}").format(
                        selected=selected_text, instruction=voice_instruction
                    ),
                )

    # Check explicit transform_name
    if transform_name:
        all_transforms = {**BUILTIN_TRANSFORMS}
        if custom_transforms:
            all_transforms.update(custom_transforms)
        tmpl = all_transforms.get(transform_name)
        if tmpl:
            return (
                tmpl["system"],
                tmpl["user"].format(selected=selected_text),
            )

    # Free-form: use voice instruction as the editing command
    system = (
        "You are a text editing assistant. The user has selected some text and "
        "given you a voice instruction about how to edit it. Apply the instruction "
        "to the selected text and output ONLY the resulting text. Do not explain "
        "what you did."
    )
    user = (
        f"Selected text:\n\"\"\"\n{selected_text}\n\"\"\"\n\n"
        f"Instruction: {voice_instruction}\n\n"
        f"Provide the edited text."
    )
    return system, user
