#!/usr/bin/env python3
"""WhisperFlow end-to-end test suite with auto backend detection.

AUTOMATIC BACKEND SELECTION:
  - ASR: tries local crispasr.exe first, falls back to z-ai cloud ASR
  - LLM: tries local llama-server (port 8081) first, falls back to z-ai cloud LLM
  - TTS: uses z-ai cloud TTS (for generating test audio only)

This means:
  - On the user's Windows device: local Qwen3-ASR + gemma-4 are used
  - In the z-ai workspace: z-ai cloud endpoints are used (no local models)

The test reports which backend was used for each stage so you can compare
local vs cloud performance.

Pipeline per test:
  1. Generate audio (z-ai TTS)
  2. ASR (local crispasr OR z-ai cloud)
  3. Rule-based formatting (our formatting.py — always local, instant)
  4. LLM polishing (local llama-server OR z-ai cloud)

Reports:
  - Word Error Rate (WER) at each stage
  - Timing for each stage
  - Which backend was used (local vs cloud)
  - Feature detection (lists, backtracks, fillers, etc.)

Usage:
    python tests/test_e2e_pipeline.py
    python tests/test_e2e_pipeline.py --verbose
    python tests/test_e2e_pipeline.py --skip-tts  # reuse existing audio
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from whisper_flow.formatting import apply_smart_formatting
from whisper_flow.prompts import SYSTEM_PROMPTS


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

TEST_CASES = [
    # Short (3-8s)
    {"name": "short_simple", "text": "This is a simple test of the voice dictation system."},
    {"name": "short_fillers", "text": "Um, I was thinking, uh, maybe we should, like, go to the store.",
     "expected_formatted_contains": "thinking maybe we should go to the store"},
    {"name": "short_backtrack", "text": "Let's meet at 2pm. Actually, let's meet at 3pm instead.",
     "expected_formatted_contains": "3"},
    {"name": "short_numbers", "text": "I have twenty five apples and one hundred dollars.",
     "expected_formatted_contains": "25"},

    # Medium (8-15s)
    {"name": "medium_proper_nouns", "text": "I am testing WhisperFlow with Qwen3-ASR and Moonshine models for voice dictation.",
     "expected_formatted_contains": "WhisperFlow"},
    {"name": "medium_currency", "text": "The laptop costs twelve hundred dollars and the phone costs fifty pounds.",
     "expected_formatted_contains": "$"},
    {"name": "medium_time", "text": "The meeting is at three thirty PM and ends at four forty five PM.",
     "expected_formatted_contains": "3:30"},
    {"name": "medium_list", "text": "I need to buy several items: apples, bananas, oranges, milk, and bread."},

    # Long (15-30s)
    {"name": "long_paragraph", "text": "This is a longer test to check how the system handles extended dictation. I am going to speak for about twenty seconds to see if the transcription remains accurate throughout. The system should remove filler words, fix grammar, and produce clean formatted text."},
    {"name": "long_corrections", "text": "First, I want to say that the project deadline is Friday. Actually, no, it's Monday. Sorry, I meant Tuesday. The team should be ready by then.",
     "expected_formatted_contains": "Tuesday"},
    {"name": "long_technical", "text": "We need to update the whisper_flow daemon to use the qwen3_asr backend instead of moonshine. The config file has the wrong settings. Also fix the crispasr command to include the prompt flag for proper noun detection."},

    # Edge cases
    {"name": "edge_very_short", "text": "Hello world."},
    {"name": "edge_stutter", "text": "I I I want to to to go to the the store.",
     "expected_formatted_contains": "I want to go to the store"},
    {"name": "edge_numbers", "text": "I have two cats, three dogs, and twelve fish. That is seventeen animals total.",
     "expected_formatted_contains": "17"},
]


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

# Local model paths (Windows — user's device)
CRISPASR_BIN = r"C:\Users\Parth\Desktop\whisper\third_party\crispasr\crispasr.exe"
CRISPASR_MODEL = r"C:\Users\Parth\Desktop\whisper\models\qwen3-asr-1.7b-q4_k.gguf"
LLM_SERVER_URL = "http://127.0.0.1:8081"


def check_local_asr() -> bool:
    """Check if local crispasr.exe is available."""
    return os.path.isfile(CRISPASR_BIN) and os.path.isfile(CRISPASR_MODEL)


def check_local_llm() -> bool:
    """Check if local llama-server is running on port 8081."""
    try:
        req = urllib.request.Request(f"{LLM_SERVER_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status < 500
    except Exception:
        return False


def _extract_json_from_output(output: str) -> dict | None:
    """Extract JSON from z-ai CLI output (has emoji status lines)."""
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', output, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# ASR backends
# ---------------------------------------------------------------------------

def asr_local(audio_path: str) -> tuple[str, float]:
    """Transcribe using local crispasr.exe (Qwen3-ASR)."""
    t0 = time.perf_counter()
    try:
        cmd = [
            CRISPASR_BIN, "-m", CRISPASR_MODEL,
            "-l", "en", "-t", "8", "-bs", "5", "-nt", "-np",
            "--prompt", "Transcribe in English only.",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        elapsed = (time.perf_counter() - t0) * 1000
        if result.returncode == 0:
            text = result.stdout.strip()
            # Clean up Qwen3-ASR artifacts
            text = re.sub(r'^language\s+\w+\s*', '', text, flags=re.IGNORECASE).strip()
            text = re.sub(r'<[^>]+>', '', text).strip()
            return text, elapsed
        return f"ERROR: {result.stderr[:200]}", elapsed
    except Exception as exc:
        return f"ERROR: {exc}", (time.perf_counter() - t0) * 1000


def asr_cloud(audio_path: str) -> tuple[str, float]:
    """Transcribe using z-ai cloud ASR."""
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
    except Exception as exc:
        return f"ERROR: {exc}", (time.perf_counter() - t0) * 1000


def transcribe_audio(audio_path: str, use_local: bool) -> tuple[str, float, str]:
    """Transcribe audio. Returns (text, time_ms, backend_name)."""
    if use_local:
        text, ms = asr_local(audio_path)
        return text, ms, "local Qwen3-ASR"
    else:
        text, ms = asr_cloud(audio_path)
        return text, ms, "z-ai cloud ASR"


# ---------------------------------------------------------------------------
# LLM backends
# ---------------------------------------------------------------------------

def llm_local(text: str, system_prompt: str) -> tuple[str, float]:
    """Polish using local llama-server (gemma-4)."""
    t0 = time.perf_counter()
    try:
        body = json.dumps({
            "model": "local",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
            "max_tokens": 2048,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{LLM_SERVER_URL}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
        elapsed = (time.perf_counter() - t0) * 1000

        payload = json.loads(raw)
        content = payload["choices"][0]["message"]["content"]
        return content.strip(), elapsed
    except Exception as exc:
        return f"ERROR: {exc}", (time.perf_counter() - t0) * 1000


def llm_cloud(text: str, system_prompt: str) -> tuple[str, float]:
    """Polish using z-ai cloud LLM."""
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
    except Exception:
        return text, (time.perf_counter() - t0) * 1000


def llm_polish(text: str, system_prompt: str, use_local: bool) -> tuple[str, float, str]:
    """Polish text. Returns (text, time_ms, backend_name)."""
    if use_local:
        result, ms = llm_local(text, system_prompt)
        return result, ms, "local gemma-4"
    else:
        result, ms = llm_cloud(text, system_prompt)
        return result, ms, "z-ai cloud LLM"


# ---------------------------------------------------------------------------
# TTS — tries z-ai cloud first, falls back to Windows native SAPI TTS
# ---------------------------------------------------------------------------

def _tts_zai(text: str, output_path: str) -> bool:
    """Generate audio using z-ai cloud TTS (available in z-ai workspace)."""
    try:
        result = subprocess.run(
            ["z-ai", "tts", "-i", text[:1000], "-o", output_path],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception:
        return False


def _tts_windows_sapi(text: str, output_path: str) -> bool:
    """Generate audio using Windows native SAPI TTS (no dependencies needed).

    Uses PowerShell to invoke the SAPI.SpVoice COM object, which is built into
    Windows. This works on the user's device without needing z-ai CLI.
    The output is a WAV file at the specified path.
    """
    if sys.platform != "win32":
        return False
    try:
        # PowerShell script to generate WAV using SAPI
        # SAPI outputs to a temp file, then we move it
        ps_script = f'''
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = 0
$synth.Volume = 100
# Use a decent voice if available
$voices = $synth.GetInstalledVoices()
if ($voices.Count -gt 0) {{
    $synth.SelectVoice($voices[0].VoiceInfo.Name)
}}
$synth.SetOutputToWaveFile("{output_path}")
$synth.Speak("{text[:1000].replace('"', '`"')}")
$synth.Dispose()
'''
        result = subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception:
        return False


def _tts_pyttsx3(text: str, output_path: str) -> bool:
    """Generate audio using pyttsx3 (cross-platform, needs pip install)."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.save_to_file(text[:1000], output_path)
        engine.runAndWait()
        engine.stop()
        return os.path.exists(output_path)
    except Exception:
        return False


def generate_tts(text: str, output_path: str) -> bool:
    """Generate audio using the best available TTS method.

    Tries in order:
      1. z-ai cloud TTS (best quality, available in z-ai workspace)
      2. Windows SAPI TTS (built into Windows, no dependencies)
      3. pyttsx3 (cross-platform, needs pip install)

    Returns True if audio was generated successfully.
    """
    # Try z-ai cloud first
    if _tts_zai(text, output_path):
        return True

    # Fall back to Windows SAPI (no dependencies needed on Windows)
    if _tts_windows_sapi(text, output_path):
        return True

    # Fall back to pyttsx3 (if installed)
    if _tts_pyttsx3(text, output_path):
        return True

    return False


# ---------------------------------------------------------------------------
# WER calculation
# ---------------------------------------------------------------------------

def calculate_wer(reference: str, hypothesis: str) -> float:
    ref_words = reference.lower().split()
    hyp_words = hypothesis.lower().split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
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


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run_tests(verbose: bool = False, skip_tts: bool = False):
    audio_dir = os.path.join(tempfile.gettempdir(), "wf_test_audio")
    os.makedirs(audio_dir, exist_ok=True)

    # Detect backends
    local_asr = check_local_asr()
    local_llm = check_local_llm()
    asr_backend = "local Qwen3-ASR" if local_asr else "z-ai cloud ASR"
    llm_backend = "local gemma-4" if local_llm else "z-ai cloud LLM"

    print("=" * 95)
    print("WhisperFlow E2E Test Suite")
    print("=" * 95)
    print(f"ASR backend:  {asr_backend} ({'✅ detected' if local_asr else '❌ not found → fallback'})")
    print(f"LLM backend:  {llm_backend} ({'✅ detected' if local_llm else '❌ not running → fallback'})")
    # Detect TTS backend
    if shutil.which("z-ai"):
        tts_backend = "z-ai cloud TTS"
    elif sys.platform == "win32":
        tts_backend = "Windows SAPI TTS"
    else:
        tts_backend = "pyttsx3 (if installed)"

    print(f"TTS backend:  {tts_backend} (test audio generation only)")
    print()

    system_prompt = SYSTEM_PROMPTS["medium"]
    results = []
    total_start = time.perf_counter()

    print(f"{'Test':<25} {'ASR WER':>8} {'Fmt WER':>8} {'LLM WER':>8} {'ASR ms':>8} {'Fmt ms':>8} {'LLM ms':>8} {'Status':>8}")
    print("-" * 95)

    for tc in TEST_CASES:
        name = tc["name"]
        expected_text = tc["text"]
        audio_path = os.path.join(audio_dir, f"{name}.wav")

        # Generate audio
        if not skip_tts:
            if not os.path.exists(audio_path):
                if not generate_tts(expected_text, audio_path):
                    print(f"  [SKIP] TTS failed for {name}")
                    continue
        elif not os.path.exists(audio_path):
            continue

        # ASR
        asr_text, asr_ms, asr_be = transcribe_audio(audio_path, local_asr)

        # Formatting (always local)
        t_fmt = time.perf_counter()
        formatted = apply_smart_formatting(asr_text, writing_style="default")
        fmt_ms = (time.perf_counter() - t_fmt) * 1000

        # LLM polish
        llm_text, llm_ms, llm_be = llm_polish(formatted, system_prompt, local_llm)

        # WER
        asr_wer = calculate_wer(expected_text, asr_text)
        fmt_wer = calculate_wer(expected_text, formatted)
        llm_wer = calculate_wer(expected_text, llm_text)

        # Feature check
        feature_pass = True
        if "expected_formatted_contains" in tc:
            if tc["expected_formatted_contains"].lower() not in formatted.lower():
                feature_pass = False

        status = "PASS" if asr_wer < 0.5 and feature_pass else "CHECK"

        results.append({
            "name": name, "expected": expected_text,
            "asr": asr_text, "formatted": formatted, "llm_polished": llm_text,
            "asr_wer": asr_wer, "fmt_wer": fmt_wer, "llm_wer": llm_wer,
            "asr_ms": asr_ms, "fmt_ms": fmt_ms, "llm_ms": llm_ms,
            "asr_backend": asr_be, "llm_backend": llm_be,
            "status": status,
        })

        print(f"{name:<25} {asr_wer:>7.1%} {fmt_wer:>7.1%} {llm_wer:>7.1%} {asr_ms:>7.0f} {fmt_ms:>7.0f} {llm_ms:>7.0f} {status:>8}")

        if verbose:
            print(f"  Expected:   {expected_text!r}")
            print(f"  ASR ({asr_be}): {asr_text!r}")
            print(f"  Formatted:  {formatted!r}")
            print(f"  LLM ({llm_be}): {llm_text!r}")
            print()

    total_elapsed = time.perf_counter() - total_start

    # Summary
    print("\n" + "=" * 95)
    print("SUMMARY")
    print("=" * 95)

    if results:
        n = len(results)
        avg = lambda key: sum(r[key] for r in results) / n
        passed = sum(1 for r in results if r["status"] == "PASS")

        print(f"Tests passed:       {passed}/{n}")
        print(f"ASR backend:        {results[0]['asr_backend']}")
        print(f"LLM backend:        {results[0]['llm_backend']}")
        print(f"Average ASR WER:    {avg('asr_wer'):.1%}")
        print(f"Average Format WER: {avg('fmt_wer'):.1%}")
        print(f"Average LLM WER:    {avg('llm_wer'):.1%}")
        print(f"Average ASR time:   {avg('asr_ms'):.0f}ms")
        print(f"Average Format time:{avg('fmt_ms'):.0f}ms")
        print(f"Average LLM time:   {avg('llm_ms'):.0f}ms")
        print(f"Average total:      {avg('asr_ms') + avg('fmt_ms') + avg('llm_ms'):.0f}ms")
        print(f"Total test time:    {total_elapsed:.1f}s")

    report_path = os.path.join(PROJECT_ROOT, "tests", "e2e_test_report.json")
    with open(report_path, "w") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "asr_backend": asr_backend,
            "llm_backend": llm_backend,
            "total_tests": len(results),
            "total_time_s": total_elapsed,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nReport: {report_path}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WhisperFlow E2E test with auto backend detection")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--skip-tts", action="store_true")
    args = parser.parse_args()
    run_tests(verbose=args.verbose, skip_tts=args.skip_tts)
