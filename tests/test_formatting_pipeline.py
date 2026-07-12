#!/usr/bin/env python3
"""WhisperFlow formatting pipeline test — no audio needed.

Tests the rule-based formatting pipeline (formatting.py) with known inputs
that simulate common ASR outputs. This runs instantly without needing TTS
or ASR — it tests the cleanup/polish logic directly.

Test categories:
  1. Filler word removal (um, uh, like, you know, basically)
  2. Backtrack correction (actually, I mean, scratch that)
  3. Stutter/repeated word removal
  4. ITN: number normalization (twenty five → 25)
  5. ITN: currency (twenty dollars → $20)
  6. ITN: time (three thirty pm → 3:30 PM)
  7. ITN: dates (march fifth → March 5th)
  8. Capitalization (sentence starts, standalone 'i')
  9. Spoken punctuation words (period → .)
 10. Writing styles (formal, casual, enthusiastic)
 11. Edge cases (empty, very short, trailing whitespace)
 12. Combined complex (multiple features at once)

Usage:
    python tests/test_formatting_pipeline.py
    python tests/test_formatting_pipeline.py --verbose
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from whisper_flow.formatting import apply_smart_formatting


# ---------------------------------------------------------------------------
# Test cases — simulate real ASR output
# ---------------------------------------------------------------------------

TESTS = [
    # --- Filler word removal ---
    {
        "name": "filler_um_uh",
        "input": "um I was thinking uh maybe we should go",
        "expected_contains": ["thinking", "maybe", "go"],
        "expected_not_contains": ["um", "uh"],
    },
    {
        "name": "filler_you_know",
        "input": "you know the code is basically fine",
        "expected_contains": ["code", "fine"],
        "expected_not_contains": ["you know", "basically"],
    },
    {
        "name": "filler_like",
        "input": "like I was thinking maybe we should go",
        "expected_contains": ["thinking"],
        "expected_not_contains": ["um", "uh"],
    },
    {
        "name": "filler_I_mean",
        "input": "I mean the store was closed so I went home",
        "expected_contains": ["store", "closed", "went home"],
    },

    # --- Backtrack correction ---
    {
        "name": "backtrack_actually",
        "input": "I went to the store. Actually I went to the market.",
        "expected_contains": ["market"],
        "expected_not_contains": ["store"],
    },
    {
        "name": "backtrack_scratch_that",
        "input": "The function is slow. Scratch that it is fast.",
        "expected_contains": ["fast"],
        "expected_not_contains": ["slow"],
    },
    {
        "name": "backtrack_sorry",
        "input": "Send it to John. Sorry send it to Jane.",
        "expected_contains": ["Jane"],
        "expected_not_contains": ["John"],
    },

    # --- Stutter/repeated word removal ---
    {
        "name": "stutter_simple",
        "input": "I I I want to go",
        "expected_contains": ["I want"],
    },
    {
        "name": "stutter_the",
        "input": "the the quick brown fox",
        "expected_contains": ["the quick"],
    },

    # --- ITN: Numbers ---
    {
        "name": "itn_number_simple",
        "input": "I have twenty five apples",
        "expected_contains": ["25"],
    },
    {
        "name": "itn_number_hundred",
        "input": "one hundred and five",
        "expected_contains": ["105"],
    },
    {
        "name": "itn_number_nested",
        "input": "two million three hundred thousand",
        "expected_contains": ["2300000"],
    },

    # --- ITN: Currency ---
    {
        "name": "itn_currency_dollars",
        "input": "it costs twenty dollars",
        "expected_contains": ["$20"],
    },
    {
        "name": "itn_currency_pounds",
        "input": "that is fifty pounds",
        "expected_contains": ["£50"],
    },
    {
        "name": "itn_currency_cents",
        "input": "I have five cents",
        "expected_contains": ["5¢"],
    },

    # --- ITN: Time ---
    {
        "name": "itn_time_pm",
        "input": "the meeting is at three thirty pm",
        "expected_contains": ["3:30 PM"],
    },
    {
        "name": "itn_time_oclock",
        "input": "I woke up at nine o clock",
        "expected_contains": ["9:00"],
    },

    # --- ITN: Dates ---
    {
        "name": "itn_date_month",
        "input": "march fifth",
        "expected_contains": ["March 5th"],
    },
    {
        "name": "itn_date_day",
        "input": "see you on monday",
        "expected_contains": ["Monday"],
    },

    # --- Capitalization ---
    {
        "name": "cap_standalone_i",
        "input": "i think i should go now",
        "expected_contains": ["I think I should go"],
    },
    {
        "name": "cap_sentence_start",
        "input": "hello. what is your name",
        "expected_contains": ["Hello.", "What"],
    },

    # --- Spoken punctuation ---
    {
        "name": "punct_period",
        "input": "hello period world",
        "expected_contains": ["hello."],
    },
    {
        "name": "punct_newline",
        "input": "hello new paragraph world",
        "expected_contains": ["\n\n"],
    },

    # --- Writing styles ---
    {
        "name": "style_formal",
        "input": "hello world",
        "style": "formal",
        "expected_contains": ["Hello", "."],
    },
    {
        "name": "style_casual",
        "input": "hello world.",
        "style": "casual",
        "expected_not_contains": ["."],  # casual drops trailing period
    },
    {
        "name": "style_enthusiastic",
        "input": "hello world",
        "style": "enthusiastic",
        "expected_contains": ["!"],
    },

    # --- Edge cases ---
    {
        "name": "edge_empty",
        "input": "",
        "expected_contains": [],
    },
    {
        "name": "edge_whitespace",
        "input": "   hello   ",
        "expected_contains": ["Hello"],
    },
    {
        "name": "edge_trailing_period",
        "input": "hello world",
        "expected_contains": ["."],  # should add trailing period
    },

    # --- Combined complex ---
    {
        "name": "complex_fillers_numbers",
        "input": "um I spent twenty dollars on milk period",
        "expected_contains": ["$20", "milk"],
        "expected_not_contains": ["um"],
    },
    {
        "name": "complex_stutter_backtrack",
        "input": "I I I went to the store. Actually I went to the market.",
        "expected_contains": ["market"],
        "expected_not_contains": ["store"],
    },
    {
        "name": "complex_proper_noun_fillers",
        "input": "um so yeah I went to the store and spent twenty dollars on milk period",
        "expected_contains": ["$20", "milk"],
        "expected_not_contains": ["um", "so yeah"],
    },
]


def run_tests(verbose: bool = False):
    """Run all formatting tests."""
    passed = 0
    failed = 0
    errors = []

    print(f"{'Test':<35} {'Status':>8}")
    print("-" * 45)

    for tc in TESTS:
        name = tc["name"]
        input_text = tc["input"]
        style = tc.get("style", "default")
        expected_contains = tc.get("expected_contains", [])
        expected_not_contains = tc.get("expected_not_contains", [])

        try:
            result = apply_smart_formatting(input_text, writing_style=style)
        except Exception as e:
            failed += 1
            errors.append(f"{name}: EXCEPTION: {e}")
            print(f"{name:<35} {'ERROR':>8}")
            continue

        # Check expected contains
        result_lower = result.lower()
        check_pass = True
        for expected in expected_contains:
            if expected.lower() not in result_lower:
                check_pass = False
                errors.append(f"{name}: expected '{expected}' in result, got: {result!r}")
                break

        # Check expected not contains
        if check_pass:
            for not_expected in expected_not_contains:
                if not_expected.lower() in result_lower:
                    check_pass = False
                    errors.append(f"{name}: '{not_expected}' should NOT be in result: {result!r}")
                    break

        if check_pass:
            passed += 1
            print(f"{name:<35} {'PASS':>8}")
        else:
            failed += 1
            print(f"{name:<35} {'FAIL':>8}")

        if verbose:
            print(f"  Input:    {input_text!r}")
            print(f"  Output:   {result!r}")
            print()

    # Summary
    print("\n" + "=" * 45)
    print(f"Passed: {passed}/{passed + failed}")
    if errors:
        print(f"\nFailures:")
        for e in errors:
            print(f"  - {e}")
    return failed == 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    success = run_tests(verbose=args.verbose)
    sys.exit(0 if success else 1)
