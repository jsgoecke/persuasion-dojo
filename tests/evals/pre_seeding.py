"""
Pre-seeding LLM eval fixtures — P0 accuracy gate.

These tests call the live Claude API. Run with:
    pytest tests/evals/pre_seeding.py -v

Pass criterion: all 6 fixtures pass → pre_seeding.py is safe to deploy.
If any fixture fails, inspect the model output and refine the prompt in
backend/pre_seeding.py before retrying.

P0 gate status:
  - Fixtures 1–6: automated (run against Claude API)
  - Real-world accuracy gate: 5 known-profile participants (human effort, ~2 hrs)

    How to run the real-world gate:
    1. Choose 5 people whose Superpower you know well (colleagues, direct reports, friends).
    2. Write a ~50-word description of each from memory (don't use their name).
    3. Add each as a fixture in scripts/real_world_gate.py following this format.
    4. Run: python scripts/real_world_gate.py
    5. ≥70% correct = gate passes = pre_seeding.py safe to deploy.
"""

import os
import pytest
from backend.pre_seeding import classify, PreSeedResult


# ---------------------------------------------------------------------------
# Skip all tests gracefully if no API key
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping LLM eval fixtures",
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def assert_classification(
    result: PreSeedResult,
    expected_types: list[str],
    min_confidence: float,
    *,
    allow_pending: bool = False,
) -> None:
    """Assert classification meets expected type and confidence floor."""
    if not allow_pending:
        assert result.state == "active", (
            f"Expected state='active', got state='{result.state}'. "
            f"Reasoning: {result.reasoning}"
        )
        assert result.type in expected_types, (
            f"Expected type in {expected_types}, got '{result.type}'. "
            f"Confidence: {result.confidence:.2f}. Reasoning: {result.reasoning}"
        )
        assert result.confidence >= min_confidence, (
            f"Expected confidence ≥ {min_confidence}, got {result.confidence:.2f}. "
            f"Type: {result.type}. Reasoning: {result.reasoning}"
        )
    else:
        assert result.state == "pending", (
            f"Expected state='pending' for vague input, got state='{result.state}'. "
            f"Type: {result.type}. Confidence: {result.confidence:.2f}"
        )
        assert result.confidence < 0.40, (
            f"Expected confidence < 0.40 for pending state, got {result.confidence:.2f}"
        )
        assert result.type is None, (
            f"Expected type=None for pending state, got '{result.type}'"
        )


# ---------------------------------------------------------------------------
# Fixture 1: Clear Inquisitor
# ---------------------------------------------------------------------------

def test_inquisitor_cfos():
    """
    A CFO who challenges assumptions and needs data before moving.
    Expected: Inquisitor ≥0.60
    """
    description = (
        "Sarah is our CFO. She always challenges assumptions and needs data before "
        "she'll make a decision. In meetings she asks hard questions and pushes back "
        "on weak reasoning. She won't move until the numbers add up."
    )
    result = classify(description)
    assert_classification(result, ["Inquisitor"], min_confidence=0.60)


# ---------------------------------------------------------------------------
# Fixture 2: Clear Firestarter
# ---------------------------------------------------------------------------

def test_firestarter_visionary():
    """
    Someone energized by big ideas, storytelling, future-orientation.
    Expected: Firestarter ≥0.60
    """
    description = (
        "Marcus gets excited about big ideas and loves telling stories about where "
        "we're going. He rallies the team with his energy and vision. Sometimes he "
        "moves too fast and skips the details, but he's the one who gets people fired up."
    )
    result = classify(description)
    assert_classification(result, ["Firestarter"], min_confidence=0.60)


# ---------------------------------------------------------------------------
# Fixture 3: Clear Bridge Builder
# ---------------------------------------------------------------------------

def test_bridge_builder_empathetic():
    """
    Someone who reads the room, builds consensus, very empathetic.
    Expected: Bridge Builder ≥0.55
    """
    description = (
        "Chen builds bridges between teams and is very empathetic. He's always checking "
        "in to make sure everyone feels heard. In meetings he synthesizes different "
        "perspectives and finds the common ground. He avoids conflict but moves toward alignment."
    )
    result = classify(description)
    assert_classification(result, ["Bridge Builder"], min_confidence=0.55)


# ---------------------------------------------------------------------------
# Fixture 4: Clear Architect
# ---------------------------------------------------------------------------

def test_architect_systematic():
    """
    Someone methodical, systematic, maps everything out before deciding.
    Expected: Architect ≥0.60
    """
    description = (
        "David is methodical and systematic. He maps everything out before deciding. "
        "He creates frameworks for every problem, always wants to understand the full "
        "system before acting, and gets uncomfortable when others skip steps."
    )
    result = classify(description)
    assert_classification(result, ["Architect"], min_confidence=0.60)


# ---------------------------------------------------------------------------
# Fixture 5: Email thread inference (Inquisitor or Architect)
# ---------------------------------------------------------------------------

def test_email_thread_data_heavy():
    """
    Pasted email thread: assertive, data-heavy, challenges reasoning.
    Expected: Inquisitor or Architect ≥0.50

    Note: Both types are logic-oriented. An assertive, questioning tone
    points to Inquisitor; a framework/process-heavy tone to Architect.
    The threshold is lower (0.50) because the signal is ambiguous.
    """
    description = """\
From: Jordan Lee
Subject: Re: Q3 Budget Proposal

I've reviewed the proposal. A few concerns:

1. The $2.4M projection assumes 15% YoY growth, but our last 4 quarters averaged 8.2%.
   What's the basis for the 15% assumption? I need to see the supporting model.

2. The headcount request is missing a capacity analysis. Before I can approve any
   new hires, I need to know current utilization rates and what specifically will break
   without them.

3. The timeline says "Q3" but doesn't define milestones. What does done look like
   by week 6, week 10, week 13? Without that I can't assess feasibility.

I'm not opposed to this in principle but I'm not signing off until the numbers hold up.

— Jordan

---
From: Jordan Lee
Subject: Re: Re: Q3 Budget Proposal

I've looked at the revised model. The 15% growth figure is now explained — thank you.
But the underlying assumption is still that we retain our top 3 accounts. What's the
plan if we don't? I want to see a downside scenario before the board meeting.
"""
    result = classify(description)
    assert_classification(result, ["Inquisitor", "Architect"], min_confidence=0.50)


# ---------------------------------------------------------------------------
# Fixture 6: Vague input → PENDING state
# ---------------------------------------------------------------------------

def test_vague_input_returns_pending():
    """
    A single vague sentence provides no behavioral signal.
    Expected: PENDING state, confidence < 0.40, type = None
    """
    description = "She's smart."
    result = classify(description)
    assert_classification(result, [], min_confidence=0.0, allow_pending=True)


# ---------------------------------------------------------------------------
# Additional edge case: empty/whitespace
# ---------------------------------------------------------------------------

def test_empty_input_raises():
    """Empty input raises ValueError before hitting the API."""
    with pytest.raises(ValueError, match="non-empty"):
        classify("")


def test_whitespace_input_raises():
    with pytest.raises(ValueError, match="non-empty"):
        classify("   ")


# ---------------------------------------------------------------------------
# Structure tests (no API call required — test response parsing)
# ---------------------------------------------------------------------------

def test_result_has_required_fields():
    """PreSeedResult must have all required fields even from cache/stub."""
    # Use a short input that fast-paths to pending without API call
    result = classify("Hi.")
    assert hasattr(result, "type")
    assert hasattr(result, "confidence")
    assert hasattr(result, "state")
    assert hasattr(result, "reasoning")
    assert hasattr(result, "input_length")


def test_short_input_fast_path_no_api():
    """Inputs under 8 words fast-path to PENDING without hitting the API."""
    result = classify("Great presenter.")
    assert result.state == "pending"
    assert result.type is None
    assert result.confidence == 0.0


def test_input_truncation_preserved():
    """Inputs over 8000 chars are truncated but still classified."""
    long_input = "Sarah always challenges assumptions and needs data to move. " * 200  # ~12,000 chars
    result = classify(long_input)
    # Result is valid; we don't check type here since it's truncated repetition
    assert result.state in ("active", "pending")
    assert 0.0 <= result.confidence <= 1.0
