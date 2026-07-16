"""Tests for whisper.cpp output line parsers (progress + segments).

Regression tests for the exact format strings confirmed from upstream cli.cpp
(see ARCHITECTURE.md). If whisper.cpp changes its output format, these
tests break and the parsers must be updated.
"""
from __future__ import annotations

from whisper_flow.backends.whisper_cpp import parse_progress_line, parse_segment_line


class TestProgressParser:
    def test_single_digit_percent(self):
        # %3d right-justifies: "  5%" (two leading spaces)
        assert parse_progress_line("whisper_print_progress_callback: progress =   5%") == 5

    def test_double_digit_percent(self):
        assert parse_progress_line("whisper_print_progress_callback: progress =  10%") == 10
        assert parse_progress_line("whisper_print_progress_callback: progress =  45%") == 45

    def test_triple_digit_percent(self):
        assert parse_progress_line("whisper_print_progress_callback: progress = 100%") == 100

    def test_non_progress_line(self):
        assert parse_progress_line("system_info: n_threads = 4") is None
        assert parse_progress_line("whisper_init_state: kv self size = 512 MB") is None
        assert parse_progress_line("some random line") is None
        assert parse_progress_line("") is None

    def test_trailing_carriage_return(self):
        assert parse_progress_line("whisper_print_progress_callback: progress =  50%\r\n") == 50


class TestSegmentParser:
    def test_basic_segment(self):
        r = parse_segment_line("[00:00:00.000 --> 00:00:05.234]  Hello world.")
        assert r == ("00:00:00.000", "00:00:05.234", "Hello world.")

    def test_long_text(self):
        r = parse_segment_line(
            "[00:00:05.234 --> 00:00:11.000]  And so, my fellow Americans, ask not what your country can do for you."
        )
        assert r is not None
        assert r[0] == "00:00:05.234"
        assert r[1] == "00:00:11.000"
        assert "fellow Americans" in r[2]

    def test_non_segment_line(self):
        assert parse_segment_line("system_info: ...") is None
        assert parse_segment_line("not a segment") is None
        assert parse_segment_line("") is None

    def test_trailing_carriage_return(self):
        r = parse_segment_line("[00:00:00.000 --> 00:00:01.000]  hi\r\n")
        assert r is not None
        assert r[2] == "hi"
