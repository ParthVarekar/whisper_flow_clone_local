"""Zero-Click Auto-Intent Router (Wispr Flow 'Mind Reader' engine).

Analyzes spoken structure, keywords, and active application window context to
automatically route dictation to the most appropriate formatting mode without
requiring manual tray icon clicks.
"""
from __future__ import annotations

import re


def detect_auto_intent(transcript: str, app_category: str = "", app_name: str = "") -> str:
    """Classify the spoken transcript and context into a formatting mode.

    Returns one of: "smart_list", "email", "coding", "meeting_notes", "social",
    "transform", or "polish".
    """
    text = transcript.strip()
    lower = text.lower()
    app_cat = (app_category or "").lower()

    # 1. Check for explicit list enumeration patterns ("first... second...", etc.)
    list_keywords = [
        "bullet point", "bullet points", "numbered list", "smart list",
        "first of all", "number one,", "number two,", "first,", "second,", "third,",
        "here are the steps", "here are a few", "several reasons:",
    ]
    if any(kw in lower for kw in list_keywords):
        return "smart_list"
    # Or multiple numbered prefixes like "1." ... "2." in speech
    if len(re.findall(r"\b(?:firstly|secondly|thirdly|\d+\.)\b", lower)) >= 2:
        return "smart_list"

    # 2. Check for email structure or email app context
    email_salutations = ["dear ", "hi ", "hello "]
    email_signoffs = ["best regards", "warm regards", "sincerely,", "thanks,\n", "cheers,\n"]
    if app_cat == "email" or any(lower.startswith(s) for s in email_salutations) or any(s in lower for s in email_signoffs):
        if len(text) > 20:
            return "email"

    # 3. Check for coding / terminal / IDE context
    code_keywords = [
        "function ", "class ", "def ", "return ", "import ", "const ", "let ",
        "variable", "config.", "pytest", "github", "pull request", "repository",
        "powershell", "terminal", "command line", "sys.stderr", "git commit",
    ]
    if app_cat in ("ide", "terminal") or any(kw in lower for kw in code_keywords):
        return "coding"

    # 4. Check for meeting notes
    meeting_keywords = [
        "action item", "action items", "key takeaway", "key takeaways",
        "meeting notes", "decided today", "next steps for the team",
    ]
    if any(kw in lower for kw in meeting_keywords):
        return "meeting_notes"

    # 5. Check for social media posts
    social_keywords = ["hashtag", "thread:", "linkedin post", "tweet this"]
    if any(kw in lower for kw in social_keywords):
        return "social"

    # 6. Check for direct editing instructions
    transform_keywords = [
        "rewrite this to", "change this to", "make this shorter",
        "translate this to", "summarize this:",
    ]
    if any(lower.startswith(kw) for kw in transform_keywords):
        return "transform"

    # Default to confident, executive/professional polish
    return "polish"
