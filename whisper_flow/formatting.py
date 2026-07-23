"""Lightweight Smart Formatting / Rule-based cleanup for dictation text.

Phase 1 cleanup pipeline (no LLM required, ~3ms on CPU):

  1. Spoken punctuation words  →  symbols   ("period" → ".")
  2. Spoken newline words      →  newlines  ("new paragraph" → "\n\n")
  3. Filler-word removal       →  strip     ("um", "uh", "you know", "like")
  4. Backtrack correction      →  delete prior sentence  ("...store. Actually ...market.")
  5. Repeated-word / stutter   →  collapse  ("the the" → "the", "I I I" → "I")
  6. ITN: number words         →  digits    ("twenty five" → "25")
  7. ITN: currency             →  symbols   ("twenty dollars" → "$20")
  8. ITN: time                 →  digits    ("three thirty pm" → "3:30 PM")
  9. ITN: dates / ordinals     →  digits    ("march fifth" → "March 5th")
 10. Capitalization            →  fix       (sentence starts, standalone "i" → "I")
 11. Spacing normalization     →  clean     (double spaces, space before punct)
 12. Writing style             →  apply     (formal / casual / enthusiastic)
 13. Trailing punctuation      →  ensure    (add "." if missing)

This catches ~80-90% of LLM cleanup quality for clear English dictation
at zero network cost. The remaining gap (complex grammar fixes, paraphrasing)
is what Phase 2 (fine-tuned Moonshine) will close.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# 1. Spoken punctuation words
# ---------------------------------------------------------------------------
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
    (r"\bem dash\b", "—"),
    (r"\ben dash\b", "—"),
    (r"\bopen quote\b", '"'),
    (r"\bclose quote\b", '"'),
    (r"\bopen paren\b", "("),
    (r"\bclose paren\b", ")"),
]

# ---------------------------------------------------------------------------
# 2. Spoken newline words
# ---------------------------------------------------------------------------
_NEWLINE_WORDS = [
    (r"\bnew paragraph\b", "\n\n"),
    (r"\bparagraph break\b", "\n\n"),
    (r"\bnew line\b", "\n"),
    (r"\bnext line\b", "\n"),
    (r"\bline break\b", "\n"),
    (r"\btab\b", "\t"),
]

# ---------------------------------------------------------------------------
# 3. Filler words & disfluencies
# ---------------------------------------------------------------------------
_BACKTRACK_MARKERS = (
    "actually",
    "i mean ",  # trailing space to avoid matching "i meant"
    "i mean,",
    "i mean.",
    "sorry",
    "scratch that",
    "rather",
    "no wait",
    "wait no",
    "well actually",
)

_FILLER_PATTERNS = [
    # Standalone vocal hesitations/fillers between words
    (r"\b(um|uh|hmm|uhm|erm|ah|uhh|umm|hmm+|uh+|um+)\b[,\s]*", ""),
    # "like" used as filler before capitalized words
    (r"\blike\s+(?=[A-Z])", ""),
    # "you know" as conversational filler
    (r"\byou know[,\s]*", ""),
    # "I mean" as filler
    (r"\bI mean[,\s]+", ""),
    # "sort of" / "kind of" as filler
    (r"\b(sort of|kind of)[,\s]*", ""),
    # Conversational hedge words
    (r"\b(basically|literally|honestly|obviously|essentially|frankly)\b[,\s]*", ""),
    # Conversational fillers "so yeah" / "yeah so"
    (r"\bso yeah[,\s]*", ""),
    (r"\byeah[,\s]+(?=[A-Z])", ""),
    # Double spaces left behind after removal
    (r"  +", " "),
    # Comma followed by nothing (orphaned)
    (r",\s*(?=[,.;:!?]|$)", ""),
    # Space before punctuation
    (r"\s+([,.;:!?])", r"\1"),
]

# ---------------------------------------------------------------------------
# 5. Repeated-word / stutter detection
# ---------------------------------------------------------------------------
# Match the same word (case-insensitive) repeated 2+ times with only whitespace
# between them. e.g. "the the", "I I I", "um um". Short words only (1-4 chars)
# to avoid false positives like "had had" (past perfect, grammatical).
# {0,6} means: word + (word+space){0 or more} + word = minimum 2 occurrences.
_REPEATED_WORD = re.compile(
    r"\b(\w{1,4})\s+(?:\1\s*){0,6}\1\b",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 6. ITN: number words → digits
# ---------------------------------------------------------------------------
_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TEENS_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}

# Match a run of number-words possibly joined by "and"/hyphens.
# e.g. "twenty five", "one hundred and five", "two million three hundred thousand"
# NOTE: "a" is intentionally NOT included — it causes too many false positives
# ("a note" → "0 note"). The phrase "a hundred" is rare in dictation and the
# false-positive cost outweighs the benefit.
_NUMBER_WORD_RUN = re.compile(
    r"\b(?:" +
    "|".join(list(_ONES) + list(_TEENS_TENS) + list(_SCALES) + ["and"]) +
    r")(?:[-\s]+(?:" +
    "|".join(list(_ONES) + list(_TEENS_TENS) + list(_SCALES) + ["and"]) +
    r"))*\b",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 7. ITN: currency
# ---------------------------------------------------------------------------
_CURRENCY_MAP = {
    "dollars": "$", "dollar": "$",
    "cents": "¢", "cent": "¢",
    "pounds": "£", "pound": "£",
    "euros": "€", "euro": "€",
    "rupees": "₹", "rupee": "₹",
    "yen": "¥",
}

# ---------------------------------------------------------------------------
# 8. ITN: time
# ---------------------------------------------------------------------------
# "three thirty pm" → "3:30 PM"
# "nine o'clock" → "9:00"
# "quarter past three" → "3:15" (best-effort, skip if ambiguous)
_TIME_HOUR_MAP = {
    "twelve": 12, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11,
}

_TIME_PATTERN = re.compile(
    r"\b("
    r"(?:twelve|one|two|three|four|five|six|seven|eight|nine|ten|eleven)"
    r"(?:\s+(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|twenty five|thirty|forty|forty five|fifty))"
    r"|"
    r"(?:twelve|one|two|three|four|five|six|seven|eight|nine|ten|eleven)\s+o'?clock"
    r")\s*"
    r"(a\.?m\.?|p\.?m\.?)?\b",
    flags=re.IGNORECASE,
)

# Also handle compound minutes that the main regex might miss
# e.g. "four forty five" → the main regex matches "four forty", leaving "five"
# This post-fix handles the leftover by merging "4:40five" or "4:40 five" → "4:45"
_TIME_COMPOUND_FIX = re.compile(
    r"(\d+):(\d+)(?:\s*)(five|ten)\b",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 9. ITN: dates / ordinals
# ---------------------------------------------------------------------------
_MONTHS = {
    "january": "January", "february": "February", "march": "March",
    "april": "April", "may": "May", "june": "June", "july": "July",
    "august": "August", "september": "September", "october": "October",
    "november": "November", "december": "December",
}

_ORDINAL_WORDS = {
    "first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th",
    "fifth": "5th", "sixth": "6th", "seventh": "7th", "eighth": "8th",
    "ninth": "9th", "tenth": "10th", "eleventh": "11th", "twelfth": "12th",
    "thirteenth": "13th", "fourteenth": "14th", "fifteenth": "15th",
    "sixteenth": "16th", "seventeenth": "17th", "eighteenth": "18th",
    "nineteenth": "19th", "twentieth": "20th", "twenty first": "21st",
    "twenty second": "22nd", "twenty third": "23rd", "twenty fourth": "24th",
    "twenty fifth": "25th", "twenty sixth": "26th", "twenty seventh": "27th",
    "twenty eighth": "28th", "twenty ninth": "29th", "thirtieth": "30th",
    "thirty first": "31st",
}

# ---------------------------------------------------------------------------
# 10. Capitalization
# ---------------------------------------------------------------------------
# Standalone "i" pronoun → "I" (case-insensitive match, not part of a word)
_STANDALONE_I = re.compile(r"(?<![a-zA-Z])i(?![a-zA-Z])")

# Days of week to capitalize
_DAYS = {
    "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday",
    "thursday": "Thursday", "friday": "Friday", "saturday": "Saturday",
    "sunday": "Sunday",
}


# ===========================================================================
# Public API
# ===========================================================================


def apply_smart_formatting(text: str, *, writing_style: str = "default") -> str:
    """Apply the full Phase 1 rule-based cleanup pipeline to dictation text.

    Order matters: remove fillers/backtrack first (so ITN doesn't accidentally
    consume filler words as numbers), then normalize numbers/dates, then fix
    capitalization, then spacing, then writing style.
    """
    out = str(text or "").strip()
    if not out:
        return ""

    press_enter = bool(re.search(r"\bpress enter\b\s*$", out, flags=re.IGNORECASE))

    # Stage 1-2: spoken control words
    out = _apply_newlines(out)
    out = _apply_punctuation_words(out)

    # Stage 3-4: fillers and backtrack correction
    out = _apply_fillers(out)
    out = _apply_backtrack(out)

    # Stage 5: repeated-word / stutter removal
    out = _remove_repeated_words(out)

    # Stage 6-9: ITN (time first, then dates/ordinals, then currency, then numbers)
    # Order matters: time/dates/currency patterns are more specific than plain
    # number runs, so they must run first to avoid being consumed by the
    # generic number normalizer (e.g. "three thirty pm" -> "3:30 PM", not "33 pm").
    out = _apply_itn_time(out)
    out = _apply_itn_dates_ordinals(out)
    out = _apply_itn_currency(out)
    out = _apply_itn_numbers(out)

    # Stage 10: capitalization
    out = _apply_capitalization(out)

    # Stage 11: spacing normalization
    out = _apply_press_enter(out)
    out = _normalize_spacing(out)

    # Stage 12: trailing punctuation (before writing style so casual can strip it)
    out = _ensure_trailing_punct(out)

    # Stage 13: writing style (may add/remove trailing punctuation)
    out = _apply_writing_style(out, writing_style)

    out = out.rstrip(" \t")
    # Strip leading punctuation/whitespace left behind by filler removal
    # e.g. "Um. I went" → fillers remove "Um" → ". I went" → "I went"
    out = out.lstrip(" .,;:!?\t\n")
    # Collapse duplicate trailing punctuation (possibly space-separated):
    # ". ." → ".", "? ." → "?", ". ." → "."
    while True:
        stripped = out.rstrip()
        if len(stripped) >= 2 and stripped[-1] in ".!?" and stripped[-2] in ".!?":
            out = stripped[:-1]
        elif len(stripped) >= 3 and stripped[-1] in ".!?" and stripped[-2] == " " and stripped[-3] in ".!?":
            out = stripped[:-2] + stripped[-1]
        else:
            break
    if press_enter and not out.endswith("\n"):
        out += "\n"
    return out


# ===========================================================================
# Stage implementations
# ===========================================================================


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


def _apply_fillers(text: str) -> str:
    out = text
    for pattern, repl in _FILLER_PATTERNS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE | re.MULTILINE)
    return out


def _apply_backtrack(text: str) -> str:
    # Backtrack correction: "I went to the store. Actually I went to the market."
    # should become "I went to the market."
    #
    # Only removes ONE prior sentence per backtrack marker — not all prior
    # sentences. This prevents over-aggressive deletion when there are
    # multiple backtrack markers in sequence.
    sentences = re.split(r"([.!?]\s+)", text)
    rebuilt: list[str] = []
    for part in sentences:
        lowered = part.lower()
        marker = next((m for m in _BACKTRACK_MARKERS if m in lowered), "")
        if marker:
            idx = lowered.find(marker)
            suffix = part[idx + len(marker):].strip(" ,")
            # Remove ONLY the immediately preceding sentence + its separator.
            # Pop the separator first, then the sentence.
            if rebuilt:
                while rebuilt and not rebuilt[-1].strip():
                    rebuilt.pop()
                if rebuilt:
                    rebuilt.pop()  # separator
            if rebuilt:
                while rebuilt and not rebuilt[-1].strip():
                    rebuilt.pop()
                if rebuilt:
                    rebuilt.pop()  # sentence
            if len(suffix.split()) >= 1:
                rebuilt.append(suffix)
            continue
        rebuilt.append(part)
    return "".join(rebuilt)


def _remove_repeated_words(text: str) -> str:
    """Collapse stuttered/repeated short words: 'the the' → 'the', 'I I I' → 'I'.

    Iterates until stable because regex replacement can create new adjacencies.
    Only short words (1-4 chars) are matched to avoid breaking valid constructs
    like "had had" (past perfect) or "that that" (relative clause).
    """
    prev = None
    out = text
    # Run up to 5 passes; stop early when stable.
    for _ in range(5):
        prev = out
        out = _REPEATED_WORD.sub(lambda m: m.group(1), out)
        if out == prev:
            break
    return out


def _words_to_number(phrase: str) -> int | None:
    """Convert a phrase like 'two hundred and five' to 205. None if unparseable.

    Handles nested scales correctly: 'three hundred thousand' = 300*1000 = 300000,
    not 300+1000 = 1300. The algorithm uses a running `current` subtotal that
    accumulates ones/tens and multiplies by 'hundred', then flushes to `total`
    when a scale >= 1000 (thousand/million/billion) is encountered.
    """
    tokens = re.sub(r"[\s-]+", " ", phrase.lower()).split(" ")
    tokens = [t for t in tokens if t and t != "and"]
    if not tokens:
        return None
    total = 0
    current = 0
    for tok in tokens:
        if tok in _ONES:
            current += _ONES[tok]
        elif tok in _TEENS_TENS:
            current += _TEENS_TENS[tok]
        elif tok == "a":  # "a hundred" = 100
            continue
        elif tok == "hundred":
            # Multiply current subtotal by 100, but keep in current (don't flush).
            # e.g. "three hundred" → current = 3 * 100 = 300
            if current == 0:
                current = 1
            current *= 100
        elif tok in ("thousand", "million", "billion"):
            # Flush current * scale to total, reset current.
            # e.g. "three hundred thousand" → total += 300 * 1000 = 300000
            scale = _SCALES[tok]
            if current == 0:
                current = 1
            total += current * scale
            current = 0
        else:
            return None
    return total + current


def _apply_itn_numbers(text: str) -> str:
    """Replace number-word runs with digits: 'twenty five' → '25'.

    Conservative: only replaces runs that include at least one true number word,
    avoiding false positives on words like 'and'. Skips runs that are part of a
    larger hyphenated compound (e.g. 'twenty-five-year-old' left alone).
    """
    def repl(m: re.Match) -> str:
        phrase = m.group(0)
        # Don't touch if it's already preceded/followed by a digit (e.g. in a date)
        # The regex itself won't match digits, so this is mostly defensive.
        val = _words_to_number(phrase)
        if val is None:
            return phrase
        # Skip very large implausible values (safety cap)
        if abs(val) > 999_999_999:
            return phrase
        return str(val)

    return _NUMBER_WORD_RUN.sub(repl, text)


# Currencies where the symbol goes BEFORE the number: $20, £50, €10
_CURRENCY_PREFIX = {"dollars": "$", "dollar": "$", "pounds": "£", "pound": "£",
                     "euros": "€", "euro": "€", "rupees": "₹", "rupee": "₹", "yen": "¥"}
# Currencies where the symbol goes AFTER the number: 5¢, 10p
_CURRENCY_SUFFIX = {"cents": "¢", "cent": "¢", "pence": "p", "penny": "p"}


def _apply_itn_currency(text: str) -> str:
    """Replace 'twenty dollars' → '$20', 'five cents' → '5¢'.

    Must run BEFORE _apply_itn_numbers so the number words adjacent to the
    currency word are consumed together. Prefix currencies ($, £, €) put the
    symbol before the number; suffix currencies (¢, p) put it after.
    """
    num_alt = "|".join(list(_ONES) + list(_TEENS_TENS) + list(_SCALES) + ["and"])
    # NOTE: "a" intentionally excluded (see _NUMBER_WORD_RUN comment above)
    num_run = r"(?:" + num_alt + r")(?:[-\s]+(?:" + num_alt + r"))*"

    out = text
    # Prefix currencies: "twenty dollars" → "$20"
    for word, sym in _CURRENCY_PREFIX.items():
        pat = re.compile(r"\b(" + num_run + r")\s+" + word + r"\b", flags=re.IGNORECASE)

        def repl_pre(m: re.Match, _sym: str = sym) -> str:
            val = _words_to_number(m.group(1))
            if val is None:
                return m.group(0)
            return f"{_sym}{val}"

        out = pat.sub(repl_pre, out)

    # Suffix currencies: "five cents" → "5¢"
    for word, sym in _CURRENCY_SUFFIX.items():
        pat = re.compile(r"\b(" + num_run + r")\s+" + word + r"\b", flags=re.IGNORECASE)

        def repl_suf(m: re.Match, _sym: str = sym) -> str:
            val = _words_to_number(m.group(1))
            if val is None:
                return m.group(0)
            return f"{val}{_sym}"

        out = pat.sub(repl_suf, out)
    return out


def _apply_itn_time(text: str) -> str:
    """Replace 'three thirty pm' → '3:30 PM', 'nine o'clock' → '9:00'."""
    def repl(m: re.Match) -> str:
        full = m.group(0).strip()
        ampm = m.group(2)
        # Try to parse hour + optional minute
        tokens = re.sub(r"o'?clock", "", full, flags=re.IGNORECASE).strip().split()
        # Drop trailing am/pm token if captured separately
        if ampm:
            tokens = [t for t in tokens if t.lower().replace(".", "") not in ("am", "pm")]
        if not tokens:
            return full
        hour_str = tokens[0].lower()
        hour = _TIME_HOUR_MAP.get(hour_str)
        if hour is None:
            return full
        minute = 0
        if len(tokens) >= 2:
            minute_str = tokens[1].lower()
            minute_map = {
                "oh": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
                "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
                "twenty five": 25, "thirty": 30, "forty": 40, "forty five": 45,
                "fifty": 50,
            }
            # Try "twenty five" as a two-token minute
            if len(tokens) >= 3 and (tokens[1] + " " + tokens[2]).lower() in minute_map:
                minute = minute_map[(tokens[1] + " " + tokens[2]).lower()]
            else:
                minute = minute_map.get(minute_str, 0)
        result = f"{hour}:{minute:02d}"
        if ampm:
            result += " " + ampm.upper().replace(".", "")
        return result

    # Fix common mis-transcriptions of "o'clock": "o clock" → "o'clock"
    # Done before the main regex so "nine o clock" matches the o'clock branch.
    out = re.sub(r"\bo['\s]?clock\b", "o'clock", text, flags=re.IGNORECASE)
    # Run the time regex first (converts "three thirty" → "3:30")
    out = _TIME_PATTERN.sub(repl, out)

    # Fix compound minutes: "4:40 five" → "4:45", "3:20 ten" → "3:30"
    def _compound_fix(m: re.Match) -> str:
        hour = int(m.group(1))
        minute = int(m.group(2))
        leftover = m.group(3).lower()
        add = {"five": 5, "ten": 10}.get(leftover, 0)
        return f"{hour}:{minute + add:02d}"

    out = _TIME_COMPOUND_FIX.sub(_compound_fix, out)

    # Fix "p M" / "a M" (Moonshine sometimes outputs AM/PM with a space).
    # Only match when preceded by a digit (time context) to avoid matching
    # "am" in "I am going" or "pm" in standalone text.
    # Run AFTER the time regex so "3:30 p M" has a digit before "p".
    out = re.sub(r"(?<=\d)\s*p\.?\s*m\.?\b", " PM", out, flags=re.IGNORECASE)
    out = re.sub(r"(?<=\d)\s*a\.?\s*m\.?\b", " AM", out, flags=re.IGNORECASE)
    return out


def _apply_itn_dates_ordinals(text: str) -> str:
    """Replace 'march fifth' → 'March 5th', 'first' → '1st' (standalone)."""
    out = text

    # Month + ordinal: "march fifth" → "March 5th", "january twenty third" → "January 23rd"
    for month_word, month_name in _MONTHS.items():
        pattern = re.compile(
            r"\b" + month_word + r"\s+(" + "|".join(_ORDINAL_WORDS.keys()) + r")\b",
            flags=re.IGNORECASE,
        )

        def make_repl(mn: str):
            def repl(m: re.Match) -> str:
                ordinal = _ORDINAL_WORDS.get(m.group(1).lower())
                if ordinal:
                    return f"{mn} {ordinal}"
                return m.group(0)
            return repl

        out = pattern.sub(make_repl(month_name), out)

    # Standalone ordinals (not after a month): "the first of may" → "the 1st of May"
    # Be conservative: only replace if preceded by "the" or followed by "of"
    for word, ordinal in _ORDINAL_WORDS.items():
        # "the first" → "the 1st"
        out = re.sub(
            r"\bthe\s+" + word + r"\b",
            lambda m, o=ordinal: "the " + o,
            out,
            flags=re.IGNORECASE,
        )

    # Capitalize standalone month names
    for month_word, month_name in _MONTHS.items():
        out = re.sub(r"\b" + month_word + r"\b", month_name, out, flags=re.IGNORECASE)

    # Capitalize days of week
    for day_word, day_name in _DAYS.items():
        out = re.sub(r"\b" + day_word + r"\b", day_name, out, flags=re.IGNORECASE)

    return out


def _apply_capitalization(text: str) -> str:
    """Capitalize sentence starts, standalone 'i' → 'I', proper nouns."""
    out = text

    # Standalone "i" → "I" (pronoun). Use lookahead/lookbehind to avoid
    # touching "i" inside other words or in acronyms like "iOS".
    out = _STANDALONE_I.sub("I", out)

    # Capitalize first letter of the whole text
    if out and out[0].isalpha() and out[0].islower():
        out = out[0].upper() + out[1:]

    # Capitalize first letter after sentence-ending punctuation + whitespace
    # e.g. ". hello" → ". Hello", "! what" → "! What"
    out = re.sub(
        r"([.!?]\s+)([a-z])",
        lambda m: m.group(1) + m.group(2).upper(),
        out,
    )

    # Capitalize first letter after newline (paragraph start)
    out = re.sub(
        r"(\n\s*)([a-z])",
        lambda m: m.group(1) + m.group(2).upper(),
        out,
    )

    return out


def _apply_press_enter(text: str) -> str:
    # H12 FIX: remove ALL occurrences of "press enter", not just at end
    out = re.sub(r"\bpress enter\b", "", text, flags=re.IGNORECASE)
    out = out.rstrip()
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
    # H11 FIX: undo the auto-space for decimals (1.5, 3.14) and common abbreviations
    # (e.g., i.e., Mr., Dr., etc.) and time formats (3:30, 12:45).
    out = re.sub(r"(\d)\. (\d)", r"\1.\2", out)  # fix decimals: 1. 5 → 1.5
    out = re.sub(r"\b([ei])\. ([ge])\.", r"\1.\2.", out)  # e.g. → e.g., i.e. → i.e.
    out = re.sub(r"(\d): (\d)", r"\1:\2", out)  # fix time: 3: 30 → 3:30
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


def _ensure_trailing_punct(text: str) -> str:
    """Ensure the text ends with sentence-ending punctuation (., !, or ?).

    Skipped if the text ends with a newline, already has punctuation, or
    looks like a fragment (very short or no verb — best-effort heuristic).
    """
    out = text.rstrip()
    if not out:
        return text
    # Don't add if already punctuated
    if out[-1] in ".!?":
        return text
    # Don't add if ends with newline (multi-paragraph)
    if out[-1] == "\n":
        return text
    # Don't add to very short fragments (likely incomplete)
    if len(out.split()) < 2:
        return text
    # Don't add if ends with a symbol (URL, code, etc.)
    if out[-1] in ":,;)\"'":
        return text
    return out + "."
