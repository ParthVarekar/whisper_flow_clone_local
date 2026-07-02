"""Integration tests for the pipeline with mock backends.

Exercises the full Pipeline.run_file / run_mic flow with fake STT + LLM
backends, verifying: notifier event ordering, benchmark integration, cancel
path, partial recovery, empty-transcript guard.
"""
from __future__ import annotations

import pytest

from whisper_flow.backends.base import (
    LLMBackend,
    Segment,
    TranscriptionBackend,
    TranscriptionResult,
)
from whisper_flow.benchmark import Benchmark
from whisper_flow.config import Config
from whisper_flow.errors import CancelledError, TranscriptionError
from whisper_flow.notifier import NullNotifier
from whisper_flow.pipeline import Pipeline


class RecordingNotifier(NullNotifier):
    """NullNotifier that records all events for assertion."""
    def __init__(self):
        super().__init__(verbose=False)
        self.events: list = []

    def stage(self, name, detail=""): self.events.append(("stage", name, detail))
    def progress(self, pct, detail=""): self.events.append(("progress", pct))
    def segment(self, text, ts=""): self.events.append(("segment", text, ts))
    def amplitude(self, rms): self.events.append(("amplitude", rms))
    def audio_info(self, dur, model): self.events.append(("audio_info", dur, model))
    def done(self, message=""): self.events.append(("done", message))
    def error(self, message): self.events.append(("error", message))


class MockSTT(TranscriptionBackend):
    name = "mock"
    def check(self): pass
    def transcribe(self, path, *, language="auto", on_progress=None, on_segment=None):
        if on_progress: on_progress(50, "")
        if on_segment: on_segment("hello world", "00:00:00.000 --> 00:00:02.000")
        if on_progress: on_progress(100, "")
        return TranscriptionResult(
            text="hello world",
            segments=[Segment("hello world", 0, 2000, "en")],
            language="en",
        )


class MockLLM(LLMBackend):
    name = "mock"
    def check(self): pass
    def process(self, prompt, *, system="", max_tokens=512, temperature=0.3):
        return "SUMMARY: " + prompt[:20]


class CancelSTT(TranscriptionBackend):
    name = "cancel"
    def check(self): pass
    def transcribe(self, path, *, language="auto", on_progress=None, on_segment=None):
        raise CancelledError("user cancelled")


class EmptySTT(TranscriptionBackend):
    name = "empty"
    def check(self): pass
    def transcribe(self, path, *, language="auto", on_progress=None, on_segment=None):
        return TranscriptionResult(text="", segments=[], language="")


@pytest.fixture
def patched_pipeline(tmp_wav_1s, monkeypatch):
    """Patch normalize_file/chunk_audio so we don't need ffmpeg."""
    import whisper_flow.pipeline as P
    monkeypatch.setattr(P, "normalize_file", lambda *a, **k: tmp_wav_1s)
    monkeypatch.setattr(P, "chunk_audio", lambda *a, **k: [tmp_wav_1s])
    return tmp_wav_1s


class TestRunFileHappy:
    def test_full_flow_emits_correct_events(self, patched_pipeline):
        cfg = Config(); cfg.mode = "summarize"; cfg.transcription.model = "/tmp/m.bin"
        n = RecordingNotifier()
        pipe = Pipeline(cfg, notifier=n)
        pipe._stt = MockSTT(); pipe._llm = MockLLM()
        result = pipe.run_file(patched_pipeline)
        # event ordering
        assert n.events[0][0] == "stage" and n.events[0][1] == "Normalizing audio"
        assert ("progress", 50) in n.events
        assert ("progress", 100) in n.events
        assert ("segment", "hello world", "00:00:00.000 --> 00:00:02.000") in n.events
        assert ("stage", "LLM processing", "summarize") in n.events
        assert n.events[-1] == ("done", "transcription complete")
        # result contents
        assert result["transcript"] == "hello world"
        assert result["processed"].startswith("SUMMARY:")
        assert result["mode"] == "summarize"
        # happy path does not set canceled/partial keys (only cancel path does)

    def test_audio_info_fires(self, patched_pipeline):
        cfg = Config(); cfg.mode = "raw"; cfg.transcription.model = "/tmp/m.bin"
        n = RecordingNotifier()
        pipe = Pipeline(cfg, notifier=n)
        pipe._stt = MockSTT()
        pipe.run_file(patched_pipeline)
        info_events = [e for e in n.events if e[0] == "audio_info"]
        assert len(info_events) == 1
        assert info_events[0][1] == pytest.approx(1.0, abs=0.01)
        assert info_events[0][2] == "m.bin"


class TestBenchmarkIntegration:
    def test_benchmark_stages_recorded(self, patched_pipeline):
        cfg = Config(); cfg.mode = "summarize"
        cfg.transcription.model = "/tmp/m.bin"; cfg.llm.model = "/tmp/l.gguf"
        bench = Benchmark(whisper_model="m.bin", llm_model="l.gguf", mode="summarize")
        n = RecordingNotifier()
        pipe = Pipeline(cfg, notifier=n, benchmark=bench)
        pipe._stt = MockSTT(); pipe._llm = MockLLM()
        pipe.run_file(patched_pipeline)
        r = bench.finish(audio_duration_sec=1.0, transcript_char_count=11,
                         segment_count=1, llm_char_count=30)
        assert "preprocess" in r.stages
        assert "transcription" in r.stages
        assert "llm" in r.stages


class TestCancelPath:
    def test_cancel_returns_partial_result(self, patched_pipeline):
        cfg = Config(); cfg.mode = "summarize"; cfg.transcription.model = "/tmp/m.bin"
        n = RecordingNotifier()
        pipe = Pipeline(cfg, notifier=n)
        pipe._stt = CancelSTT(); pipe._llm = MockLLM()
        result = pipe.run_file(patched_pipeline)
        assert result["canceled"] is True
        assert result["partial"] is True
        assert ("done", "canceled") in n.events
        # should NOT have an error event
        assert not any(e[0] == "error" for e in n.events)


class TestEmptyTranscript:
    def test_empty_transcript_raises(self, patched_pipeline):
        # The EmptySTT returns no segments; whisper_cpp backend would raise
        # TranscriptionError on empty output. Here we simulate via a mock that
        # mimics the backend's empty-guard by raising directly.
        cfg = Config(); cfg.mode = "raw"; cfg.transcription.model = "/tmp/m.bin"
        n = RecordingNotifier()
        pipe = Pipeline(cfg, notifier=n)
        # Replace MockSTT.transcribe to raise empty-transcript error like the real backend
        class EmptyRaisingSTT(MockSTT):
            def transcribe(self, *a, **k):
                raise TranscriptionError("empty transcript — audio may be silent")
        pipe._stt = EmptyRaisingSTT()
        with pytest.raises(TranscriptionError, match="empty transcript"):
            pipe.run_file(patched_pipeline)
        assert any(e[0] == "error" for e in n.events)


class TestCancelRegistration:
    def test_cancel_registered_with_notifier(self, patched_pipeline):
        cfg = Config(); cfg.transcription.model = "/tmp/m.bin"
        n = RecordingNotifier()
        pipe = Pipeline(cfg, notifier=n)
        # register_cancel is called in __init__
        assert n._cancel_cb is not None
        # calling it should not raise even with no backend yet
        n._cancel_cb()
