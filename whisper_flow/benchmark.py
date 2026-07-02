"""Benchmarking: per-stage timing + derived metrics + JSON/Markdown reports.

Measures separately:
  - audio_load      (file read / mic capture)
  - preprocess      (ffmpeg normalize)
  - transcription   (whisper-cli)
  - llm             (llama-server)
  - total           (end-to-end)

Derived:
  - realtime_factor = audio_duration_sec / transcription_sec   (>1 = faster than realtime)
  - tokens_per_sec  = approx generated chars / llm_sec         (char-based approx; whisper.cpp
                  doesn't expose token counts via subprocess, so we use chars as a proxy
                  and label it clearly in reports)
  - peak_rss_mb     = peak resident set size (POSIX rusage; Windows fallback via psutil if present)

Usage from the pipeline:
    bench = Benchmark()
    bench.start('audio_load')
    ... load audio ...
    bench.stop('audio_load')
    ... etc ...
    bench.finish(audio_duration_sec=..., transcript_char_count=..., llm_char_count=...)
    bench.write_json('bench.json')
    bench.write_markdown('bench.md')

Or via the CLI: `whisper-flow bench -f audio.wav --benchmark-dir reports/`
"""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class StageTiming:
    name: str
    seconds: float = 0.0
    started: float = 0.0
    running: bool = False


@dataclass
class BenchmarkResult:
    """Serializable benchmark result."""
    # environment
    python: str = ""
    platform: str = ""
    # per-stage seconds
    stages: dict[str, float] = field(default_factory=dict)
    # totals
    total_seconds: float = 0.0
    # inputs
    audio_duration_sec: float = 0.0
    audio_path: str = ""
    whisper_model: str = ""
    llm_model: str = ""
    mode: str = ""
    language: str = ""
    # outputs
    transcript_char_count: int = 0
    segment_count: int = 0
    llm_char_count: int = 0
    # derived
    realtime_factor: float = 0.0      # audio_duration / transcription_time
    approx_tokens_per_sec: float = 0.0  # llm_char_count / llm_time (char-proxy)
    peak_rss_mb: float = 0.0
    # metadata
    notes: list[str] = field(default_factory=list)


def _peak_rss_mb() -> float:
    """Peak RSS in MiB. POSIX via resource.getrusage; Windows best-effort via psutil."""
    try:
        import resource
        # ru_maxrss is in kilobytes on Linux, bytes on macOS
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == "darwin":
            return usage.ru_maxrss / (1024 * 1024)
        return usage.ru_maxrss / 1024
    except (ImportError, AttributeError):
        pass
    try:
        import psutil  # optional
        proc = psutil.Process()
        return proc.memory_info().peak_wset / (1024 * 1024)
    except (ImportError, Exception):  # noqa: BLE001
        return 0.0


class Benchmark:
    """Accumulator for per-stage timings.

    Thread-safety: methods are intended to be called from the main pipeline
    thread; concurrent starts of the same stage are not supported (would overwrite
    `started`). The pipeline calls stages sequentially.
    """

    def __init__(self, *, audio_path: str = "", whisper_model: str = "",
                 llm_model: str = "", mode: str = "", language: str = ""):
        self._stages: dict[str, StageTiming] = {}
        self._result = BenchmarkResult(
            python=sys.version.split()[0],
            platform=platform.platform(),
            audio_path=audio_path,
            whisper_model=whisper_model,
            llm_model=llm_model,
            mode=mode,
            language=language,
        )
        self._t0 = time.perf_counter()

    def start(self, name: str) -> None:
        st = self._stages.get(name)
        if st is None:
            st = StageTiming(name=name)
            self._stages[name] = st
        st.started = time.perf_counter()
        st.running = True

    def stop(self, name: str) -> None:
        st = self._stages.get(name)
        if st is None or not st.running:
            return
        st.seconds += time.perf_counter() - st.started
        st.running = False

    def note(self, msg: str) -> None:
        self._result.notes.append(msg)

    def finish(self, *, audio_duration_sec: float = 0.0,
               transcript_char_count: int = 0, segment_count: int = 0,
               llm_char_count: int = 0) -> BenchmarkResult:
        r = self._result
        r.total_seconds = time.perf_counter() - self._t0
        r.stages = {name: round(st.seconds, 4) for name, st in self._stages.items()}
        r.audio_duration_sec = audio_duration_sec
        r.transcript_char_count = transcript_char_count
        r.segment_count = segment_count
        r.llm_char_count = llm_char_count

        t_stt = r.stages.get("transcription", 0.0)
        if audio_duration_sec > 0 and t_stt > 0:
            r.realtime_factor = round(audio_duration_sec / t_stt, 3)
        t_llm = r.stages.get("llm", 0.0)
        if llm_char_count > 0 and t_llm > 0:
            r.approx_tokens_per_sec = round(llm_char_count / t_llm, 1)
        r.peak_rss_mb = round(_peak_rss_mb(), 2)
        return r

    # -- output --------------------------------------------------------------

    def write_json(self, path: str, result: Optional[BenchmarkResult] = None) -> None:
        r = result or self._result
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(r), fh, ensure_ascii=False, indent=2)

    def write_markdown(self, path: str, result: Optional[BenchmarkResult] = None) -> None:
        r = result or self._result
        lines = [
            "# whisper-flow benchmark report",
            "",
            f"- **date**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **python**: {r.python}",
            f"- **platform**: {r.platform}",
            f"- **audio**: `{r.audio_path}` ({r.audio_duration_sec:.2f} s)" if r.audio_duration_sec else f"- **audio**: `{r.audio_path}`",
            f"- **whisper model**: `{r.whisper_model}`" if r.whisper_model else "- **whisper model**: _(none)_",
            f"- **llm model**: `{r.llm_model}`" if r.llm_model else "- **llm model**: _(none)_",
            f"- **mode**: {r.mode or '_raw_'}",
            f"- **language**: {r.language or '_auto_'}",
            "",
            "## Per-stage timings",
            "",
            "| stage | seconds |",
            "|---|---|",
        ]
        for name in ("audio_load", "preprocess", "transcription", "llm"):
            if name in r.stages:
                lines.append(f"| {name} | {r.stages[name]:.4f} |")
        lines.append(f"| **total** | **{r.total_seconds:.4f}** |")
        lines += [
            "",
            "## Derived metrics",
            "",
            f"- **realtime factor**: {r.realtime_factor}×  (>1 = faster than realtime)"
            if r.realtime_factor else "- **realtime factor**: n/a (no transcription time)",
            f"- **approx tokens/sec** (char-proxy, LLM stage): {r.approx_tokens_per_sec}"
            if r.approx_tokens_per_sec else "- **approx tokens/sec**: n/a",
            f"- **peak RSS**: {r.peak_rss_mb:.1f} MB",
            f"- **transcript length**: {r.transcript_char_count} chars ({r.segment_count} segments)",
            f"- **llm output length**: {r.llm_char_count} chars",
            "",
        ]
        if r.notes:
            lines += ["## Notes", ""]
            for n in r.notes:
                lines.append(f"- {n}")
            lines.append("")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
