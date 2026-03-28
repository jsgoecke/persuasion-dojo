"""
Unit tests for backend/pre_seeding.py — no live API calls.

Tests the classify() function with a mocked Anthropic client, covering:
- Active classification for each Superpower type
- Pending state for short/vague input
- Empty input raises ValueError
- JSON parsing and validation
- Confidence range enforcement
- Code-fence stripping
- State consistency rules (null type ↔ pending)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from backend.pre_seeding import classify, PreSeedResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client(response_json: dict) -> MagicMock:
    """Build a sync Anthropic client that returns a JSON string."""
    content = MagicMock()
    content.text = json.dumps(response_json)
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create = MagicMock(return_value=response)
    return client


def _mock_client_raw(raw_text: str) -> MagicMock:
    """Build a sync client that returns raw text (for testing JSON parse errors)."""
    content = MagicMock()
    content.text = raw_text
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create = MagicMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# Active classification — happy paths
# ---------------------------------------------------------------------------

class TestActiveClassification:
    """Tests where the model returns a valid classification."""

    def test_architect_classification(self):
        client = _mock_client({
            "type": "Architect",
            "confidence": 0.85,
            "state": "active",
            "reasoning": "Data-first language with systematic framing.",
        })
        result = classify(
            "She always wants to see the data first. Maps out frameworks before deciding.",
            client=client,
        )
        assert result.type == "Architect"
        assert result.state == "active"
        assert result.confidence == 0.85
        assert result.reasoning == "Data-first language with systematic framing."
        assert result.input_length > 0

    def test_firestarter_classification(self):
        client = _mock_client({
            "type": "Firestarter",
            "confidence": 0.78,
            "state": "active",
            "reasoning": "Visionary language with narrative framing.",
        })
        result = classify(
            "He always leads with a big picture vision, tells stories to rally the team.",
            client=client,
        )
        assert result.type == "Firestarter"
        assert result.state == "active"

    def test_inquisitor_classification(self):
        client = _mock_client({
            "type": "Inquisitor",
            "confidence": 0.72,
            "state": "active",
            "reasoning": "Probing questions and skeptical pushback.",
        })
        result = classify(
            "He challenges every assumption and demands evidence before agreeing to anything.",
            client=client,
        )
        assert result.type == "Inquisitor"
        assert result.state == "active"

    def test_bridge_builder_classification(self):
        client = _mock_client({
            "type": "Bridge Builder",
            "confidence": 0.80,
            "state": "active",
            "reasoning": "Consensus-focused language with empathetic framing.",
        })
        result = classify(
            "She always checks in with the team, finds common ground between opposing views.",
            client=client,
        )
        assert result.type == "Bridge Builder"
        assert result.state == "active"


# ---------------------------------------------------------------------------
# Pending / insufficient input
# ---------------------------------------------------------------------------

class TestPendingState:
    """Tests where classify returns pending (insufficient signal)."""

    def test_short_input_returns_pending_without_api_call(self):
        """Input under _MIN_ACTIVE_CHARS (20) bypasses the API entirely."""
        client = MagicMock()
        result = classify("Nice person", client=client)
        assert result.state == "pending"
        assert result.type is None
        assert result.confidence == 0.0
        # Should NOT have called the API for short input
        client.messages.create.assert_not_called()

    def test_few_words_returns_pending(self):
        """Input under _MIN_ACTIVE_WORDS (8) bypasses the API."""
        client = MagicMock()
        result = classify("A really great person overall", client=client)
        assert result.state == "pending"
        assert result.type is None
        client.messages.create.assert_not_called()

    def test_model_returns_pending(self):
        """Model itself can signal insufficient data."""
        client = _mock_client({
            "type": None,
            "confidence": 0.25,
            "state": "pending",
            "reasoning": "Input is too vague to identify behavioral signals.",
        })
        result = classify(
            "She is a professional who works hard and communicates well in meetings.",
            client=client,
        )
        assert result.state == "pending"
        assert result.type is None
        assert result.confidence == 0.25


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Tests for invalid inputs and malformed model responses."""

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            classify("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            classify("   \n\t  ")

    def test_invalid_json_raises_value_error(self):
        client = _mock_client_raw("This is not JSON at all.")
        with pytest.raises(ValueError, match="non-JSON"):
            classify("A detailed description of someone who uses data.", client=client)

    def test_invalid_type_raises_value_error(self):
        client = _mock_client({
            "type": "Wizard",  # not a valid Superpower
            "confidence": 0.8,
            "state": "active",
            "reasoning": "Magic.",
        })
        with pytest.raises(ValueError, match="Invalid type"):
            classify(
                "She always leads meetings with data and challenges every assumption in detail.",
                client=client,
            )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Response parsing, code fences, and consistency enforcement."""

    def test_code_fence_stripped(self):
        """Model wraps JSON in ```json ... ``` — should still parse."""
        raw = '```json\n{"type": "Architect", "confidence": 0.9, "state": "active", "reasoning": "Test."}\n```'
        client = _mock_client_raw(raw)
        result = classify(
            "She always wants to see the data first, builds frameworks and models.",
            client=client,
        )
        assert result.type == "Architect"

    def test_null_type_forces_pending_state(self):
        """If model returns type=null but state=active, fix to pending."""
        client = _mock_client({
            "type": None,
            "confidence": 0.5,
            "state": "active",  # inconsistent — should be pending when type is null
            "reasoning": "Not enough signal.",
        })
        result = classify(
            "A person who works in meetings and talks to other people about things.",
            client=client,
        )
        assert result.state == "pending"
        assert result.type is None

    def test_pending_state_forces_null_type(self):
        """If model returns state=pending but type is set, force type to None."""
        client = _mock_client({
            "type": "Architect",
            "confidence": 0.3,
            "state": "pending",  # inconsistent — pending should have null type
            "reasoning": "Weak signal.",
        })
        result = classify(
            "A person who sometimes asks questions and sometimes gives opinions on data.",
            client=client,
        )
        assert result.type is None
        assert result.state == "pending"

    def test_confidence_is_float(self):
        client = _mock_client({
            "type": "Firestarter",
            "confidence": 0.65,
            "state": "active",
            "reasoning": "Story-first approach.",
        })
        result = classify(
            "He inspires the team with stories about the future vision and moves fast.",
            client=client,
        )
        assert isinstance(result.confidence, float)
        assert 0.0 <= result.confidence <= 1.0

    def test_input_length_tracks_stripped_text(self):
        description = "  She leads with data, maps frameworks, asks analytical questions.  "
        client = _mock_client({
            "type": "Architect",
            "confidence": 0.8,
            "state": "active",
            "reasoning": "Systematic approach.",
        })
        result = classify(description, client=client)
        assert result.input_length == len(description.strip())

    def test_long_input_is_truncated(self):
        """Input over 8000 chars is truncated before sending to the API."""
        long_text = "word " * 2000  # ~10,000 chars
        client = _mock_client({
            "type": "Architect",
            "confidence": 0.7,
            "state": "active",
            "reasoning": "Detected systematic patterns.",
        })
        result = classify(long_text, client=client)
        # Should succeed without error — truncation happens internally
        assert result.type == "Architect"
        # Input length reflects the TRUNCATED length (8000), not the original
        assert result.input_length == 8000
