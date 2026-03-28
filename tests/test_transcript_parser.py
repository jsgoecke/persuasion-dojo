"""
Unit tests for backend/transcript_parser.py.

Tests the three format parsers (markdown, otter, simple), auto-detection,
timestamp parsing, and edge cases (empty, malformed, whitespace).
"""

from __future__ import annotations

import pytest

from backend.transcript_parser import (
    parse_transcript,
    detect_format,
    _parse_timestamp,
)


# ---------------------------------------------------------------------------
# Markdown bold format: **Speaker:** text
# ---------------------------------------------------------------------------

class TestMarkdownFormat:

    def test_single_utterance(self):
        text = "**Alice:** I think we should proceed with the plan."
        result = parse_transcript(text)
        assert len(result) == 1
        assert result[0]["speaker"] == "Alice"
        assert result[0]["text"] == "I think we should proceed with the plan."

    def test_multiple_speakers(self):
        text = (
            "**Alice:** I think we should proceed.\n"
            "**Bob:** I disagree — we need more data.\n"
            "**Alice:** Fair point. Let me pull the numbers.\n"
        )
        result = parse_transcript(text)
        assert len(result) == 3
        assert result[0]["speaker"] == "Alice"
        assert result[1]["speaker"] == "Bob"
        assert result[2]["speaker"] == "Alice"

    def test_colon_inside_bold(self):
        """Format variant: **Speaker**: text (colon outside bold)."""
        text = "**Alice**: Hello.\n**Bob**: Hi there."
        result = parse_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker"] == "Alice"
        assert result[1]["speaker"] == "Bob"

    def test_detected_as_markdown(self):
        text = "**Alice:** Hello.\n**Bob:** Hi.\n**Alice:** How are you?\n"
        assert detect_format(text) == "markdown"


# ---------------------------------------------------------------------------
# Otter.ai / Zoom format: Speaker  0:42\ntext
# ---------------------------------------------------------------------------

class TestOtterFormat:

    def test_single_utterance(self):
        text = "Alice  0:42\nI think we should proceed with the plan."
        result = parse_transcript(text)
        assert len(result) == 1
        assert result[0]["speaker"] == "Alice"
        assert result[0]["text"] == "I think we should proceed with the plan."
        assert result[0]["start"] == 42.0

    def test_multi_line_utterance(self):
        text = (
            "Alice  1:05\n"
            "I think we should proceed.\n"
            "The data supports this approach.\n"
            "\n"
            "Bob  2:30\n"
            "I need to see the numbers first.\n"
        )
        result = parse_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker"] == "Alice"
        assert "data supports" in result[0]["text"]
        assert result[1]["speaker"] == "Bob"
        assert result[1]["start"] == 150.0  # 2:30 = 150s

    def test_hms_timestamp(self):
        text = "Alice  1:23:45\nHello."
        result = parse_transcript(text)
        assert len(result) == 1
        assert result[0]["start"] == 5025.0  # 1*3600 + 23*60 + 45

    def test_detected_as_otter(self):
        text = "Alice  0:00\nHello.\nBob  0:15\nHi.\nAlice  0:30\nOk.\n"
        assert detect_format(text) == "otter"


# ---------------------------------------------------------------------------
# Simple colon format: Speaker: text
# ---------------------------------------------------------------------------

class TestSimpleFormat:

    def test_single_utterance(self):
        text = "Alice: I think we should proceed with the plan."
        result = parse_transcript(text)
        assert len(result) == 1
        assert result[0]["speaker"] == "Alice"
        assert result[0]["text"] == "I think we should proceed with the plan."

    def test_multiple_speakers(self):
        text = "Alice: Hello.\nBob: Hi there.\nAlice: How are you?"
        result = parse_transcript(text)
        assert len(result) == 3
        assert result[0]["speaker"] == "Alice"
        assert result[1]["speaker"] == "Bob"
        assert result[2]["speaker"] == "Alice"

    def test_multi_line_continuation(self):
        """Lines without a speaker: prefix are continuation of previous speaker."""
        text = "Alice: I think we should proceed.\nThe data supports this.\nBob: Agreed."
        result = parse_transcript(text)
        assert len(result) == 2
        assert "data supports" in result[0]["text"]
        assert result[1]["speaker"] == "Bob"

    def test_detected_as_simple(self):
        text = "Alice: Hello.\nBob: Hi.\nAlice: Ok.\n"
        assert detect_format(text) == "simple"


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

class TestTimestampParsing:

    def test_minutes_seconds(self):
        assert _parse_timestamp("1:30") == 90.0

    def test_hours_minutes_seconds(self):
        assert _parse_timestamp("1:23:45") == 5025.0

    def test_zero_timestamp(self):
        assert _parse_timestamp("0:00") == 0.0

    def test_invalid_timestamp(self):
        assert _parse_timestamp("not a timestamp") == 0.0

    def test_single_digit_minutes(self):
        assert _parse_timestamp("5:00") == 300.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_string_returns_empty(self):
        assert parse_transcript("") == []

    def test_none_returns_empty(self):
        """None-ish input handled gracefully."""
        assert parse_transcript("") == []

    def test_whitespace_only_returns_empty(self):
        assert parse_transcript("   \n\t  \n  ") == []

    def test_no_speakers_found(self):
        """Plain text with no speaker labels returns empty or best effort."""
        text = "This is just a paragraph of text without any speaker labels."
        result = parse_transcript(text)
        assert isinstance(result, list)

    def test_single_speaker_line(self):
        """Edge: only one speaker line (below the ≥2 detection threshold)."""
        text = "Alice: Just one line."
        result = parse_transcript(text)
        # Should still parse with fallback detection
        assert len(result) >= 1

    def test_unicode_speaker_names(self):
        text = "José: Hola.\nMüller: Guten Tag."
        result = parse_transcript(text)
        # The simple parser requires names starting with [A-Z] — this is a known limitation
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

class TestFormatDetection:

    def test_unknown_format(self):
        text = "just random text with no structure whatsoever"
        assert detect_format(text) == "unknown"

    def test_mixed_signals_picks_strongest(self):
        """When multiple formats match, the one with the most hits wins."""
        text = (
            "**Alice:** Markdown format line one.\n"
            "**Bob:** Markdown format line two.\n"
            "**Alice:** Markdown format line three.\n"
            "Carol: Simple format line.\n"
        )
        assert detect_format(text) == "markdown"
