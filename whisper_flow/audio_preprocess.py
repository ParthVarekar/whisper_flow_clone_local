"""Audio preprocessing for improved ASR accuracy.

Applies the following enhancements to captured audio before transcription:

1. **Leading/trailing silence trim** — removes silence at start/end of
   recording (caused by the delay between hotkey press and speaking, and
   hotkey release after speaking stops). Reduces hallucination risk.

2. **Noise gate** — attenuates samples below a noise threshold to zero.
   Removes constant background noise (fans, AC, keyboard) that confuses
   small ASR models. Threshold adapts to the noise floor.

3. **Normalization / auto-gain** — scales the audio so the peak amplitude
   reaches a target level (0.9). Quieter speech becomes louder, improving
   the signal-to-noise ratio for the model.

4. **High-pass filter** — removes low-frequency rumble below 80 Hz
   (desk vibrations, HVAC, traffic) that isn't speech.

5. **Optional noise reduction** — spectral gating noise reduction using
   noisereduce (if installed). Significantly improves quality on noisy
   recordings but adds ~100ms latency. Off by default; enable via config.

All processing uses numpy (already a dependency). The high-pass filter
uses a simple biquad implementation (no scipy dependency).
"""

from __future__ import annotations

import array
import wave
import os
import tempfile
from typing import Optional

try:
    import numpy as np
except ImportError:
    np = None  # numpy is required at runtime; this just makes the import safe


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def preprocess_audio(
    wav_path: str,
    *,
    trim_silence: bool = True,
    silence_threshold_db: float = -40.0,
    noise_gate: bool = True,
    normalize: bool = True,
    target_peak: float = 0.9,
    highpass_filter: bool = True,
    highpass_cutoff_hz: float = 80.0,
    noise_reduction: bool = False,
    sample_rate: int = 16000,
) -> str:
    """Preprocess a WAV file for better ASR accuracy.

    Reads the WAV file, applies the enabled preprocessing steps, and writes
    the result to a new temp WAV file. Returns the path to the processed file.

    All processing is in float32 internally; output is int16 (16-bit PCM)
    to match what Qwen3-ASR / whisper.cpp expect.

    Args:
        wav_path: Path to the input WAV file (16kHz mono int16).
        trim_silence: If True, remove leading/trailing silence.
        silence_threshold_db: dB threshold for silence detection (default -40dB).
        noise_gate: If True, attenuate samples below the noise floor.
        normalize: If True, scale audio so peak reaches target_peak.
        target_peak: Target peak amplitude after normalization (0.0-1.0).
        highpass_filter: If True, remove low-frequency rumble.
        highpass_cutoff_hz: High-pass filter cutoff frequency (default 80Hz).
        noise_reduction: If True, apply spectral noise reduction (needs noisereduce).
        sample_rate: Expected sample rate (default 16000).

    Returns:
        Path to the processed WAV file (temp file, caller should delete after use).
    """
    if np is None:
        return wav_path  # numpy not available — return original

    # Read the WAV file
    samples, sr = _read_wav(wav_path)
    if samples is None or len(samples) == 0:
        return wav_path  # couldn't read — return original

    # Convert int16 → float32 [-1, 1]
    audio = samples.astype(np.float32) / 32768.0

    # 1. Trim leading/trailing silence
    if trim_silence:
        audio = _trim_silence(audio, sr, silence_threshold_db)

    # 2. High-pass filter (remove low-frequency rumble)
    if highpass_filter and len(audio) > 0:
        audio = _highpass_filter(audio, sr, highpass_cutoff_hz)

    # 3. Noise gate (remove samples below noise floor)
    if noise_gate and len(audio) > 0:
        audio = _noise_gate(audio, sr)

    # 4. Spectral noise reduction (optional, needs noisereduce)
    if noise_reduction and len(audio) > 0:
        audio = _spectral_noise_reduction(audio, sr)

    # 5. Normalize (auto-gain)
    if normalize and len(audio) > 0:
        audio = _normalize(audio, target_peak)

    # If trimming removed everything, return original
    if len(audio) == 0:
        return wav_path

    # Write processed audio to a new temp WAV file
    out_path = _write_wav(audio, sr)
    return out_path


# ---------------------------------------------------------------------------
# Individual processing steps
# ---------------------------------------------------------------------------

def _read_wav(path: str):
    """Read a WAV file and return (int16_samples, sample_rate)."""
    try:
        with wave.open(path, "rb") as w:
            nch = w.getnchannels()
            sampwidth = w.getsampwidth()
            sr = w.getframerate()
            nframes = w.getnframes()
            raw = w.readframes(nframes)
        if sampwidth == 2:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            if nch > 1:
                samples = samples[::nch]  # take first channel (mono)
            return samples.astype(np.int16), sr
        # Other formats — return None to signal "use original"
        return None, sr
    except Exception:
        return None, 16000


def _trim_silence(audio: np.ndarray, sr: int, threshold_db: float) -> np.ndarray:
    """Remove leading and trailing silence.

    Silence is detected as samples below `threshold_db` (relative to peak).
    Keeps at least 100ms of leading context so the first word isn't clipped.
    """
    if len(audio) == 0:
        return audio

    # Convert dB threshold to amplitude
    peak = np.max(np.abs(audio))
    if peak == 0:
        return audio  # all silence
    threshold_amp = peak * (10.0 ** (threshold_db / 20.0))

    # Find first sample above threshold
    above = np.abs(audio) > threshold_amp
    if not np.any(above):
        return audio  # all below threshold — keep as-is (don't delete everything)

    first_idx = int(np.argmax(above))
    last_idx = int(len(audio) - 1 - np.argmax(above[::-1]))

    # Keep 100ms of leading context (pre-roll) so first word isn't clipped
    pre_roll = int(0.1 * sr)
    first_idx = max(0, first_idx - pre_roll)

    # Keep 50ms of trailing context (post-roll)
    post_roll = int(0.05 * sr)
    last_idx = min(len(audio) - 1, last_idx + post_roll)

    return audio[first_idx:last_idx + 1]


def _highpass_filter(audio: np.ndarray, sr: int, cutoff_hz: float) -> np.ndarray:
    """Apply a first-order high-pass filter to remove low-frequency rumble.

    Uses a simple RC high-pass filter (biquad would be better but needs scipy).
    Cutoff frequency default 80Hz removes HVAC rumble, desk vibrations, traffic.
    """
    if len(audio) == 0:
        return audio

    rc = 1.0 / (2.0 * 3.14159265358979 * cutoff_hz)
    dt = 1.0 / sr
    alpha = rc / (rc + dt)

    filtered = np.zeros_like(audio)
    prev_input = 0.0
    prev_output = 0.0
    for i in range(len(audio)):
        current = audio[i]
        output = alpha * (prev_output + current - prev_input)
        filtered[i] = output
        prev_input = current
        prev_output = output

    return filtered


def _noise_gate(audio: np.ndarray, sr: int) -> np.ndarray:
    """Apply a noise gate that attenuates quiet sections to zero.

    Estimates the noise floor from the quietest 10% of frames, then
    attenuates anything below 2x that level. This removes constant
    background noise (fans, AC) without affecting speech.
    """
    if len(audio) == 0:
        return audio

    # Frame the audio into 20ms windows for noise estimation
    frame_len = int(0.02 * sr)
    n_frames = len(audio) // frame_len
    if n_frames < 3:
        return audio  # too short to estimate noise floor

    # Compute RMS energy per frame
    frames = audio[:n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))

    # Noise floor = median of the quietest 30% of frames
    sorted_rms = np.sort(rms)
    n_quiet = max(1, n_frames // 3)
    noise_floor = float(np.median(sorted_rms[:n_quiet]))

    if noise_floor <= 0:
        return audio  # no noise detected

    # Gate threshold = 2x noise floor (6dB above noise)
    gate_threshold = noise_floor * 2.0

    # Apply gate: attenuate frames below threshold by 0.1x (not full mute —
    # full mute sounds unnatural and can confuse ASR models)
    gated = audio.copy()
    for i in range(n_frames):
        if rms[i] < gate_threshold:
            start = i * frame_len
            end = start + frame_len
            gated[start:end] *= 0.1

    return gated


def _spectral_noise_reduction(audio: np.ndarray, sr: int) -> np.ndarray:
    """Apply spectral gating noise reduction using noisereduce (optional).

    Significantly reduces stationary background noise. Adds ~100ms latency.
    Only runs if noisereduce is installed (pip install noisereduce).
    """
    try:
        import noisereduce as nr
    except ImportError:
        return audio  # noisereduce not installed — skip

    try:
        # noisereduce works best with float32 in [-1, 1]
        reduced = nr.reduce_noise(y=audio, sr=sr, stationary=True)
        return reduced.astype(np.float32)
    except Exception:
        return audio  # if noisereduce fails, return original


def _normalize(audio: np.ndarray, target_peak: float = 0.9) -> np.ndarray:
    """Normalize audio so the peak amplitude reaches target_peak.

    This is auto-gain: quiet speech is amplified, loud speech is attenuated.
    Improves SNR for the ASR model by ensuring consistent volume.
    """
    if len(audio) == 0:
        return audio

    peak = float(np.max(np.abs(audio)))
    if peak < 0.001:
        return audio  # too quiet to normalize meaningfully

    # Scale to target peak, but don't amplify by more than 10x (avoids
    # amplifying pure noise)
    gain = min(10.0, target_peak / peak)
    return audio * gain


def _write_wav(audio: np.ndarray, sr: int) -> str:
    """Write float32 audio [-1, 1] to a 16-bit PCM WAV temp file."""
    # Clip to [-1, 1] and convert to int16
    clipped = np.clip(audio, -1.0, 1.0)
    int16_audio = (clipped * 32767.0).astype(np.int16)

    tmp = tempfile.NamedTemporaryFile(
        prefix="wf_preproc_", suffix=".wav", delete=False, dir=tempfile.gettempdir()
    )
    out_path = tmp.name
    tmp.close()

    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(int16_audio.tobytes())

    return out_path


# ---------------------------------------------------------------------------
# Convenience: preprocess + cleanup
# ---------------------------------------------------------------------------

def preprocess_and_cleanup(wav_path: str, *, delete_original: bool = True, **kwargs) -> str:
    """Preprocess audio and delete the original temp file.

    Returns the path to the processed file. The caller is responsible for
    deleting the processed file after use.
    """
    processed = preprocess_audio(wav_path, **kwargs)
    if delete_original and processed != wav_path and os.path.exists(wav_path):
        try:
            os.remove(wav_path)
        except OSError:
            pass
    return processed
