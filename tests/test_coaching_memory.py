"""
Tests for coaching_memory.py — self-evolving coaching playbook.

Covers:
  - read_playbook: initial template vs persisted file
  - get_coaching_context: empty template returns "", filled playbook returns sections
  - _extract_section, _extract_subsection, _extract_lines_mentioning: markdown parsing
  - _format_session_evidence: session data formatting
  - update_playbook: Opus call with mock (write verification)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.coaching_memory import (
    INITIAL_PLAYBOOK,
    _extract_lines_mentioning,
    _extract_section,
    _extract_subsection,
    _format_session_evidence,
    _playbook_path,
    get_coaching_context,
    read_playbook,
    update_playbook,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FILLED_PLAYBOOK = """\
# Coaching Playbook

## Effective Patterns
- Anchoring in numbers works well with Architects (3 sessions)
- Asking a question after 3 minutes of advocacy re-engages the group
- Using ego safety language reduces ego threat events

## Ineffective Patterns
- Leading with vision when counterpart is in shortcut mode
- Talking past the 5-minute mark without inviting contribution

## Pairing Notes

### Architect
- They respond best to data-first openings
- Slow down your delivery pace

### Firestarter
- Match their energy before redirecting

## Session Trends
- Average persuasion score: 68/100 (5 sessions)
- Talk time sweet spot: 40-50%
- Ego threat events trending down: 3 → 1 over last 3 sessions
"""


@pytest.fixture
def tmp_playbook_dir(tmp_path: Path, monkeypatch):
    """Point _DATA_DIR to a temp directory for test isolation."""
    monkeypatch.setattr("backend.coaching_memory._DATA_DIR", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# read_playbook
# ---------------------------------------------------------------------------


class TestReadPlaybook:
    def test_returns_initial_template_when_no_file(self, tmp_playbook_dir):
        result = read_playbook("user-1")
        assert result == INITIAL_PLAYBOOK

    def test_returns_file_content_when_exists(self, tmp_playbook_dir):
        path = tmp_playbook_dir / "user-1.md"
        path.write_text(FILLED_PLAYBOOK, encoding="utf-8")
        result = read_playbook("user-1")
        assert result == FILLED_PLAYBOOK

    def test_different_users_have_separate_playbooks(self, tmp_playbook_dir):
        (tmp_playbook_dir / "alice.md").write_text("# Alice's playbook")
        (tmp_playbook_dir / "bob.md").write_text("# Bob's playbook")
        assert "Alice" in read_playbook("alice")
        assert "Bob" in read_playbook("bob")


# ---------------------------------------------------------------------------
# get_coaching_context
# ---------------------------------------------------------------------------


class TestGetCoachingContext:
    def test_returns_empty_for_initial_template(self, tmp_playbook_dir):
        assert get_coaching_context("user-1") == ""

    def test_returns_effective_patterns(self, tmp_playbook_dir):
        (tmp_playbook_dir / "user-1.md").write_text(FILLED_PLAYBOOK)
        ctx = get_coaching_context("user-1")
        assert "Effective Patterns" in ctx
        assert "Anchoring in numbers" in ctx

    def test_returns_ineffective_patterns(self, tmp_playbook_dir):
        (tmp_playbook_dir / "user-1.md").write_text(FILLED_PLAYBOOK)
        ctx = get_coaching_context("user-1")
        assert "Ineffective Patterns" in ctx

    def test_includes_pairing_notes_for_matching_archetype(self, tmp_playbook_dir):
        (tmp_playbook_dir / "user-1.md").write_text(FILLED_PLAYBOOK)
        ctx = get_coaching_context("user-1", counterpart_archetype="Architect")
        assert "data-first openings" in ctx

    def test_excludes_non_matching_pairing_notes(self, tmp_playbook_dir):
        (tmp_playbook_dir / "user-1.md").write_text(FILLED_PLAYBOOK)
        ctx = get_coaching_context("user-1", counterpart_archetype="Inquisitor")
        # Inquisitor has no subsection in our test playbook
        assert "data-first openings" not in ctx

    def test_includes_elm_state_mentions(self, tmp_playbook_dir):
        (tmp_playbook_dir / "user-1.md").write_text(FILLED_PLAYBOOK)
        ctx = get_coaching_context("user-1", elm_state="ego_threat")
        assert "ego threat" in ctx.lower() or "ego safety" in ctx.lower()

    def test_caps_at_500_words(self, tmp_playbook_dir):
        long_playbook = "# Coaching Playbook\n\n## Effective Patterns\n"
        long_playbook += "\n".join(f"- Pattern {i} is very effective" for i in range(200))
        long_playbook += "\n\n## Ineffective Patterns\n- nothing\n"
        (tmp_playbook_dir / "user-1.md").write_text(long_playbook)
        ctx = get_coaching_context("user-1")
        word_count = len(ctx.split())
        assert word_count <= 510  # 500 + header words + ellipsis tolerance

    def test_prefixed_with_playbook_header(self, tmp_playbook_dir):
        (tmp_playbook_dir / "user-1.md").write_text(FILLED_PLAYBOOK)
        ctx = get_coaching_context("user-1")
        assert ctx.startswith("YOUR COACHING PLAYBOOK")


# ---------------------------------------------------------------------------
# _extract_section
# ---------------------------------------------------------------------------


class TestExtractSection:
    def test_extracts_matching_section(self):
        section = _extract_section(FILLED_PLAYBOOK, "## Effective Patterns")
        assert "Anchoring in numbers" in section
        assert "Ineffective" not in section

    def test_returns_empty_for_missing_section(self):
        assert _extract_section(FILLED_PLAYBOOK, "## Nonexistent") == ""

    def test_stops_at_next_h2(self):
        section = _extract_section(FILLED_PLAYBOOK, "## Ineffective Patterns")
        assert "Pairing Notes" not in section


# ---------------------------------------------------------------------------
# _extract_subsection
# ---------------------------------------------------------------------------


class TestExtractSubsection:
    def test_extracts_matching_subsection(self):
        sub = _extract_subsection(FILLED_PLAYBOOK, "## Pairing Notes", "Architect")
        assert "data-first" in sub

    def test_returns_empty_for_missing_keyword(self):
        sub = _extract_subsection(FILLED_PLAYBOOK, "## Pairing Notes", "Inquisitor")
        assert sub == ""

    def test_case_insensitive_match(self):
        sub = _extract_subsection(FILLED_PLAYBOOK, "## Pairing Notes", "architect")
        assert "data-first" in sub

    def test_stops_at_next_h3(self):
        sub = _extract_subsection(FILLED_PLAYBOOK, "## Pairing Notes", "Architect")
        assert "Match their energy" not in sub  # That's in the Firestarter subsection


# ---------------------------------------------------------------------------
# _extract_lines_mentioning
# ---------------------------------------------------------------------------


class TestExtractLinesMentioning:
    def test_finds_matching_lines(self):
        lines = _extract_lines_mentioning(FILLED_PLAYBOOK, "ego threat")
        assert "ego threat" in lines.lower()

    def test_case_insensitive(self):
        lines = _extract_lines_mentioning(FILLED_PLAYBOOK, "EGO THREAT")
        assert len(lines) > 0

    def test_excludes_headings(self):
        lines = _extract_lines_mentioning(FILLED_PLAYBOOK, "Coaching Playbook")
        # The heading line starts with # and should be excluded
        assert lines == ""

    def test_caps_at_5_lines(self):
        text = "\n".join(f"line {i} has keyword" for i in range(10))
        lines = _extract_lines_mentioning(text, "keyword")
        assert len(lines.split("\n")) <= 5


# ---------------------------------------------------------------------------
# _format_session_evidence
# ---------------------------------------------------------------------------


class TestFormatSessionEvidence:
    def test_basic_formatting(self):
        summary = {
            "context": "board",
            "persuasion_score": 72,
            "timing_score": 20,
            "ego_safety_score": 25,
            "convergence_score": 27,
            "ego_threat_events": 2,
            "talk_time_ratio": 0.45,
            "total_utterances": 30,
            "prompt_results": [],
        }
        result = _format_session_evidence("Firestarter", summary)
        assert "Firestarter" in result
        assert "72/100" in result
        assert "board" in result
        assert "0.45" in result

    def test_includes_prompt_results(self):
        summary = {
            "context": "sales",
            "persuasion_score": 65,
            "timing_score": 18,
            "ego_safety_score": 22,
            "convergence_score": 25,
            "ego_threat_events": 1,
            "talk_time_ratio": 0.5,
            "total_utterances": 20,
            "prompt_results": [
                {
                    "triggered_by": "elm:ego_threat",
                    "counterpart_archetype": "Architect",
                    "text": "Anchor your next point in a number",
                    "effectiveness_score": 0.75,
                    "convergence_before": 0.4,
                    "convergence_after": 0.65,
                }
            ],
        }
        result = _format_session_evidence("Firestarter", summary)
        assert "ego_threat" in result
        assert "Architect" in result
        assert "0.75" in result
        assert "0.40 → 0.65" in result

    def test_no_prompt_results(self):
        summary = {"prompt_results": []}
        result = _format_session_evidence("Unknown", summary)
        assert "no prompts with effectiveness data" in result


# ---------------------------------------------------------------------------
# update_playbook
# ---------------------------------------------------------------------------


class TestUpdatePlaybook:
    def test_skips_when_no_api_key(self, tmp_playbook_dir, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        asyncio.run(
            update_playbook("user-1", "Firestarter", {}, api_key="")
        )
        # No file written
        assert not (tmp_playbook_dir / "user-1.md").exists()

    @patch("anthropic.AsyncAnthropic")
    def test_writes_updated_playbook(self, MockAnthropic, tmp_playbook_dir):
        # Mock the API response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="# Updated Coaching Playbook\n\n## Effective Patterns\n- Anchoring in numbers works well with Architects (3 sessions confirmed)\n- Asking questions after extended advocacy re-engages the group effectively\n\n## Ineffective Patterns\n- Leading with vision when counterpart is defensive\n\n## Session Trends\n- Average score: 75/100")]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        MockAnthropic.return_value = mock_client

        summary = {
            "persuasion_score": 75,
            "timing_score": 22,
            "ego_safety_score": 25,
            "convergence_score": 28,
            "ego_threat_events": 1,
            "talk_time_ratio": 0.45,
            "total_utterances": 25,
            "context": "board",
            "prompt_results": [],
        }

        asyncio.run(
            update_playbook("user-1", "Architect", summary, api_key="test-key")
        )

        path = tmp_playbook_dir / "user-1.md"
        assert path.exists()
        content = path.read_text()
        assert "Updated Coaching Playbook" in content

    @patch("anthropic.AsyncAnthropic")
    def test_rejects_bad_format(self, MockAnthropic, tmp_playbook_dir):
        # Mock response that doesn't start with # — should be rejected
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="This is not a valid playbook")]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        MockAnthropic.return_value = mock_client

        asyncio.run(
            update_playbook("user-1", "Architect", {}, api_key="test-key")
        )

        # File should NOT have been written
        assert not (tmp_playbook_dir / "user-1.md").exists()

    @patch("anthropic.AsyncAnthropic")
    def test_handles_api_failure_gracefully(self, MockAnthropic, tmp_playbook_dir):
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
        MockAnthropic.return_value = mock_client

        # Should not raise
        asyncio.run(
            update_playbook("user-1", "Architect", {}, api_key="test-key")
        )
        assert not (tmp_playbook_dir / "user-1.md").exists()
