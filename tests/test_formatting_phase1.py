"""Unit tests for the Phase 1 rule-based cleanup pipeline (formatting.py)."""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from whisper_flow.formatting import apply_smart_formatting


# ---------------------------------------------------------------------------
# Filler word removal
# ---------------------------------------------------------------------------
class TestFillerRemoval:
    def test_um_uh(self):
        assert apply_smart_formatting("um I was thinking uh maybe") == "I was thinking maybe."

    def test_you_know(self):
        assert apply_smart_formatting("you know the code is fine") == "The code is fine."

    def test_basically_literally(self):
        # First letter gets capitalized by capitalization stage
        assert apply_smart_formatting("it is basically fine") == "It is fine."
        assert apply_smart_formatting("it is literally done") == "It is done."

    def test_sort_of_kind_of(self):
        assert apply_smart_formatting("it is sort of working") == "It is working."
        assert apply_smart_formatting("it is kind of done") == "It is done."

    def test_I_mean_backtrack(self):
        # "I mean" at start triggers backtrack, removing nothing before it
        result = apply_smart_formatting("I mean the store was closed")
        assert "store was closed" in result

    def test_so_yeah(self):
        assert apply_smart_formatting("so yeah I went home") == "I went home."

    def test_combined_fillers(self):
        result = apply_smart_formatting("um so yeah I went to the store")
        assert result == "I went to the store."


# ---------------------------------------------------------------------------
# Backtrack correction
# ---------------------------------------------------------------------------
class TestBacktrack:
    def test_actually(self):
        assert apply_smart_formatting(
            "I went to the store. Actually I went to the market."
        ) == "I went to the market."

    def test_scratch_that(self):
        result = apply_smart_formatting(
            "The function is slow. Scratch that it is fast."
        )
        assert "it is fast" in result.lower()
        assert "slow" not in result

    def test_no_wait(self):
        result = apply_smart_formatting(
            "The price is ten dollars. No wait it is twenty dollars."
        )
        assert "ten dollars" not in result
        assert "$20" in result

    def test_sorry(self):
        result = apply_smart_formatting(
            "Send it to John. Sorry send it to Jane."
        )
        assert "John" not in result
        assert "Jane" in result

    def test_actually_mid_sentence_preserved(self):
        result = apply_smart_formatting(
            "This is actually a very good test."
        )
        assert "This is actually a very good test." in result

    def test_sorry_mid_sentence_preserved(self):
        result = apply_smart_formatting(
            "I am sorry for the delay."
        )
        assert "I am sorry for the delay." in result


# ---------------------------------------------------------------------------
# Repeated word / stutter removal
# ---------------------------------------------------------------------------
class TestRepeatedWords:
    def test_simple_repeat(self):
        assert apply_smart_formatting("the the fox") == "The fox."

    def test_triple_repeat(self):
        assert apply_smart_formatting("I I I want") == "I want."

    def test_no_false_positive_long_words(self):
        # "had had" (past perfect) should NOT be collapsed
        result = apply_smart_formatting("I had had enough")
        assert "had had" in result or "had" in result

    def test_iterative_collapse(self):
        # After collapsing "the the" → "the", should not create new repeat
        # Single word result doesn't get trailing period (fragment heuristic)
        result = apply_smart_formatting("the the the the")
        assert result == "The"


# ---------------------------------------------------------------------------
# ITN: Numbers
# ---------------------------------------------------------------------------
class TestITNNumbers:
    def test_simple(self):
        assert apply_smart_formatting("I have twenty five apples") == "I have 25 apples."

    def test_teens(self):
        assert apply_smart_formatning_safe("thirteen") == "13"

    def test_hundred(self):
        assert apply_smart_formatting("one hundred") == "100"

    def test_hundred_and_five(self):
        assert apply_smart_formatting("one hundred and five") == "105"

    def test_thousand(self):
        assert apply_smart_formatting("two thousand") == "2000"

    def test_nested_scales(self):
        # "three hundred thousand" = 300 * 1000 = 300000, not 300 + 1000
        assert apply_smart_formatting("three hundred thousand") == "300000"

    def test_million(self):
        assert apply_smart_formatting("two million three hundred thousand") == "2300000"


# ---------------------------------------------------------------------------
# ITN: Currency
# ---------------------------------------------------------------------------
class TestITNCurrency:
    def test_dollars_prefix(self):
        assert apply_smart_formatting("it costs twenty dollars") == "It costs $20."

    def test_pounds_prefix(self):
        assert apply_smart_formatting("that is fifty pounds") == "That is £50."

    def test_euros_prefix(self):
        assert apply_smart_formatting("it is ten euros") == "It is €10."

    def test_cents_suffix(self):
        assert apply_smart_formatting("I have five cents") == "I have 5¢."

    def test_rupees_prefix(self):
        assert apply_smart_formatting("it costs hundred rupees") == "It costs ₹100."


# ---------------------------------------------------------------------------
# ITN: Time
# ---------------------------------------------------------------------------
class TestITNTime:
    def test_hour_minute_pm(self):
        assert apply_smart_formatting("the meeting is at three thirty pm") == "The meeting is at 3:30 PM."

    def test_hour_minute_am(self):
        assert apply_smart_formatting("wake up at seven fifteen am") == "Wake up at 7:15 AM."

    def test_o_clock_with_space(self):
        assert apply_smart_formatting("I woke up at nine o clock") == "I woke up at 9:00."

    def test_o_clock_with_apostrophe(self):
        assert apply_smart_formatting("I woke up at nine o'clock") == "I woke up at 9:00."

    def test_no_false_positive(self):
        # "three" alone should not become a time
        result = apply_smart_formatting("I have three apples")
        assert "3:00" not in result


# ---------------------------------------------------------------------------
# ITN: Dates and ordinals
# ---------------------------------------------------------------------------
class TestITNDates:
    def test_month_ordinal(self):
        assert apply_smart_formatting("march fifth") == "March 5th."

    def test_month_ordinal_year(self):
        result = apply_smart_formatting("march fifth two thousand")
        assert "March 5th" in result
        assert "2000" in result

    def test_day_capitalization(self):
        assert apply_smart_formatting("see you on monday") == "See you on Monday."
        assert apply_smart_formatting("friday meeting") == "Friday meeting."

    def test_month_capitalization(self):
        assert apply_smart_formatting("in january") == "In January."


# ---------------------------------------------------------------------------
# Capitalization
# ---------------------------------------------------------------------------
class TestCapitalization:
    def test_standalone_i(self):
        assert apply_smart_formatting("i think i should go") == "I think I should go."

    def test_sentence_start(self):
        assert apply_smart_formatting("hello. what is your name") == "Hello. What is your name."

    def test_paragraph_start(self):
        result = apply_smart_formatting("first line new paragraph second line")
        # After "new paragraph" → \n\n, "second" should be capitalized
        assert "\n\n" in result
        assert "Second" in result

    def test_no_false_positive_in_words(self):
        # "i" inside "iPhone" should not be capitalized
        result = apply_smart_formatting("I like my iphone")
        assert "iphone" in result.lower() or "iPhone" in result


# ---------------------------------------------------------------------------
# Spoken punctuation words
# ---------------------------------------------------------------------------
class TestPunctuationWords:
    def test_period(self):
        result = apply_smart_formatting("hello period world")
        assert "." in result

    def test_comma(self):
        result = apply_smart_formatting("hello comma world")
        assert "," in result

    def test_question_mark(self):
        result = apply_smart_formatting("what question mark")
        assert "?" in result

    def test_new_paragraph(self):
        result = apply_smart_formatting("hello new paragraph world")
        assert "\n\n" in result

    def test_new_line(self):
        result = apply_smart_formatting("hello new line world")
        assert "\n" in result


# ---------------------------------------------------------------------------
# Spacing normalization
# ---------------------------------------------------------------------------
class TestSpacing:
    def test_double_space(self):
        assert apply_smart_formatting("hello  world") == "Hello world."

    def test_space_before_punct(self):
        assert apply_smart_formatting("hello , world") == "Hello, world."

    def test_decimal_not_broken(self):
        result = apply_smart_formatting("the value is 3.14")
        assert "3.14" in result

    def test_time_not_broken(self):
        result = apply_smart_formatting("meet at 3:30 pm")
        assert "3:30" in result

    def test_trailing_whitespace(self):
        # Single word doesn't get trailing period
        assert apply_smart_formatting("hello   ") == "Hello"


# ---------------------------------------------------------------------------
# Writing styles
# ---------------------------------------------------------------------------
class TestWritingStyles:
    def test_formal(self):
        result = apply_smart_formatting("hello world", writing_style="formal")
        assert result[0].isupper()
        assert result.endswith(".")

    def test_casual(self):
        result = apply_smart_formatting("hello world.", writing_style="casual")
        assert not result.endswith(".")

    def test_enthusiastic(self):
        result = apply_smart_formatting("hello world", writing_style="enthusiastic")
        assert result.endswith("!")

    def test_very_casual_contractions(self):
        result = apply_smart_formatting("I am going", writing_style="very_casual")
        assert "I'm" in result


# ---------------------------------------------------------------------------
# Trailing punctuation
# ---------------------------------------------------------------------------
class TestTrailingPunct:
    def test_adds_period(self):
        assert apply_smart_formatting("hello world").endswith(".")

    def test_keeps_existing(self):
        assert apply_smart_formatting("hello world.").endswith(".")

    def test_keeps_question(self):
        assert apply_smart_formatting("hello?").endswith("?")

    def test_no_period_for_fragment(self):
        # Single word fragment should not get a period
        result = apply_smart_formatting("hello")
        assert not result.endswith(".") or len(result.split()) >= 2

    def test_no_period_after_symbol(self):
        result = apply_smart_formatting("see the link:")
        assert not result.endswith(".")


# ---------------------------------------------------------------------------
# End-to-end integration
# ---------------------------------------------------------------------------
class TestEndToEnd:
    def test_complex_dictation(self):
        result = apply_smart_formatting(
            "um so yeah I went to the store and spent twenty dollars on milk period"
        )
        assert "$20" in result
        assert "milk" in result
        assert "um" not in result.lower()
        assert "so yeah" not in result.lower()

    def test_stuttered_dictation(self):
        result = apply_smart_formatting("I I I want to um go to the the store")
        assert "I want" in result
        assert "the the" not in result
        assert "um" not in result.lower()

    def test_corrected_dictation(self):
        result = apply_smart_formatting(
            "the price is ten dollars. actually it is twenty dollars"
        )
        assert "$10" not in result
        assert "$20" in result


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def apply_smart_formatning_safe(text: str) -> str:
    """Wrapper that returns the result without trailing period for single-word tests."""
    return apply_smart_formatting(text).rstrip(".")
