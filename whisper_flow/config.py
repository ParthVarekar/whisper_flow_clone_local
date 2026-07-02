"""Configuration for whisper-flow.

Load order (later wins):
    1. built-in defaults
    2. JSON config file (--config / WHISPER_FLOW_CONFIG)
    3. environment variables (WHISPER_FLOW_*)
    4. CLI flags

The config is a plain dataclass so it serializes/deserializes cleanly and is
easy to inspect. No third-party deps: JSON only (stdlib).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional

from .errors import ConfigError

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_WHISPER_BIN = "whisper-cli"
DEFAULT_LLAMA_SERVER_BIN = "llama-server"
DEFAULT_LLAMA_CLI_BIN = "llama-cli"
DEFAULT_FFMPEG_BIN = "ffmpeg"
DEFAULT_ARECORD_BIN = "arecord"

# 16-bit 16 kHz mono WAV is what whisper.cpp prefers (see whisper.cpp README).
WHISPER_SAMPLE_RATE = 16000
WHISPER_CHANNELS = 1
WHISPER_AUDIO_CODEC = "pcm_s16le"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TranscriptionConfig:
    """whisper.cpp (STT) backend settings."""

    whisper_bin: str = DEFAULT_WHISPER_BIN
    model: str = ""  # path to ggml Whisper .bin (e.g. ggml-base.en.bin)
    language: str = "auto"  # "auto", "en", "fr", ... ; "auto" lets whisper detect
    translate: bool = False  # translate to English
    threads: int = 4
    # GPU/backend selection. whisper.cpp picks Metal automatically on Apple Silicon.
    # On CUDA build, set gpu="cuda" (no extra flag needed; build-time -DGGML_CUDA=1).
    # Use "cpu" to force CPU. Other values are passed through verbatim where supported.
    gpu: str = "auto"  # "auto" | "cpu" | "cuda" | "metal" | "vulkan"
    flash_attention: bool = True
    max_len: int = 0  # 0 = whisper.cpp default (~30s segments)
    # VAD: whisper-cli natively supports Silero VAD via --vad -vm <model>
    # (cli.cpp:1248-1256, added v1.8.5). When vad=True and vad_model is set,
    # whisper-full runs VAD first and skips silence. See RESEARCH.md Task 5.
    vad: bool = False
    vad_model: str = ""  # path to ggml-silero-v*.bin (download via download-vad-model.sh)
    vad_threshold: float = 0.5  # -vt; speech probability threshold 0..1
    vad_min_speech_ms: int = 0   # -vspd; 0 = whisper.cpp default
    vad_min_silence_ms: int = 0  # -vsd; 0 = whisper.cpp default
    vad_max_speech_s: int = 0    # -vmsd; 0 = whisper.cpp default (no cap)
    vad_speech_pad_ms: int = 0   # -vp; 0 = whisper.cpp default


@dataclass
class LLMConfig:
    """llama.cpp (LLM) backend settings."""

    mode: str = "server"  # "server" (HTTP to llama-server) | "cli" (subprocess llama-cli)
    llama_server_bin: str = DEFAULT_LLAMA_SERVER_BIN
    llama_cli_bin: str = DEFAULT_LLAMA_CLI_BIN
    model: str = ""  # path to GGUF LLM model
    mmproj: str = ""  # multimodal projector (unused for text-only LLM; reserved)
    host: str = "127.0.0.1"
    port: int = 8080
    # Generation
    temperature: float = 0.3
    max_tokens: int = 512
    top_p: float = 0.9
    # Extra context length to reserve for the prompt (helps avoid truncation).
    n_ctx: int = 2048
    threads: int = 4
    gpu_layers: int = 0  # 0 = CPU; set >0 (or -1 for all) for GPU offload on CUDA/Metal


@dataclass
class AudioConfig:
    """Audio capture / normalization / chunking."""

    ffmpeg_bin: str = DEFAULT_FFMPEG_BIN
    arecord_bin: str = DEFAULT_ARECORD_BIN
    sample_rate: int = WHISPER_SAMPLE_RATE
    channels: int = WHISPER_CHANNELS
    # Mic capture device. "default" uses the system default source.
    # On Linux: ALSA/PulseAudio "default". On macOS: avfoundation "default".
    # On Windows: a dshow device name string (use `whisper-flow list-devices`).
    mic_device: str = "default"
    mic_backend: str = "auto"  # "auto" | "arecord" | "ffmpeg" | "sounddevice"
    # Long-audio chunking. chunk_seconds <= 0 disables pre-chunking and lets
    # whisper.cpp handle long audio internally (it processes 30s windows).
    # Set e.g. 600 to split files longer than 10 min into 10-min chunks.
    chunk_seconds: int = 0
    # Streaming-mic mode (auto start/stop via VAD). When stream=True, mic capture
    # runs in a rolling buffer and periodically hands audio to whisper-cli with
    # VAD enabled; only new segments are emitted. stream_chunk_s is the poll
    # interval (seconds of audio per whisper-cli invocation). See RESEARCH.md §A4.
    stream: bool = False
    stream_chunk_s: int = 5   # how many seconds of audio per transcription pass
    stream_max_s: int = 30    # rolling buffer length (max audio kept)


@dataclass
class OutputConfig:
    """Output format / destination."""

    format: str = "text"  # "text" | "json" | "srt" | "vtt" | "all"
    write_files: bool = False  # write transcript files next to source
    out_dir: str = ""  # directory for written files (default: temp/next to source)


@dataclass
class Config:
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    # LLM post-processing mode for the `process` command.
    # "raw" = no LLM (transcription only). The CLI `transcribe` cmd always uses raw.
    mode: str = "summarize"  # "summarize" | "correct" | "polish" | "command" | "assistant" | "raw"
    verbose: bool = False


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

# Maps a dotted config key (e.g. "transcription.model") to its owning dataclass
# field. Used both for env vars and for CLI flag application.
_FLAT_FIELDS: dict[str, tuple[Any, str]] = {}


def _build_flat_index() -> None:
    for section_name in ("transcription", "llm", "audio", "output"):
        section: Any = getattr(Config(), section_name)
        for f in fields(section):
            _FLAT_FIELDS[f"{section_name}.{f.name}"] = (section_name, f.name)
    # top-level scalar fields on Config itself
    for f in fields(Config):
        if f.name in ("transcription", "llm", "audio", "output"):
            continue
        _FLAT_FIELDS[f.name] = (None, f.name)


_build_flat_index()


def _coerce(value: str, current: Any) -> Any:
    """Coerce a string (env var / CLI) to the type of an existing field value."""
    if isinstance(current, bool):
        return value.lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        try:
            return int(value)
        except ValueError:
            return current
    if isinstance(current, float):
        try:
            return float(value)
        except ValueError:
            return current
    return value


def _set_dotted(cfg: Config, dotted: str, value: Any) -> None:
    section_name, field_name = _FLAT_FIELDS.get(dotted, (None, ""))
    if section_name is None and field_name:
        # top-level field on Config
        setattr(cfg, field_name, _coerce(value, getattr(cfg, field_name)) if isinstance(value, str) else value)
        return
    if section_name is None:
        raise ConfigError(f"unknown config key: {dotted!r}")
    section = getattr(cfg, section_name)
    current = getattr(section, field_name)
    setattr(section, field_name, _coerce(value, current) if isinstance(value, str) else value)


def _load_toml_module():
    """Return a tomllib-compatible module, or raise ConfigError with a hint.

    stdlib tomllib on Python 3.11+; optional `tomli` backport on 3.8-3.10.
    """
    try:
        import tomllib  # Python 3.11+ (PEP 680)
        return tomllib
    except ModuleNotFoundError:
        pass
    try:
        import tomli as tomllib  # pip install tomli  (or whisper-flow[toml])
        return tomllib
    except ModuleNotFoundError as exc:
        raise ConfigError(
            "TOML config requires Python 3.11+ (built-in tomllib) or "
            "`pip install tomli` on Python 3.8-3.10 (or `pip install whisper-flow[toml]`)."
        ) from exc


def load_config_file(path: str) -> dict[str, Any]:
    """Load a JSON (.json) or TOML (.toml) config file into a dict."""
    if not path:
        return {}
    if not os.path.isfile(path):
        raise ConfigError(f"config file not found: {path!r}")
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".toml":
            tomllib = _load_toml_module()
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        elif ext in (".json", ""):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            raise ConfigError(
                f"unsupported config file extension {ext!r} (use .json or .toml)"
            )
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in config file {path!r}: {exc}") from exc
    except Exception as exc:
        # tomllib raises tomllib.TOMLDecodeError (or tomli.TOMLDecodeError);
        # catch broadly and wrap, since the exception type varies by source.
        if "TOMLDecode" in type(exc).__name__:
            raise ConfigError(f"invalid TOML in config file {path!r}: {exc}") from exc
        raise
    if not isinstance(data, dict):
        raise ConfigError(f"config file must be a table/object, got {type(data).__name__}")
    return data


def _apply_dict(cfg: Config, data: dict[str, Any]) -> None:
    for key, value in data.items():
        if isinstance(value, dict):
            # nested section, e.g. {"transcription": {"model": "..."}}
            section = getattr(cfg, key, None)
            if section is None:
                raise ConfigError(f"unknown config section: {key!r}")
            for sub_key, sub_val in value.items():
                _set_dotted(cfg, f"{key}.{sub_key}", sub_val)
        else:
            _set_dotted(cfg, key, value)


def _apply_env(cfg: Config) -> None:
    prefix = "WHISPER_FLOW_"
    for env_key, value in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        dotted = env_key[len(prefix):].lower().replace("__", ".")
        # only apply if it's a known key (silently ignore unknown env vars)
        if dotted in _FLAT_FIELDS:
            _set_dotted(cfg, dotted, value)


def load_config(
    config_path: Optional[str] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> Config:
    """Build a Config from defaults -> file -> env -> explicit overrides.

    `overrides` is a dict of dotted keys -> values, applied last (CLI layer uses this).
    """
    cfg = Config()
    # 1. file
    path = config_path or os.environ.get("WHISPER_FLOW_CONFIG", "")
    if path:
        _apply_dict(cfg, load_config_file(path))
    # 2. env
    _apply_env(cfg)
    # 3. explicit overrides (CLI)
    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            _set_dotted(cfg, key, value)
    return cfg


def config_to_dict(cfg: Config) -> dict[str, Any]:
    d = asdict(cfg)
    return d
