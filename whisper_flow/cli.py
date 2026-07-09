"""Command-line interface.

Subcommands:
  transcribe  - transcribe an audio file (STT only)
  mic         - record from microphone and transcribe (STT only)
  process     - transcribe + LLM post-processing (--mode none|light|medium|high|summarize|correct|polish|command|assistant|raw)
  serve       - run a minimal local HTTP server wrapping the pipeline
  check       - preflight: verify binaries + models are present

Examples:
  python -m whisper_flow transcribe -f audio.wav --language en
  python -m whisper_flow mic --duration 5
  python -m whisper_flow process -f audio.wav --mode summarize
  python -m whisper_flow process --mic --duration 8 --mode command
  python -m whisper_flow serve --port 8090
  python -m whisper_flow check
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from . import __version__
from .config import load_config
from .errors import WhisperFlowError, render_error
from .pipeline import Pipeline

# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------

def _add_common_transcription_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("-f", "--file", help="audio file to transcribe")
    p.add_argument("--mic", action="store_true", help="use microphone input instead of a file")
    p.add_argument("--duration", type=float, default=5.0,
                   help="mic recording duration in seconds (0 = Start/Stop live microphone session)")
    p.add_argument("--language", help="spoken language (e.g. en, fr, auto)")
    p.add_argument("--whisper-model", "--stt-model", help="path to ggml Whisper .bin model")
    p.add_argument("--whisper-bin", help="path to whisper-cli binary")
    p.add_argument("--translate", action="store_true", default=argparse.SUPPRESS, help="translate to English")
    p.add_argument("--threads", type=int, help="CPU threads for whisper.cpp")
    p.add_argument("--gpu", choices=["auto", "cpu", "cuda", "metal", "vulkan"],
                   help="GPU/backend hint (whisper.cpp: build-time; llama.cpp: -ngl)")
    p.add_argument("--chunk-seconds", type=int, help="split long audio into N-second chunks (0=off)")
    p.add_argument("--mic-device", help="mic device (Linux: ALSA/Pulse 'default'; macOS: avfoundation 'default'; Windows: dshow device name)")
    p.add_argument("--mic-backend", choices=["auto", "arecord", "ffmpeg", "sounddevice"], help="mic capture backend")
    # VAD (whisper-cli native Silero VAD, cli.cpp:1248-1256)
    p.add_argument("--vad", action="store_true", default=argparse.SUPPRESS, help="enable Silero VAD (skips silence; requires --vad-model)")
    p.add_argument("--vad-model", help="path to ggml-silero-v*.bin (run scripts/download_models.sh --vad)")
    p.add_argument("--vad-threshold", type=float, help="VAD speech probability threshold 0..1 (default 0.5)")
    p.add_argument("--vad-min-silence-ms", type=int, help="min silence to split segments (ms)")


def _add_common_llm_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mode", choices=["none", "light", "medium", "high", "summarize",
                                      "correct", "polish", "command", "assistant", "raw"],
                   help="LLM post-processing mode")
    p.add_argument("--llm-model", help="path to GGUF LLM model")
    p.add_argument("--llm-mode", choices=["server", "cli"], help="llama.cpp access mode")
    p.add_argument("--llm-host", help="llama-server host")
    p.add_argument("--llm-port", type=int, help="llama-server port")
    p.add_argument("--temperature", type=float, help="LLM sampling temperature")
    p.add_argument("--max-tokens", type=int, help="LLM max generated tokens")
    p.add_argument("--gpu-layers", type=int, help="llama.cpp GPU layers (-ngl); 0=CPU")
    p.add_argument("--writing-style", choices=["default", "casual", "very_casual", "formal"],
                   help="dictation writing style")


def _add_output_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--output-format", "--format", choices=["text", "json", "srt", "vtt", "all"],
                   help="output format")
    p.add_argument("--write-files", action="store_true", default=argparse.SUPPRESS,
                   help="write transcript files next to source / in --out-dir")
    p.add_argument("--out-dir", help="directory for written transcript files")
    p.add_argument("--json", action="store_true",
                   help="print full result as JSON to stdout (overrides --output-format for display)")


def _add_notifier_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--no-gui", action="store_true", default=argparse.SUPPRESS,
                   help="disable the live GUI progress window (console output only)")
    p.add_argument("--gui", action="store_true", default=argparse.SUPPRESS,
                   help="force the GUI progress window even if no display is detected")
    p.add_argument("--notify", action="store_true", default=argparse.SUPPRESS,
                   help="also fire desktop notifications (notify-send) on start/done/error")


def _build_notifier(args: argparse.Namespace, *, verbose: bool, initial_mode: str = "summarize"):
    from .notifier import make_notifier
    gui = not bool(getattr(args, "no_gui", False))
    if getattr(args, "gui", False):
        gui = True
    return make_notifier(gui=gui, notify=bool(getattr(args, "notify", False)),
                         title="whisper-flow", verbose=verbose, initial_mode=initial_mode)


def _overrides_from_args(args: argparse.Namespace) -> dict:
    """Map CLI args to dotted config overrides.

    C6 FIX: None values are skipped (attribute not set via argparse.SUPPRESS).
    False values for store_true flags are also skipped so that config-file
    True settings survive when the flag isn't explicitly passed on the CLI.
    """
    o: dict = {}
    _BOOL_FLAGS = frozenset({
        "transcription.translate", "transcription.vad",
        "output.write_files", "verbose",
    })
    def put(key, val):
        if val is None:
            return
        if key in _BOOL_FLAGS and val is False:
            return
        o[key] = val

    put("transcription.language", getattr(args, "language", None))
    put("transcription.model", getattr(args, "whisper_model", None))
    put("transcription.whisper_bin", getattr(args, "whisper_bin", None))
    put("transcription.translate", getattr(args, "translate", None))
    put("transcription.threads", getattr(args, "threads", None))
    put("transcription.gpu", getattr(args, "gpu", None))
    put("transcription.vad", getattr(args, "vad", None))
    put("transcription.vad_model", getattr(args, "vad_model", None))
    put("transcription.vad_threshold", getattr(args, "vad_threshold", None))
    put("transcription.vad_min_silence_ms", getattr(args, "vad_min_silence_ms", None))
    put("audio.chunk_seconds", getattr(args, "chunk_seconds", None))
    put("audio.mic_device", getattr(args, "mic_device", None))
    put("audio.mic_backend", getattr(args, "mic_backend", None))

    put("llm.mode", getattr(args, "llm_mode", None))
    put("llm.model", getattr(args, "llm_model", None))
    put("llm.host", getattr(args, "llm_host", None))
    put("llm.port", getattr(args, "llm_port", None))
    put("llm.temperature", getattr(args, "temperature", None))
    put("llm.max_tokens", getattr(args, "max_tokens", None))
    put("llm.gpu_layers", getattr(args, "gpu_layers", None))
    put("writing_style", getattr(args, "writing_style", None))

    put("output.format", getattr(args, "output_format", None))
    put("output.write_files", getattr(args, "write_files", None))
    put("output.out_dir", getattr(args, "out_dir", None))

    put("mode", getattr(args, "mode", None))
    put("verbose", getattr(args, "verbose", None))
    return o


def _resolve_input(args: argparse.Namespace) -> tuple[Optional[str], bool]:
    """Return (file_path, use_mic). Validates exactly one input source."""
    use_mic = bool(getattr(args, "mic", False))
    file_path = getattr(args, "file", None)
    if use_mic and file_path:
        raise WhisperFlowError("specify either --file or --mic, not both")
    if not use_mic and not file_path:
        raise WhisperFlowError("no input: provide --file FILE or --mic")
    return file_path, use_mic


def _print_result(result: dict, args: argparse.Namespace) -> None:
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return
    fmt = (getattr(args, "output_format", None) or "text").lower()
    if fmt == "json":
        sys.stdout.write(json.dumps(
            {"transcript": result["transcript"], "processed": result["processed"],
             "language": result["language"], "segments": result["segments"]},
            ensure_ascii=False, indent=2) + "\n")
        return
    if fmt == "srt":
        from .pipeline import segments_to_srt
        sys.stdout.write(segments_to_srt([
            _seg(d) for d in result["segments"]]) + "\n")
        return
    if fmt == "vtt":
        from .pipeline import segments_to_vtt
        sys.stdout.write(segments_to_vtt([
            _seg(d) for d in result["segments"]]) + "\n")
        return
    # default text
    if result.get("mode") and result["mode"] != "raw" and result.get("processed"):
        sys.stdout.write(result["processed"] + "\n")
    else:
        sys.stdout.write(result["transcript"] + "\n")


def _seg(d: dict):
    from .backends import Segment
    return Segment(text=d["text"], start_ms=d["start_ms"], end_ms=d["end_ms"], language=d.get("language", ""))


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _maybe_benchmark(args, cfg):
    """Return a Benchmark if --benchmark set, else None."""
    bdir = getattr(args, "benchmark", None)
    if not bdir:
        return None
    from .benchmark import Benchmark
    os.makedirs(bdir, exist_ok=True)
    return Benchmark(audio_path=getattr(args, "file", "") or "",
                     whisper_model=cfg.transcription.model,
                     llm_model=cfg.llm.model,
                     mode=cfg.mode, language=cfg.transcription.language)


def _finalize_benchmark(bench, result, args) -> None:
    if bench is None:
        return
    bdir = getattr(args, "benchmark", None)
    transcript = result.get("transcript", "") if isinstance(result, dict) else ""
    processed = result.get("processed", "") if isinstance(result, dict) else ""
    seg_count = len(result.get("segments", [])) if isinstance(result, dict) else 0
    audio_dur = 0.0
    # audio_dur was passed to bench.audio_info via the pipeline; recover from result if present
    r = bench.finish(audio_duration_sec=audio_dur,
                      transcript_char_count=len(transcript),
                      segment_count=seg_count,
                      llm_char_count=len(processed))
    jpath = os.path.join(bdir, "benchmark.json")
    mpath = os.path.join(bdir, "benchmark.md")
    bench.write_json(jpath, r)
    bench.write_markdown(mpath, r)
    sys.stderr.write(f"[benchmark] wrote {jpath} and {mpath}\n")


def _cmd_transcribe(args: argparse.Namespace) -> int:
    from .errors import CancelledError
    file_path, use_mic = _resolve_input(args)
    cfg = load_config(args.config, _overrides_from_args(args))
    cfg.mode = "raw"  # transcribe = STT only
    notifier = _build_notifier(args, verbose=cfg.verbose, initial_mode=cfg.mode)
    bench = _maybe_benchmark(args, cfg)
    pipe = Pipeline(cfg, notifier=notifier, benchmark=bench)

    def _work():
        if use_mic:
            return pipe.run_mic(args.duration)
        return pipe.run_file(file_path)

    try:
        result = notifier.run(_work)
    except CancelledError as exc:
        sys.stderr.write(f"[canceled] {exc}\n")
        return exc.exit_code
    _finalize_benchmark(bench, result, args)
    _print_result(result, args)
    return 0


def _cmd_process(args: argparse.Namespace) -> int:
    from .errors import CancelledError
    file_path, use_mic = _resolve_input(args)
    cfg = load_config(args.config, _overrides_from_args(args))
    if cfg.mode == "raw":
        cfg.mode = "summarize"  # sensible default for `process`
    notifier = _build_notifier(args, verbose=cfg.verbose, initial_mode=cfg.mode)
    bench = _maybe_benchmark(args, cfg)
    pipe = Pipeline(cfg, notifier=notifier, benchmark=bench)

    def _work():
        if use_mic:
            return pipe.run_mic(args.duration)
        return pipe.run_file(file_path)

    try:
        result = notifier.run(_work)
    except CancelledError as exc:
        sys.stderr.write(f"[canceled] {exc}\n")
        return exc.exit_code
    _finalize_benchmark(bench, result, args)
    _print_result(result, args)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    import shutil
    cfg = load_config(args.config, _overrides_from_args(args))
    print("whisper-flow preflight check")
    print("=" * 40)
    ok = True

    # binaries
    for label, name in [
        ("whisper-cli", cfg.transcription.whisper_bin),
        ("llama-server", cfg.llm.llama_server_bin),
        ("llama-cli", cfg.llm.llama_cli_bin),
        ("ffmpeg", cfg.audio.ffmpeg_bin),
        ("arecord", cfg.audio.arecord_bin),
    ]:
        found = shutil.which(name)
        status = "OK " if found else "MISSING"
        if not found and name in ("arecord",):  # arecord optional (ffmpeg fallback)
            print(f"  [{status}] {label:14s} {name}  (optional)")
        elif not found:
            print(f"  [{status}] {label:14s} {name}")
            ok = False
        else:
            print(f"  [{status}] {label:14s} {name}  -> {found}")

    # models
    for label, path, kind in [
        ("Whisper model", cfg.transcription.model, "ggml .bin"),
        ("LLM model", cfg.llm.model, "GGUF"),
    ]:
        if not path:
            print(f"  [UNSET ] {label:14s} (not configured)")
            ok = False
        elif not __import__("os").path.isfile(path):
            print(f"  [MISSING] {label:14s} {path}")
            ok = False
        else:
            print(f"  [OK ]    {label:14s} {path}")

    # llama-server reachable?
    if cfg.llm.mode == "server":
        try:
            from .backends.llama_cpp import LlamaCppBackend
            LlamaCppBackend(cfg.llm).check()
            print(f"  [OK ]    llama-server    reachable at http://{cfg.llm.host}:{cfg.llm.port}")
        except WhisperFlowError as exc:
            print(f"  [DOWN]   llama-server    {exc}")
            ok = False
    print("=" * 40)
    print("all good" if ok else "some checks failed (see above)")
    return 0 if ok else 1


def _cmd_daemon(args: argparse.Namespace) -> int:
    from .daemon import run_daemon
    cfg = load_config(args.config, _overrides_from_args(args))
    return run_daemon(cfg)


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server import run_server
    cfg = load_config(args.config, _overrides_from_args(args))
    return run_server(cfg, host=args.host, port=args.port, verbose=cfg.verbose)


def _cmd_models(args: argparse.Namespace) -> int:
    from .models import default_model_dirs, list_models, pick_interactive, render_table, download_model
    if getattr(args, "download", None):
        try:
            path = download_model(args.download)
            print(f"Downloaded model to: {path}")
            return 0
        except Exception as exc:
            print(f"Error downloading model: {exc}")
            return 1

    extra = args.model_dirs or []
    by_kind = list_models(extra)
    if args.select:
        kind = args.select  # "whisper" | "gguf" | "vad"
        models = by_kind.get(kind, [])
        if not models:
            print(f"no {kind} models found. Searched: {', '.join(default_model_dirs())}")
            return 1
        chosen = pick_interactive(models, f"Select a {kind} model:")
        if chosen is None:
            print("no selection.")
            return 1
        print(chosen.path)
        return 0
    print(render_table(by_kind))
    return 0


def _cmd_list_devices(args: argparse.Namespace) -> int:
    import sys as _sys

    from .config import AudioConfig
    cfg = load_config(args.config, _overrides_from_args(args)) if args.config else None
    audio_cfg = cfg.audio if cfg else AudioConfig()
    print(f"platform: {_sys.platform}")
    if _sys.platform == "win32":
        from .audio import list_devices_dshow
        devs = list_devices_dshow(audio_cfg)
        if not devs:
            print("no DirectShow audio devices found (or ffmpeg not installed).")
            return 1
        print("DirectShow audio input devices:")
        for i, d in enumerate(devs, 1):
            print(f"  [{i}] {d}")
        print("\nuse: --mic-device \"<name>\"")
    elif _sys.platform == "darwin":
        print("macOS AVFoundation uses ':default' for the default input device.")
        print("To list: ffmpeg -f avfoundation -list_devices true -i \"\"")
    else:
        print("Linux: list ALSA devices with `arecord -l`, PulseAudio with `pactl list sources short`.")
        print("Default device 'default' is usually correct on PulseAudio/PipeWire systems.")
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    """Run a file through the pipeline and write a benchmark report."""
    if not args.file:
        sys.stderr.write("bench requires -f FILE\n")
        return 2
    # merge args into a namespace that _cmd_process expects, then run process with --benchmark
    args.mic = False
    args.mode = args.mode or "raw"
    args.benchmark = args.out or "benchmarks"
    args.no_gui = True  # bench is non-interactive
    args.gui = False
    args.notify = False
    args.json = True  # emit result as JSON too
    args.output_format = "text"
    args.write_files = False
    args.out_dir = ""
    return _cmd_process(args)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="whisper-flow",
        description="Fully local speech-to-text + LLM pipeline (whisper.cpp + llama.cpp).",
    )
    p.add_argument("--config", help="path to JSON config file")
    p.add_argument("--version", action="version", version=f"whisper-flow {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="verbose logging to stderr")

    # Parent parser so --config / --verbose work both before AND after the subcommand.
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", default=argparse.SUPPRESS, help="path to JSON config file")
    parent.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="verbose logging to stderr"
    )

    sub = p.add_subparsers(dest="command", required=True)

    sp_t = sub.add_parser("transcribe", help="transcribe an audio file or mic (STT only)",
                          parents=[parent])
    _add_common_transcription_opts(sp_t)
    _add_output_opts(sp_t)
    _add_notifier_opts(sp_t)
    sp_t.add_argument("--benchmark", metavar="DIR",
                      help="write benchmark.json + benchmark.md to DIR")
    sp_t.set_defaults(func=_cmd_transcribe)

    sp_m = sub.add_parser("mic", help="record from microphone and transcribe (STT only)",
                          parents=[parent])
    _add_common_transcription_opts(sp_m)
    # --duration and --mic are already added by _add_common_transcription_opts;
    # force --mic on for this subcommand by presetting the default.
    sp_m.set_defaults(mic=True)
    _add_output_opts(sp_m)
    _add_notifier_opts(sp_m)
    sp_m.add_argument("--benchmark", metavar="DIR", help="write benchmark reports to DIR")
    sp_m.set_defaults(func=_cmd_transcribe)

    sp_p = sub.add_parser("process", help="transcribe + LLM post-processing",
                          parents=[parent])
    _add_common_transcription_opts(sp_p)
    _add_common_llm_opts(sp_p)
    _add_output_opts(sp_p)
    _add_notifier_opts(sp_p)
    sp_p.add_argument("--benchmark", metavar="DIR", help="write benchmark reports to DIR")
    sp_p.set_defaults(func=_cmd_process)

    sp_daemon = sub.add_parser("daemon", help="run background daemon",
                               parents=[parent])
    _add_common_transcription_opts(sp_daemon)
    _add_common_llm_opts(sp_daemon)
    sp_daemon.set_defaults(func=_cmd_daemon)

    sp_c = sub.add_parser("check", help="preflight: verify binaries + models",
                          parents=[parent])
    _add_common_transcription_opts(sp_c)
    _add_common_llm_opts(sp_c)
    sp_c.set_defaults(func=_cmd_check)

    sp_s = sub.add_parser("serve", help="run a minimal local HTTP server",
                          parents=[parent])
    sp_s.add_argument("--host", default="127.0.0.1")
    sp_s.add_argument("--port", type=int, default=8090)
    _add_common_transcription_opts(sp_s)
    _add_common_llm_opts(sp_s)
    sp_s.set_defaults(func=_cmd_serve)

    sp_models = sub.add_parser("models", help="discover and select models",
                               parents=[parent])
    sp_models.add_argument("--select", choices=["whisper", "gguf", "vad"],
                           help="interactive selection for the given kind")
    sp_models.add_argument("--download", help="download a model by name (e.g. small.en, medium.en, vad)")
    sp_models.add_argument("--model-dirs", nargs="*", help="extra dirs to scan")
    sp_models.set_defaults(func=_cmd_models)

    sp_dev = sub.add_parser("list-devices", help="list microphone input devices",
                            parents=[parent])
    sp_dev.set_defaults(func=_cmd_list_devices)

    sp_bench = sub.add_parser("bench", help="run a file through the pipeline + write benchmark",
                              parents=[parent])
    _add_common_transcription_opts(sp_bench)
    _add_common_llm_opts(sp_bench)
    sp_bench.add_argument("--out", default="benchmarks", help="output dir for reports")
    sp_bench.set_defaults(func=_cmd_bench)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from .errors import CancelledError
    try:
        return args.func(args)
    except CancelledError as exc:
        sys.stderr.write(render_error(exc) + "\n")
        return exc.exit_code
    except WhisperFlowError as exc:
        sys.stderr.write(render_error(exc) + "\n")
        return exc.exit_code
    except KeyboardInterrupt:
        sys.stderr.write("\n[interrupted]\n")
        return 130
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(render_error(exc) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
