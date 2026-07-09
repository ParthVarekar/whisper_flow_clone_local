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

    backend: str = "whisper_cpp"  # "whisper_cpp" | "qwen3_asr"
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
    # Qwen3-ASR: multimodal speech-LLM via CrispASR / llama.cpp
    qwen3_asr_bin: str = ""      # path to crispasr(.exe) / llama-mtmd-cli
    qwen3_asr_model: str = ""    # path to Qwen3-ASR-*.gguf
    qwen3_asr_mmproj: str = ""   # path to mmproj-Qwen3-ASR-*.gguf (optional for CrispASR)


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
    writing_style: str = "default"  # "default" | "casual" | "very_casual" | "formal"
    smart_formatting: bool = True
    # LLM post-processing mode for the `process` command.
    # "raw"/"none" = no LLM (transcription only). The CLI `transcribe` cmd always uses raw.
    mode: str = "summarize"  # "none"|"light"|"medium"|"high"|"summarize"|"correct"|"polish"|"command"|"assistant"|"raw"
    verbose: bool = False
    dictation_hotkey: str = "ctrl+shift+space"
    command_hotkey: str = "ctrl+shift+t"
    snippets: dict[str, str] = field(default_factory=dict)
    dictionary: list[str] = field(default_factory=list)
    app_styles: dict[str, dict[str, str]] = field(default_factory=dict)
    custom_transforms: dict[str, dict[str, str]] = field(default_factory=dict)


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
        if key in ("snippets", "app_styles", "custom_transforms") and isinstance(value, dict):
            setattr(cfg, key, value)
        elif key == "dictionary" and isinstance(value, list):
            setattr(cfg, key, value)
        elif isinstance(value, dict):
            # nested section, e.g. {"transcription": {"model": "..."}}
            section = getattr(cfg, key, None)
            if section is None or isinstance(section, dict):
                setattr(cfg, key, value)
            else:
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


def save_config(cfg: Config, path: str) -> None:
    """Save Config to a TOML or JSON file."""
    if not path:
        return
    ext = os.path.splitext(path)[1].lower()
    data = config_to_dict(cfg)
    if ext == ".json":
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    else:
        # Minimal TOML serialization for standard config
        lines = []
        # Top-level scalars
        for k, v in data.items():
            if not isinstance(v, (dict, list)):
                if isinstance(v, str):
                    lines.append(f'{k} = "{v}"')
                elif isinstance(v, bool):
                    lines.append(f'{k} = {str(v).lower()}')
                else:
                    lines.append(f'{k} = {v}')
        lines.append("")
        # Tables
        for k, v in data.items():
            if isinstance(v, dict):
                # First write flat keys of table k
                has_scalars = any(not isinstance(sub_v, dict) for sub_v in v.values())
                if has_scalars or not v:
                    lines.append(f"[{k}]")
                    for sub_k, sub_v in v.items():
                        if not isinstance(sub_v, dict):
                            if isinstance(sub_v, str):
                                lines.append(f'{sub_k} = "{sub_v}"')
                            elif isinstance(sub_v, bool):
                                lines.append(f'{sub_k} = {str(sub_v).lower()}')
                            else:
                                lines.append(f'{sub_k} = {sub_v}')
                    lines.append("")
                # Next write sub-tables
                for sub_k, sub_v in v.items():
                    if isinstance(sub_v, dict):
                        lines.append(f"[{k}.{sub_k}]")
                        for ssk, ssv in sub_v.items():
                            if isinstance(ssv, str):
                                lines.append(f'{ssk} = "{ssv}"')
                            elif isinstance(ssv, bool):
                                lines.append(f'{ssk} = {str(ssv).lower()}')
                            else:
                                lines.append(f'{ssk} = {ssv}')
                        lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

