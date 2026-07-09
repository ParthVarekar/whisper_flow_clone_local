"""Audio capture + normalization + chunking.

All audio is normalized to 16-bit 16 kHz mono WAV (whisper.cpp's preferred
format) before being handed to the transcription backend.

  * File input: ffmpeg converts any input format -> normalized WAV.
  * Mic input:  arecord (ALSA, usually preinstalled) or ffmpeg (-f alsa/-f pulse).
  * Chunking:   optional ffmpeg-based split for very long files. whisper.cpp
                already processes long audio in 30s windows internally, so
                chunking is OFF by default and only needed for extremely long
                recordings or for parallel/robust handling.
"""

from __future__ import annotations

from collections import deque
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from typing import Callable, Iterator

from .config import AudioConfig
from .errors import AudioError, BinaryNotFoundError

WAV_CODEC = "pcm_s16le"
_ACTIVE_CAPTURE_LOCK = threading.Lock()
_ACTIVE_CAPTURE_PROC: subprocess.Popen[str] | None = None
_ACTIVE_CAPTURE_STOP: threading.Event | None = None


def _which(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise BinaryNotFoundError(name)
    return path


def _probe_duration_seconds(path: str, ffmpeg_bin: str) -> float:
    """Best-effort duration probe via ffprobe (may not be installed)."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return 0.0
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if proc.returncode == 0:
            return float(proc.stdout.decode("utf-8", "replace").strip() or 0.0)
    except (ValueError, OSError):
        pass
    return 0.0


def validate_wav(path: str) -> float:
    """Sanity-check a WAV file's header. Returns duration in seconds.

    Raises AudioError on corrupted/empty/non-WAV files.
    """
    if not os.path.isfile(path):
        raise AudioError(f"audio file not found: {path!r}")
    if os.path.getsize(path) < 44:
        raise AudioError(f"audio file too small to be a valid WAV: {path!r}")
    try:
        with wave.open(path, "rb") as w:
            nch = w.getnchannels()
            sampwidth = w.getsampwidth()
            framerate = w.getframerate()
            nframes = w.getnframes()
        if nch < 1 or sampwidth < 1 or framerate < 1:
            raise AudioError(f"invalid WAV params in {path!r}: ch={nch} sampwidth={sampwidth} rate={framerate}")
        if framerate not in (8000, 11025, 16000, 22050, 32000, 44100, 48000, 96000, 192000):
            # not fatal — whisper.cpp + ffmpeg resample to 16k; just note it
            pass
        return nframes / framerate if framerate else 0.0
    except wave.Error as exc:
        raise AudioError(f"corrupted WAV header in {path!r}: {exc}") from exc
    except OSError as exc:
        raise AudioError(f"cannot read {path!r}: {exc}") from exc


def normalize_file(cfg: AudioConfig, input_path: str, *, verbose: bool = False) -> str:
    """Convert any audio file to a normalized 16kHz mono WAV. Returns the new path.

    If the input is already a compliant WAV, ffmpeg is still run (cheap and
    guarantees format correctness). The output lives in a temp dir.
    """
    if not os.path.isfile(input_path):
        raise AudioError(f"input audio file not found: {input_path!r}")
    ffmpeg = _which(cfg.ffmpeg_bin)

    tmp = tempfile.NamedTemporaryFile(
        prefix="wf_norm_", suffix=".wav", delete=False, dir=tempfile.gettempdir()
    )
    out_path = tmp.name
    tmp.close()

    cmd = [
        ffmpeg, "-y", "-i", input_path,
        "-ar", str(cfg.sample_rate),
        "-ac", str(cfg.channels),
        "-c:a", WAV_CODEC,
        out_path,
    ]
    if verbose:
        print(f"[audio] normalize: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        stderr = proc.stderr.decode("utf-8", "replace")
        os.path.exists(out_path) and os.remove(out_path)
        raise AudioError(f"ffmpeg normalization failed for {input_path!r}:\n{stderr.strip()[:800]}")
    return out_path


def capture_mic(cfg: AudioConfig, duration: float, *, verbose: bool = False) -> str:
    """Record from the microphone for `duration` seconds. Returns a WAV path.

    Backend selection (cfg.mic_backend='auto' picks the first available):
      - sounddevice (optional `pip install whisper-flow[mic]`): cross-platform, default device auto
      - arecord: Linux (ALSA, preinstalled on most distros)
      - ffmpeg:   cross-platform fallback with per-OS input format:
          Linux:   -f pulse | -f alsa  (pulse preferred if pactl present)
          macOS:   -f avfoundation -i ":default"  (documented default alias)
          Windows: -f dshow -i audio="<device>"  (use list_devices_dshow() to enumerate)

    duration <= 0 records until Ctrl+C (ffmpeg/sounddevice only; arecord needs -d).
    """
    backend = cfg.mic_backend
    if backend == "auto":
        backend = _auto_mic_backend()

    if backend == "sounddevice":
        return _capture_sounddevice(cfg, duration, verbose=verbose)
    if backend == "arecord":
        return _capture_arecord(cfg, duration, verbose=verbose)
    return _capture_ffmpeg(cfg, duration, verbose=verbose)


def stop_active_capture() -> bool:
    """Best-effort stop for an in-flight microphone capture.

    Returns True if a capture process was active and a terminate signal was sent.
    """
    with _ACTIVE_CAPTURE_LOCK:
        proc = _ACTIVE_CAPTURE_PROC
        stop_evt = _ACTIVE_CAPTURE_STOP
    if stop_evt is not None:
        stop_evt.set()
        return True
    if proc is None or proc.poll() is not None:
        return False
    try:
        if proc.stdin is not None:
            proc.stdin.write("q\n")
            proc.stdin.flush()
        else:
            proc.terminate()
        return True
    except Exception:  # noqa: BLE001
        try:
            proc.terminate()
            return True
        except Exception:  # noqa: BLE001
            return False


def stream_mic_chunks(
    cfg: AudioConfig,
    chunk_seconds: int,
    *,
    on_amplitude: Callable[[float], None] | None = None,
    stop_event: threading.Event | None = None,
    verbose: bool = False,
) -> Iterator[tuple[str, float]]:
    """Yield `(wav_path, duration_seconds)` chunks from the default microphone.

    This is the live-streaming path used for indefinite mic sessions. On
    Windows/macOS/Linux we prefer the sounddevice backend because it gives us a
    clean in-process stop signal and avoids the awkward subprocess teardown
    semantics of ffmpeg for open-ended capture.
    """
    try:
        import numpy as _np
        import sounddevice as sd
    except ImportError as exc:
        raise AudioError(
            "live streaming requires the sounddevice backend; run `pip install sounddevice`"
        ) from exc

    fs, ch = cfg.sample_rate, cfg.channels
    frames_per_chunk = max(1, int(chunk_seconds * fs))
    stop_evt = stop_event or threading.Event()
    q: deque[_np.ndarray] = deque()
    q_frames = 0
    q_lock = threading.Lock()

    if verbose:
        print(f"[audio] stream (sounddevice): fs={fs} ch={ch} chunk={chunk_seconds}s", flush=True)

    def _callback(indata, _frames, _time, _status) -> None:
        nonlocal q_frames
        arr = indata.copy()
        with q_lock:
            q.append(arr)
            q_frames += len(arr)
        if on_amplitude is not None:
            rms = float(_np.sqrt(_np.mean(arr.astype(_np.float32) ** 2))) / 32768.0
            try:
                on_amplitude(rms)
            except Exception:  # noqa: BLE001
                pass
        if stop_evt.is_set():
            raise sd.CallbackStop()

    def _pop_frames(n_frames: int) -> _np.ndarray:
        nonlocal q_frames
        parts: list[_np.ndarray] = []
        need = n_frames
        with q_lock:
            while need > 0 and q:
                arr = q[0]
                if len(arr) <= need:
                    parts.append(q.popleft())
                    need -= len(arr)
                else:
                    parts.append(arr[:need].copy())
                    q[0] = arr[need:].copy()
                    need = 0
            taken = n_frames - need
            q_frames -= taken
        return _np.concatenate(parts, axis=0) if parts else _np.empty((0, ch), dtype=_np.int16)

    with _ACTIVE_CAPTURE_LOCK:
        global _ACTIVE_CAPTURE_STOP
        _ACTIVE_CAPTURE_STOP = stop_evt

    try:
        with sd.InputStream(
            samplerate=fs,
            channels=ch,
            dtype="int16",
            device=_sd_device(cfg.mic_device),
            callback=_callback,
        ):
            while True:
                with q_lock:
                    available = q_frames
                if available >= frames_per_chunk:
                    arr = _pop_frames(frames_per_chunk)
                    if len(arr):
                        yield _write_wav_chunk(arr, fs, ch), len(arr) / fs
                    continue
                if stop_evt.is_set():
                    if available > 0:
                        arr = _pop_frames(available)
                        if len(arr):
                            yield _write_wav_chunk(arr, fs, ch), len(arr) / fs
                    break
                sd.sleep(100)
    finally:
        with _ACTIVE_CAPTURE_LOCK:
            _ACTIVE_CAPTURE_STOP = None


class LiveMicCapture:
    """Continuous sounddevice microphone capture with rolling/full snapshots.

    This powers the GUI Start/Stop live dictation flow: we keep a rolling window
    for low-latency preview transcription while also retaining the full session
    so Stop can trigger one final high-quality transcription pass.
    """

    def __init__(
        self,
        cfg: AudioConfig,
        *,
        max_window_seconds: int,
        on_amplitude: Callable[[float], None] | None = None,
        verbose: bool = False,
    ):
        self.cfg = cfg
        self.max_window_seconds = max(1, int(max_window_seconds or 1))
        self.on_amplitude = on_amplitude
        self.verbose = verbose
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stop_evt = threading.Event()
        self._rolling = deque()
        self._rolling_frames = 0
        self._all_parts = []
        self._all_frames = 0
        self._stream = None
        self._started = False

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise AudioError(
                "live microphone mode requires the sounddevice backend; run `pip install sounddevice`"
            ) from exc

        fs = self.cfg.sample_rate
        ch = self.cfg.channels
        max_frames = max(fs, int(self.max_window_seconds * fs))

        if self.verbose:
            print(
                f"[audio] live mic: fs={fs} ch={ch} window={self.max_window_seconds}s",
                flush=True,
            )

        def _callback(indata, _frames, _time, _status) -> None:
            nonlocal max_frames
            arr = indata.copy()
            with self._lock:
                self._rolling.append(arr)
                self._rolling_frames += len(arr)
                self._all_parts.append(arr)
                self._all_frames += len(arr)
                while self._rolling_frames > max_frames and self._rolling:
                    dropped = self._rolling.popleft()
                    self._rolling_frames -= len(dropped)
            if self.on_amplitude is not None:
                try:
                    import numpy as _np

                    rms = float(_np.sqrt(_np.mean(arr.astype(_np.float32) ** 2))) / 32768.0
                    self.on_amplitude(rms)
                except Exception:  # noqa: BLE001
                    pass
            self._ready.set()
            if self._stop_evt.is_set():
                raise sd.CallbackStop()

        try:
            self._stream = sd.InputStream(
                samplerate=fs,
                channels=ch,
                dtype="int16",
                device=_sd_device(self.cfg.mic_device),
                callback=_callback,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001
            raise AudioError(f"sounddevice live capture failed: {exc}") from exc

        with _ACTIVE_CAPTURE_LOCK:
            global _ACTIVE_CAPTURE_STOP
            _ACTIVE_CAPTURE_STOP = self._stop_evt
        self._started = True

    def stop(self) -> None:
        self._stop_evt.set()

    def wait_until_audio(self, timeout: float = 2.0) -> bool:
        return self._ready.wait(timeout)

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while not self._stop_evt.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    @property
    def stopped(self) -> bool:
        return self._stop_evt.is_set()

    @property
    def total_duration_sec(self) -> float:
        with self._lock:
            return self._all_frames / self.cfg.sample_rate if self.cfg.sample_rate else 0.0

    def snapshot_window(self) -> tuple[str, float, int]:
        """Return `(wav_path, duration_seconds, offset_ms)` for the rolling window."""
        return self._snapshot(full=False)

    def snapshot_full(self) -> tuple[str, float, int]:
        """Return `(wav_path, duration_seconds, offset_ms)` for the full session."""
        return self._snapshot(full=True)

    def close(self) -> None:
        self._stop_evt.set()
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _ACTIVE_CAPTURE_LOCK:
                global _ACTIVE_CAPTURE_STOP
                if _ACTIVE_CAPTURE_STOP is self._stop_evt:
                    _ACTIVE_CAPTURE_STOP = None

    def _snapshot(self, *, full: bool) -> tuple[str, float, int]:
        try:
            import numpy as _np
        except ImportError as exc:
            raise AudioError("numpy is required for live microphone snapshots") from exc

        with self._lock:
            parts = list(self._all_parts if full else self._rolling)
            total_frames = self._all_frames
        if not parts:
            raise AudioError("no audio captured yet")
        arr = _np.concatenate(parts, axis=0)
        offset_frames = 0 if full else max(0, total_frames - len(arr))
        return (
            _write_wav_chunk(arr, self.cfg.sample_rate, self.cfg.channels),
            len(arr) / self.cfg.sample_rate if self.cfg.sample_rate else 0.0,
            int(round(offset_frames * 1000.0 / self.cfg.sample_rate)) if self.cfg.sample_rate else 0,
        )


def _write_wav_chunk(arr, sample_rate: int, channels: int) -> str:
    tmp = tempfile.NamedTemporaryFile(
        prefix="wf_stream_", suffix=".wav", delete=False, dir=tempfile.gettempdir()
    )
    out_path = tmp.name
    tmp.close()
    with wave.open(out_path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(arr.tobytes())
    return out_path


def list_sounddevice_input_devices() -> list[tuple[str, str]]:
    """Return `(spec, label)` pairs for sounddevice input devices.

    `spec` is suitable for `sounddevice.InputStream(device=...)`.
    """
    try:
        import sounddevice as sd
    except ImportError:
        return [("default", "System default")]

    items: list[tuple[str, str]] = [("default", "System default")]
    try:
        devices = sd.query_devices()
        default_input = sd.default.device[0] if sd.default.device else None
    except Exception:  # noqa: BLE001
        return items

    for idx, dev in enumerate(devices):
        try:
            max_in = int(dev.get("max_input_channels", 0) or 0)
        except Exception:  # noqa: BLE001
            max_in = 0
        if max_in <= 0:
            continue
        name = str(dev.get("name", f"Input {idx}")).replace("\r", " ").replace("\n", " ").strip()
        label = f"{idx}: {name}"
        if default_input == idx:
            label += " (Default)"
        items.append((str(idx), label))
    return items


def _auto_mic_backend() -> str:
    # sounddevice preferred if installed (single codepath, default device)
    try:
        import sounddevice  # noqa: F401
        return "sounddevice"
    except ImportError:
        pass
    if sys.platform == "linux" and shutil.which("arecord"):
        return "arecord"
    return "ffmpeg"


def _capture_sounddevice(cfg: AudioConfig, duration: float, *, verbose: bool) -> str:
    global _ACTIVE_CAPTURE_STOP
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise AudioError(
            "sounddevice backend selected but the package is not installed; "
            "run `pip install sounddevice` (or whisper-flow[mic])"
        ) from exc
    tmp = tempfile.NamedTemporaryFile(
        prefix="wf_mic_", suffix=".wav", delete=False, dir=tempfile.gettempdir()
    )
    out_path = tmp.name
    tmp.close()

    fs, ch = cfg.sample_rate, cfg.channels
    if verbose:
        print(f"[audio] mic (sounddevice): fs={fs} ch={ch} duration={duration}", flush=True)
    try:
        if duration and duration > 0:
            total_frames = int(duration * fs)
            data = sd.rec(total_frames, samplerate=fs, channels=ch, dtype="int16",
                          device=_sd_device(cfg.mic_device))
            sd.wait()
        else:
            import numpy as _np
            frames: list[_np.ndarray] = []
            stop_evt = threading.Event()
            with _ACTIVE_CAPTURE_LOCK:
                _ACTIVE_CAPTURE_STOP = stop_evt

            def _callback(indata, _frames, _time, _status) -> None:
                frames.append(indata.copy())
                if stop_evt.is_set():
                    raise sd.CallbackStop()

            with sd.InputStream(
                samplerate=fs,
                channels=ch,
                dtype="int16",
                device=_sd_device(cfg.mic_device),
                callback=_callback,
            ):
                while not stop_evt.is_set():
                    sd.sleep(100)
            if not frames:
                raise AudioError("sounddevice captured no frames (mic may be missing or muted)")
            data = _np.concatenate(frames, axis=0)
    except KeyboardInterrupt:
        sd.stop()
    except Exception as exc:  # sd.PortAudioError etc.
        raise AudioError(f"sounddevice capture failed: {exc}") from exc
    finally:
        with _ACTIVE_CAPTURE_LOCK:
            _ACTIVE_CAPTURE_STOP = None

    import numpy as _np  # sounddevice returns a numpy array; declared optional dep
    arr = data if isinstance(data, _np.ndarray) else _np.array(data)
    if arr.size == 0:
        raise AudioError("sounddevice captured no frames (mic may be missing or muted)")
    with wave.open(out_path, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(arr.tobytes())
    return out_path


def _sd_device(spec: str):
    """Resolve a sounddevice device spec. 'default' -> None (let PortAudio pick)."""
    if not spec or spec == "default":
        return None
    try:
        return int(spec)
    except ValueError:
        return spec


def _capture_arecord(cfg: AudioConfig, duration: float, *, verbose: bool) -> str:
    arecord = _which(cfg.arecord_bin)
    tmp = tempfile.NamedTemporaryFile(
        prefix="wf_mic_", suffix=".wav", delete=False, dir=tempfile.gettempdir()
    )
    out_path = tmp.name
    tmp.close()

    # -f S16_LE -r <rate> -c <ch>  produces 16-bit PCM at the exact rate/channels.
    # (Previous code used '-f cd' which is 44100/16-bit/stereo and contradicted -r 16000.)
    cmd = [
        arecord,
        "-D", cfg.mic_device,
        "-f", "S16_LE",
        "-r", str(cfg.sample_rate),
        "-c", str(cfg.channels),
        "-t", "wav",
    ]
    if duration and duration > 0:
        cmd += ["-d", str(int(round(duration)))]
    cmd.append(out_path)

    if verbose:
        print(f"[audio] mic (arecord): {' '.join(cmd)}", flush=True)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except KeyboardInterrupt:
        if os.path.isfile(out_path):
            return out_path
        raise AudioError("interrupted before any audio was captured")
    if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        stderr = proc.stderr.decode("utf-8", "replace")
        raise AudioError(
            f"arecord failed (code {proc.returncode}).\n{stderr.strip()[:800]}\n"
            f"  hint: check device {cfg.mic_device!r} with `arecord -l` / `pactl list sources`."
        )
    # arecord was given correct rate/channels already; re-encode codec just in case.
    return _ensure_codec(cfg, out_path, verbose=verbose)


def _capture_ffmpeg(cfg: AudioConfig, duration: float, *, verbose: bool) -> str:
    ffmpeg = _which(cfg.ffmpeg_bin)
    proc = None
    tmp = tempfile.NamedTemporaryFile(
        prefix="wf_mic_", suffix=".wav", delete=False, dir=tempfile.gettempdir()
    )
    out_path = tmp.name
    tmp.close()

    input_args = _ffmpeg_mic_input_args(cfg)
    cmd = [ffmpeg, "-y"] + input_args + [
        "-ar", str(cfg.sample_rate),
        "-ac", str(cfg.channels),
        "-c:a", WAV_CODEC,
    ]
    if duration and duration > 0:
        cmd += ["-t", str(duration)]
    cmd.append(out_path)

    if verbose:
        print(f"[audio] mic (ffmpeg {sys.platform}): {' '.join(cmd)}", flush=True)
    if duration and duration > 0:
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except KeyboardInterrupt:
            if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                return out_path
            raise AudioError("interrupted before any audio was captured")
        if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            stderr = proc.stderr.decode("utf-8", "replace")
            raise AudioError(
                f"ffmpeg mic capture failed (code {proc.returncode}).\n{stderr.strip()[:800]}"
            )
        return out_path

    # Open-ended recording: run ffmpeg as a child process so the GUI Stop button
    # can terminate capture and let the pipeline continue into transcription.
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        with _ACTIVE_CAPTURE_LOCK:
            global _ACTIVE_CAPTURE_PROC
            _ACTIVE_CAPTURE_PROC = proc
        _, stderr = proc.communicate()
    except KeyboardInterrupt:
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            return out_path
        raise AudioError("interrupted before any audio was captured")
    finally:
        with _ACTIVE_CAPTURE_LOCK:
            if proc is not None and _ACTIVE_CAPTURE_PROC is proc:
                _ACTIVE_CAPTURE_PROC = None

    if os.path.isfile(out_path) and os.path.getsize(out_path) > 44:
        return out_path

    if proc.returncode not in (0, 255) and (not os.path.isfile(out_path) or os.path.getsize(out_path) == 0):
        raise AudioError(
            f"ffmpeg mic capture failed (code {proc.returncode}).\n{stderr.strip()[:800]}"
        )
    return out_path


def _ffmpeg_mic_input_args(cfg: AudioConfig) -> list[str]:
    """Build ffmpeg input args per OS. Uses cfg.mic_device ('default' = OS default)."""
    dev = cfg.mic_device or "default"
    if sys.platform == "darwin":
        # AVFoundation: "video:audio". ":default" = no video, default audio input.
        # ffmpeg-devices.html documents the 'default' alias for avfoundation.
        return ["-f", "avfoundation", "-i", f":{dev}"]
    if sys.platform == "win32":
        # DirectShow. No 'default' alias — user must pass a device name string
        # (use `whisper-flow list-devices` to enumerate).
        if dev == "default":
            raise AudioError(
                "Windows ffmpeg/dshow requires an explicit device name. "
                "Run `whisper-flow list-devices` to enumerate, then set "
                "--mic-device \"Microphone (Your Device)\"."
            )
        return ["-f", "dshow", "-i", f"audio={dev}"]
    # Linux: prefer pulse (if pactl present, i.e. PulseAudio/PipeWire-pulse running),
    # fall back to ALSA.
    if shutil.which("pactl") is not None:
        return ["-f", "pulse", "-i", dev]
    return ["-f", "alsa", "-i", dev]


def list_devices_dshow(cfg: AudioConfig) -> list[str]:
    """Windows only: parse `ffmpeg -list_devices true -f dshow -i dummy` stderr.
    Returns a list of audio input device name strings."""
    ffmpeg = _which(cfg.ffmpeg_bin)
    # ffmpeg exits non-zero when listing devices; that's expected.
    proc = subprocess.run(
        [ffmpeg, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    text = proc.stderr.decode("utf-8", "replace")
    devices: list[str] = []
    in_audio = False
    for line in text.splitlines():
        low = line.lower()
        if "directshow audio" in low:
            in_audio = True
            continue
        if "directshow video" in low:
            in_audio = False
            continue
        is_audio_device = in_audio or "(audio)" in low
        if is_audio_device and '"' in line:
            # crude: extract the quoted device name
            parts = line.split('"')
            if len(parts) >= 2:
                devices.append(parts[1])
    return devices


def _ensure_codec(cfg: AudioConfig, path: str, *, verbose: bool) -> str:
    """Re-encode to guaranteed pcm_s16le if needed. Usually a no-op passthrough."""
    ffmpeg = _which(cfg.ffmpeg_bin)
    tmp = tempfile.NamedTemporaryFile(
        prefix="wf_micc_", suffix=".wav", delete=False, dir=tempfile.gettempdir()
    )
    out_path = tmp.name
    tmp.close()
    cmd = [ffmpeg, "-y", "-i", path, "-c:a", WAV_CODEC, out_path]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        os.remove(path)
        return out_path
    # fall back to the original capture
    os.path.exists(out_path) and os.remove(out_path)
    return path


def chunk_audio(cfg: AudioConfig, wav_path: str, *, verbose: bool = False) -> list[str]:
    """Optionally split a WAV into <= chunk_seconds chunks. Returns [wav_path] if disabled."""
    if cfg.chunk_seconds and cfg.chunk_seconds > 0:
        dur = _probe_duration_seconds(wav_path, cfg.ffmpeg_bin)
        if 0 < dur <= cfg.chunk_seconds:
            return [wav_path]
        return _split(wav_path, cfg.chunk_seconds, cfg, verbose=verbose)
    return [wav_path]


def _split(wav_path: str, chunk_seconds: int, cfg: AudioConfig, *, verbose: bool) -> list[str]:
    ffmpeg = _which(cfg.ffmpeg_bin)
    out_dir = tempfile.mkdtemp(prefix="wf_chunks_")
    pattern = os.path.join(out_dir, "chunk_%04d.wav")
    cmd = [
        ffmpeg, "-y", "-i", wav_path,
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-c", "copy",
        pattern,
    ]
    if verbose:
        print(f"[audio] chunk: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace")
        # If segment copy fails (e.g. codec mismatch), fall back to whole file.
        if verbose:
            print(f"[audio] chunking failed, using whole file: {stderr.strip()[:200]}", flush=True)
        return [wav_path]
    chunks = sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir)
        if f.endswith(".wav") and os.path.getsize(os.path.join(out_dir, f)) > 0
    )
    return chunks or [wav_path]
