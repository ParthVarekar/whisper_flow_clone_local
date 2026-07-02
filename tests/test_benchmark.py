"""Tests for benchmark arithmetic and report generation."""
from __future__ import annotations

import json

from whisper_flow.benchmark import Benchmark, _peak_rss_mb


class TestBenchmark:
    def test_stage_timings(self):
        b = Benchmark()
        b.start("transcription")
        b.stop("transcription")
        b.start("llm")
        b.stop("llm")
        r = b.finish(audio_duration_sec=10.0, transcript_char_count=100,
                     segment_count=5, llm_char_count=50)
        assert r.stages["transcription"] >= 0.0
        assert r.stages["llm"] >= 0.0
        assert r.total_seconds > 0.0

    def test_realtime_factor(self):
        b = Benchmark()
        b.start("transcription")
        b.stop("transcription")
        # manually set the transcription time for a deterministic RTF
        b._stages["transcription"].seconds = 2.0
        r = b.finish(audio_duration_sec=10.0)
        assert r.realtime_factor == 5.0  # 10s audio / 2s transcribe = 5x realtime

    def test_realtime_factor_zero_transcription(self):
        b = Benchmark()
        r = b.finish(audio_duration_sec=10.0)
        assert r.realtime_factor == 0.0  # no transcription time -> 0

    def test_tokens_per_sec(self):
        b = Benchmark()
        b._stages["llm"] = type("S", (), {"seconds": 5.0, "started": 0.0, "running": False, "name": "llm"})()
        r = b.finish(llm_char_count=100)
        assert r.approx_tokens_per_sec == 20.0  # 100 chars / 5s = 20

    def test_json_report(self, tmp_path):
        b = Benchmark(audio_path="/tmp/a.wav", whisper_model="base.en", mode="raw")
        b.start("transcription"); b.stop("transcription")
        r = b.finish(audio_duration_sec=5.0, transcript_char_count=50, segment_count=2)
        jpath = str(tmp_path / "bench.json")
        b.write_json(jpath, r)
        with open(jpath) as f:
            data = json.load(f)
        assert data["audio_path"] == "/tmp/a.wav"
        assert data["whisper_model"] == "base.en"
        assert "transcription" in data["stages"]
        assert data["audio_duration_sec"] == 5.0

    def test_markdown_report(self, tmp_path):
        b = Benchmark(audio_path="/tmp/a.wav", whisper_model="base.en", mode="raw")
        b.start("transcription"); b.stop("transcription")
        r = b.finish(audio_duration_sec=5.0, transcript_char_count=50, segment_count=2)
        mpath = str(tmp_path / "bench.md")
        b.write_markdown(mpath, r)
        with open(mpath) as f:
            content = f.read()
        assert "# whisper-flow benchmark report" in content
        assert "Per-stage timings" in content
        assert "realtime factor" in content.lower()
        assert "peak RSS" in content

    def test_peak_rss_mb(self):
        # should return a non-negative number (0 on platforms without resource/psutil)
        rss = _peak_rss_mb()
        assert rss >= 0.0
