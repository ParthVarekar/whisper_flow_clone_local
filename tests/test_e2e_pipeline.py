#!/usr/bin/env python3
"""WhisperFlow end-to-end test suite.

Generates test audio at varying lengths using z-ai TTS, then runs each
through the full pipeline:
  1. ASR (z-ai cloud ASR — simulates Qwen3-ASR on user's device)
  2. Rule-based formatting (our formatting.py)
  3. LLM polishing (z-ai cloud LLM — simulates gemma-4 on user's device)

Reports:
  - Word Error Rate (WER) for ASR accuracy
  - Formatting improvement (how much formatting.py cleans up)
  - LLM polishing improvement (how much LLM improves over formatted)
  - Timing for each stage
  - List formatting detection
  - Backtrack correction
  - Filler word removal
  - Number normalization

Usage:
    python tests/test_e2e_pipeline.py
    python tests/test_e2e_pipeline.py --verbose
    python tests/test_e2e_pipeline.py --skip-tts  # skip audio generation, use existing

Requires: z-ai CLI in PATH (for TTS + ASR + LLM)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tempfile
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from whisper_flow.formatting import apply_smart_formatting


# ---------------------------------------------------------------------------
# Test cases — varying lengths and feature coverage
# ---------------------------------------------------------------------------

TEST_CASES = [
    # --- Short recordings (3-8 seconds) ---
    {
        "name": "short_simple",
        "text": "This is a simple test of the voice dictation system.",
        "expected_duration": "~4s",
        "features": ["basic"],
    },
    {
        "name": "short_fillers",
        "text": "Um, I was thinking, uh, maybe we should, like, go to the store.",
        "expected_features": ["filler_removal"],
        "expected_formatted_contains": "thinking maybe we should go to the store",
    },
    {
        "name": "short_backtrack",
        "text": "Let's meet at 2pm. Actually, let's meet at 3pm instead.",
        "expected_features": ["backtrack"],
        "expected_formatted_contains": "3pm",
    },
    {
        "name": "short_numbers",
        "text": "I have twenty five apples and one hundred dollars.",
        "expected_features": ["number_normalization"],
        "expected_formatted_contains": "25",
    },

    # --- Medium recordings (8-15 seconds) ---
    {
        "name": "medium_proper_nouns",
        "text": "I am testing WhisperFlow with Qwen3-ASR and Moonshine models for voice dictation.",
        "expected_features": ["proper_nouns"],
        "expected_formatted_contains": "WhisperFlow",
    },
    {
        "name": "medium_currency",
        "text": "The laptop costs twelve hundred dollars and the phone costs fifty pounds.",
        "expected_features": ["currency"],
        "expected_formatted_contains": "$",
    },
    {
        "name": "medium_time",
        "text": "The meeting is at three thirty PM and ends at four forty five PM.",
        "expected_features": ["time_normalization"],
        "expected_formatted_contains": "3:30",
    },
    {
        "name": "medium_list",
        "text": "I need to buy several items: apples, bananas, oranges, milk, and bread.",
        "expected_features": ["list_detection"],
    },

    # --- Long recordings (15-30 seconds) ---
    {
        "name": "long_paragraph",
        "text": (
            "This is a longer test to check how the system handles extended dictation. "
            "I am going to speak for about twenty seconds to see if the transcription "
            "remains accurate throughout. The system should remove filler words, fix "
            "grammar, and produce clean formatted text. Let's see how it performs."
        ),
        "expected_duration": "~20s",
        "features": ["long_form"],
    },
    {
        "name": "long_with_corrections",
        "text": (
            "First, I want to say that the project deadline is Friday. "
            "Actually, no, it's Monday. Sorry, I meant Tuesday. "
            "The team should be ready by then."
        ),
        "expected_features": ["backtrack", "long_form"],
        "expected_formatted_contains": "Tuesday",
    },
    {
        "name": "long_technical",
        "text": (
            "We need to update the whisper_flow daemon to use the qwen3_asr backend "
            "instead of moonshine. The config file llama4.toml has the wrong settings. "
            "Also, fix the crispasr command to include the dash prompt flag for "
            "proper noun detection."
        ),
        "expected_features": ["technical_terms", "long_form"],
    },

    # --- Edge cases ---
    {
        "name": "edge_very_short",
        "text": "Hello world.",
        "expected_features": ["minimal"],
    },
    {
        "name": "edge_stutter",
        "text": "I I I want to to to go to the the store.",
        "expected_features": ["stutter_removal"],
        "expected_formatted_contains": "I want to go to the store",
    },
    {
        "name": "edge_punctuation_words",
        "text": "Hello comma world period New paragraph This is a test period",
        "expected_features": ["punctuation_words"],
    },
    {
        "name": "edge_mixed_numbers",
        "text": "I have two cats, three dogs, and twelve fish. That is seventeen animals total.",
        "expected_features": ["number_normalization"],
    },
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def generate_tts(text: str, output_path: str) -> bool:
    """Generate audio using z-ai TTS CLI."""
    try:
        result = subprocess.run(
            ["z-ai", "tts", "-i", text, "-o", output_path],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception:
        return False


def _extract_json_from_output(output: str) -> dict | None:
    """Extract JSON object from z-ai CLI output (which has emoji status lines)."""
    import re
    # Find the first { ... } block in the output
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', output, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def transcribe_audio(audio_path: str) -> tuple[str, float]:
    """Transcribe using z-ai ASR CLI. Returns (text, time_ms)."""
    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            ["z-ai", "asr", "-f", audio_path],
            capture_output=True, text=True, timeout=60
        )
        elapsed = (time.perf_counter() - t0) * 1000
        if result.returncode == 0:
            data = _extract_json_from_output(result.stdout)
            if data and "text" in data:
                return data["text"].strip(), elapsed
            return "", elapsed
        return "", elapsed
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        return f"ERROR: {exc}", elapsed


def llm_polish(text: str) -> tuple[str, float]:
    """Polish using z-ai LLM (simulates gemma-4). Returns (text, time_ms)."""
    from whisper_flow.prompts import SYSTEM_PROMPTS
    system_prompt = SYSTEM_PROMPTS["medium"]

    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            ["z-ai", "chat", "-s", system_prompt, "-p", text],
            capture_output=True, text=True, timeout=60
        )
        elapsed = (time.perf_counter() - t0) * 1000
        if result.returncode == 0:
            data = _extract_json_from_output(result.stdout)
            if data and "choices" in data:
                content = data["choices"][0]["message"]["content"]
                return content.strip(), elapsed
            return text, elapsed
        return text, elapsed
    except Exception:
        elapsed = (time.perf_counter() - t0) * 1000
        return text, elapsed


def calculate_wer(reference: str, hypothesis: str) -> float:
    """Calculate Word Error Rate (lower is better, 0.0 = perfect)."""
    ref_words = reference.lower().split()
    hyp_words = hypothesis.lower().split()

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    # Levenshtein distance on words
    dp = [[0] * (len(hyp_words) + 1) for _ in range(len(ref_words) + 1)]
    for i in range(len(ref_words) + 1):
        dp[i][0] = i
    for j in range(len(hyp_words) + 1):
        dp[0][j] = j

    for i in range(1, len(ref_words) + 1):
        for j in range(1, len(hyp_words) + 1):
            if ref_words[i-1] == hyp_words[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1])

    return dp[len(ref_words)][len(hyp_words)] / len(ref_words)


def calculate_cer(reference: str, hypothesis: str) -> float:
    """Calculate Character Error Rate (lower is better, 0.0 = perfect)."""
    ref_chars = list(reference.lower())
    hyp_chars = list(hypothesis.lower())

    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0

    dp = [[0] * (len(hyp_chars) + 1) for _ in range(len(ref_chars) + 1)]
    for i in range(len(ref_chars) + 1):
        dp[i][0] = i
    for j in range(len(hyp_chars) + 1):
        dp[0][j] = j

    for i in range(1, len(ref_chars) + 1):
        for j in range(1, len(hyp_chars) + 1):
            if ref_chars[i-1] == hyp_chars[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1])

    return dp[len(ref_chars)][len(hyp_chars)] / len(ref_chars)


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run_tests(verbose: bool = False, skip_tts: bool = False):
    """Run all test cases and report results."""
    audio_dir = os.path.join(tempfile.gettempdir(), "wf_test_audio")
    os.makedirs(audio_dir, exist_ok=True)

    results = []
    total_start = time.perf_counter()

    print(f"{'Test':<25} {'ASR WER':>8} {'Fmt WER':>8} {'LLM WER':>8} {'ASR ms':>8} {'Fmt ms':>8} {'LLM ms':>8} {'Status':>8}")
    print("-" * 95)

    for tc in TEST_CASES:
        name = tc["name"]
        expected_text = tc["text"]
        audio_path = os.path.join(audio_dir, f"{name}.wav")

        # Step 0: Generate audio (or skip)
        # TTS has a 1024 char limit — truncate if needed
        tts_text = expected_text[:1000] if len(expected_text) > 1000 else expected_text
        if not skip_tts:
            if not os.path.exists(audio_path):
                if verbose:
                    print(f"  Generating TTS for {name}...")
                if not generate_tts(tts_text, audio_path):
                    print(f"  [SKIP] TTS failed for {name}")
                    continue
        elif not os.path.exists(audio_path):
            print(f"  [SKIP] No audio for {name} (use --skip-tts only after first run)")
            continue

        # Step 1: ASR (z-ai cloud, simulates Qwen3-ASR)
        asr_text, asr_ms = transcribe_audio(audio_path)

        # Step 2: Rule-based formatting (our formatting.py)
        t_fmt_start = time.perf_counter()
        formatted_text = apply_smart_formatting(asr_text, writing_style="default")
        fmt_ms = (time.perf_counter() - t_fmt_start) * 1000

        # Step 3: LLM polishing (z-ai cloud, simulates gemma-4)
        llm_text, llm_ms = llm_polish(formatted_text)

        # Calculate accuracy
        asr_wer = calculate_wer(expected_text, asr_text)
        fmt_wer = calculate_wer(expected_text, formatted_text)
        llm_wer = calculate_wer(expected_text, llm_text)

        # Check expected features
        feature_pass = True
        if "expected_formatted_contains" in tc:
            if tc["expected_formatted_contains"].lower() not in formatted_text.lower():
                feature_pass = False

        status = "PASS" if asr_wer < 0.5 and feature_pass else "CHECK"

        results.append({
            "name": name,
            "expected": expected_text,
            "asr": asr_text,
            "formatted": formatted_text,
            "llm_polished": llm_text,
            "asr_wer": asr_wer,
            "fmt_wer": fmt_wer,
            "llm_wer": llm_wer,
            "asr_ms": asr_ms,
            "fmt_ms": fmt_ms,
            "llm_ms": llm_ms,
            "features": tc.get("expected_features", []),
            "feature_pass": feature_pass,
            "status": status,
        })

        print(f"{name:<25} {asr_wer:>7.1%} {fmt_wer:>7.1%} {llm_wer:>7.1%} {asr_ms:>7.0f} {fmt_ms:>7.0f} {llm_ms:>7.0f} {status:>8}")

        if verbose:
            print(f"  Expected:    {expected_text!r}")
            print(f"  ASR:         {asr_text!r}")
            print(f"  Formatted:   {formatted_text!r}")
            print(f"  LLM Polish:  {llm_text!r}")
            print()

    total_elapsed = time.perf_counter() - total_start

    # Summary
    print("\n" + "=" * 95)
    print("SUMMARY")
    print("=" * 95)

    if results:
        avg_asr_wer = sum(r["asr_wer"] for r in results) / len(results)
        avg_fmt_wer = sum(r["fmt_wer"] for r in results) / len(results)
        avg_llm_wer = sum(r["llm_wer"] for r in results) / len(results)
        avg_asr_ms = sum(r["asr_ms"] for r in results) / len(results)
        avg_fmt_ms = sum(r["fmt_ms"] for r in results) / len(results)
        avg_llm_ms = sum(r["llm_ms"] for r in results) / len(results)
        passed = sum(1 for r in results if r["status"] == "PASS")

        print(f"Tests passed:       {passed}/{len(results)}")
        print(f"Average ASR WER:    {avg_asr_wer:.1%}")
        print(f"Average Format WER: {avg_fmt_wer:.1%}")
        print(f"Average LLM WER:    {avg_llm_wer:.1%}")
        print(f"Average ASR time:   {avg_asr_ms:.0f}ms")
        print(f"Average Format time:{avg_fmt_ms:.0f}ms")
        print(f"Average LLM time:   {avg_llm_ms:.0f}ms")
        print(f"Average total time: {avg_asr_ms + avg_fmt_ms + avg_llm_ms:.0f}ms")
        print(f"Total test time:    {total_elapsed:.1f}s")

        # Feature breakdown
        print("\nFeature Coverage:")
        all_features = set()
        for r in results:
            all_features.update(r["features"])
        for feature in sorted(all_features):
            feature_tests = [r for r in results if feature in r["features"]]
            feature_pass = sum(1 for r in feature_tests if r["feature_pass"])
            print(f"  {feature:<25} {feature_pass}/{len(feature_tests)} passed")

    # Save detailed results
    report_path = os.path.join(PROJECT_ROOT, "tests", "e2e_test_report.json")
    with open(report_path, "w") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_tests": len(results),
            "total_time_s": total_elapsed,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nDetailed report saved to: {report_path}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WhisperFlow E2E test suite")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--skip-tts", action="store_true", help="Skip TTS generation (use existing audio)")
    args = parser.parse_args()
    run_tests(verbose=args.verbose, skip_tts=args.skip_tts)
