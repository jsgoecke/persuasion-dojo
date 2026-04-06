"""
Tests for backend/self_assessment.py — Self-Assessment Instrument.

Coverage:
  Item definitions
    - 12 items total: 6 focus, 6 stance
    - 50% reverse-scoring per axis (3F + 3R)
    - All items have valid ids and axes
  Reverse scoring
    - _reverse_score: 1↔7, 4↔4
  Axis normalization
    - All 7s  → +100
    - All 1s  → −100
    - All 4s  → 0 (neutral)
    - Mixed   → proportional
  score_responses()
    - Perfect Logic + Advocacy → (+100, +100)
    - Perfect Narrative + Analysis → (−100, −100)
    - Neutral all-4s → (0, 0)
    - Reverse items scored correctly (vs forward-only)
    - Empty responses → low confidence, neutral
    - Single-axis partial → items_used reflects actuals
  Confidence
    - Consistent responses → higher confidence than erratic
    - Fast responses (< 1000ms) → penalised
    - Uniform timing (low CV) → penalised
    - micro_argument present → confidence boosted
  map_to_archetype()
    - (+50, +50)  → Inquisitor
    - (−50, +50)  → Firestarter
    - (+50, −50)  → Architect
    - (−50, −50)  → Bridge Builder
    - (±10, +50)  → Undetermined (focus in neutral band)
    - (+50, ±10)  → Undetermined (stance in neutral band)
    - Exactly ±15 → Undetermined (band is inclusive)
    - Exactly ±16 → classified (just outside band)
  build_result()
    - Micro-argument deltas applied to axis scores
    - Deltas clamped at ±100
    - Confidence boosted by 0.10 when micro-arg present
    - Note contains archetype name for classified result
    - Note mentions neutral band for Undetermined result
  MicroArgumentResult structure
    - focus_delta clamped to [−10, 10]
    - stance_delta clamped to [−10, 10]
"""

from __future__ import annotations

import pytest
from backend.self_assessment import (
    ITEMS,
    AssessmentItem,
    AssessmentResponse,
    MicroArgumentResult,
    ScoredAxes,
    _axis_raw_to_normalized,
    _reverse_score,
    build_result,
    map_to_archetype,
    score_responses,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_responses(raw_score: int, time_ms: int = 5000) -> list[AssessmentResponse]:
    """Build a full 12-item response list with the same raw_score for every item."""
    return [
        AssessmentResponse(item_id=item.id, raw_score=raw_score, response_time_ms=time_ms)
        for item in ITEMS
    ]


def _responses_for_axes(
    focus_raw: int,
    stance_raw: int,
    time_ms: int = 5000,
) -> list[AssessmentResponse]:
    """Build responses with different raw scores per axis."""
    return [
        AssessmentResponse(
            item_id=item.id,
            raw_score=focus_raw if item.axis == "focus" else stance_raw,
            response_time_ms=time_ms,
        )
        for item in ITEMS
    ]


def _make_axes(focus: float, stance: float, confidence: float = 0.70) -> ScoredAxes:
    return ScoredAxes(
        focus_score=focus,
        stance_score=stance,
        confidence=confidence,
        in_neutral_band={"focus": abs(focus) <= 15, "stance": abs(stance) <= 15},
        items_used=12,
    )


def _make_micro(focus_delta: float, stance_delta: float) -> MicroArgumentResult:
    fa = "logic" if focus_delta > 0 else "narrative" if focus_delta < 0 else "neutral"
    sa = "advocacy" if stance_delta > 0 else "analysis" if stance_delta < 0 else "neutral"
    return MicroArgumentResult(
        text="We should launch now to get user feedback.",
        focus_axis=fa,
        stance_axis=sa,
        focus_delta=focus_delta,
        stance_delta=stance_delta,
        reasoning="Direct advocacy with market-timing framing.",
    )


# ---------------------------------------------------------------------------
# Item definitions
# ---------------------------------------------------------------------------

class TestItemDefinitions:
    def test_total_item_count(self):
        assert len(ITEMS) == 12

    def test_six_focus_items(self):
        focus = [i for i in ITEMS if i.axis == "focus"]
        assert len(focus) == 6

    def test_six_stance_items(self):
        stance = [i for i in ITEMS if i.axis == "stance"]
        assert len(stance) == 6

    def test_three_forward_three_reverse_per_axis(self):
        for axis in ("focus", "stance"):
            axis_items = [i for i in ITEMS if i.axis == axis]
            forward = [i for i in axis_items if not i.reverse]
            reverse = [i for i in axis_items if i.reverse]
            assert len(forward) == 3, f"{axis} forward count"
            assert len(reverse) == 3, f"{axis} reverse count"

    def test_unique_ids(self):
        ids = [i.id for i in ITEMS]
        assert len(ids) == len(set(ids))

    def test_all_items_have_text(self):
        for item in ITEMS:
            assert len(item.text) > 10, f"Item {item.id} text too short"


# ---------------------------------------------------------------------------
# Reverse scoring
# ---------------------------------------------------------------------------

class TestReverseScore:
    def test_1_becomes_7(self):
        assert _reverse_score(1) == 7

    def test_7_becomes_1(self):
        assert _reverse_score(7) == 1

    def test_4_stays_4(self):
        assert _reverse_score(4) == 4

    def test_2_becomes_6(self):
        assert _reverse_score(2) == 6

    def test_3_becomes_5(self):
        assert _reverse_score(3) == 5


# ---------------------------------------------------------------------------
# Axis normalization
# ---------------------------------------------------------------------------

class TestAxisNormalization:
    def test_all_sevens_normalises_to_plus_100(self):
        result = _axis_raw_to_normalized(7 * 6, 6)
        assert result == pytest.approx(100.0)

    def test_all_ones_normalises_to_minus_100(self):
        result = _axis_raw_to_normalized(1 * 6, 6)
        assert result == pytest.approx(-100.0)

    def test_all_fours_normalises_to_zero(self):
        result = _axis_raw_to_normalized(4 * 6, 6)
        assert result == pytest.approx(0.0)

    def test_single_item_all_seven(self):
        result = _axis_raw_to_normalized(7, 1)
        assert result == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# score_responses()
# ---------------------------------------------------------------------------

class TestScoreResponses:
    def test_all_sevens_gives_perfect_logic_advocacy(self):
        """
        All 7s on forward items AND all 7s on reverse items.
        Reverse items are scored as 8-7=1 — this actually gives mixed results.

        Wait: if raw=7 on a REVERSE item, scored = 8-7=1 which is the MINIMUM for that
        pole. So all-7s does NOT give +100 on both axes.

        Let's think: to get +100 on Focus (Logic pole):
          Forward Focus items (reverse=False): raw=7 (max logic)
          Reverse Focus items (reverse=True):  raw=1 (after reverse: 8-1=7, max logic)

        So we need different raw scores per item direction, not a simple all-7s call.
        Instead, test with responses that target max score per axis correctly.
        """
        # Target max Logic: forward=7, reverse=1
        # Target max Advocacy: forward=7, reverse=1
        responses = []
        for item in ITEMS:
            if item.reverse:
                raw = 1
            else:
                raw = 7
            responses.append(AssessmentResponse(item_id=item.id, raw_score=raw, response_time_ms=5000))

        result = score_responses(responses)
        assert result.focus_score == pytest.approx(100.0)
        assert result.stance_score == pytest.approx(100.0)

    def test_max_narrative_analysis(self):
        """
        Max Narrative: forward=1, reverse=7 → after reverse: 8-7=1 (lowest scored)
        Wait, reverse items with raw=7: scored = 8-7=1 → contributes to LOW axis score.
        Forward items with raw=1 → contributes to LOW axis score.
        → axis score -100.
        """
        responses = []
        for item in ITEMS:
            if item.reverse:
                raw = 7   # scored = 1 → lowest
            else:
                raw = 1   # scored = 1 → lowest
            responses.append(AssessmentResponse(item_id=item.id, raw_score=raw, response_time_ms=5000))

        result = score_responses(responses)
        assert result.focus_score == pytest.approx(-100.0)
        assert result.stance_score == pytest.approx(-100.0)

    def test_all_fours_gives_zero(self):
        """All 4s: forward score=4, reverse score=8-4=4. Sum=24. Normalised=0."""
        result = score_responses(_all_responses(4))
        assert result.focus_score == pytest.approx(0.0)
        assert result.stance_score == pytest.approx(0.0)
        assert result.in_neutral_band["focus"] is True
        assert result.in_neutral_band["stance"] is True

    def test_reverse_scoring_changes_result(self):
        """Forward-only scoring vs. correct reverse scoring should differ."""
        # If we give 7 to all items, reverse items are scored as 1 (not 7).
        # The result should NOT be +100.
        result = score_responses(_all_responses(7))
        # Forward items score 7, reverse items score 1 — mixed → moderate score, not 100.
        # Expecting somewhere around 33 (3 items at 7, 3 items at 1, avg ~4 ≈ 33)
        # More precisely: sum = 3*7 + 3*1 = 24. Normalised = (24-6)/36*200-100 = 0
        assert result.focus_score == pytest.approx(0.0)
        assert result.stance_score == pytest.approx(0.0)

    def test_empty_responses_returns_zero_neutral(self):
        result = score_responses([])
        assert result.focus_score == 0.0
        assert result.stance_score == 0.0
        assert result.confidence == 0.0
        assert result.items_used == 0

    def test_items_used_reflects_valid_count(self):
        # Only provide focus responses
        focus_ids = [item.id for item in ITEMS if item.axis == "focus"]
        partial = [
            AssessmentResponse(item_id=iid, raw_score=5, response_time_ms=3000)
            for iid in focus_ids
        ]
        result = score_responses(partial)
        assert result.items_used == len(focus_ids)

    def test_out_of_range_scores_skipped(self):
        responses = _all_responses(4)
        # Corrupt one response
        responses[0] = AssessmentResponse(
            item_id=responses[0].item_id, raw_score=8, response_time_ms=5000
        )
        result = score_responses(responses)
        assert result.items_used == 11  # one skipped


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_consistent_responses_higher_confidence(self):
        """Uniform responses (low MAD) → higher confidence than erratic ones."""
        consistent = _all_responses(5, time_ms=5000)
        erratic = [
            AssessmentResponse(item_id=item.id, raw_score=(i % 7) + 1, response_time_ms=5000)
            for i, item in enumerate(ITEMS)
        ]
        c_consistent = score_responses(consistent).confidence
        c_erratic = score_responses(erratic).confidence
        assert c_consistent > c_erratic

    def test_fast_responses_penalised(self):
        """Responses under 1000ms should lower confidence."""
        slow = _all_responses(5, time_ms=8000)
        fast = _all_responses(5, time_ms=500)
        c_slow = score_responses(slow).confidence
        c_fast = score_responses(fast).confidence
        assert c_slow > c_fast

    def test_untracked_timing_not_penalised(self):
        """response_time_ms=0 means timing not tracked — should not be penalised."""
        untracked = _all_responses(5, time_ms=0)
        timed = _all_responses(5, time_ms=5000)
        c_untracked = score_responses(untracked).confidence
        c_timed = score_responses(timed).confidence
        # Both should be similar (untracked is not penalised as fast)
        assert c_untracked >= c_timed * 0.80  # within 20%

    def test_micro_argument_boosts_confidence(self):
        """build_result with micro-arg should have higher confidence than without."""
        axes = _make_axes(50.0, 50.0, confidence=0.70)
        micro = _make_micro(focus_delta=5.0, stance_delta=5.0)
        with_micro = build_result(axes, micro_argument=micro)
        without_micro = build_result(axes, micro_argument=None)
        assert with_micro.confidence > without_micro.confidence


# ---------------------------------------------------------------------------
# map_to_archetype()
# ---------------------------------------------------------------------------

class TestMapToArchetype:
    def test_logic_advocacy_is_inquisitor(self):
        assert map_to_archetype(50, 50) == "Inquisitor"

    def test_narrative_advocacy_is_firestarter(self):
        assert map_to_archetype(-50, 50) == "Firestarter"

    def test_logic_analysis_is_architect(self):
        assert map_to_archetype(50, -50) == "Architect"

    def test_narrative_analysis_is_bridge_builder(self):
        assert map_to_archetype(-50, -50) == "Bridge Builder"

    def test_neutral_focus_returns_partial(self):
        assert map_to_archetype(10, 50) == "Advocacy-leaning"

    def test_neutral_stance_returns_partial(self):
        assert map_to_archetype(50, 10) == "Logic-leaning"

    def test_both_neutral_is_undetermined(self):
        assert map_to_archetype(5, -5) == "Undetermined"

    def test_exactly_plus_15_returns_partial(self):
        assert map_to_archetype(15, 50) == "Advocacy-leaning"

    def test_exactly_minus_15_returns_partial(self):
        assert map_to_archetype(-15, 50) == "Advocacy-leaning"

    def test_plus_16_is_classified(self):
        assert map_to_archetype(16, 50) == "Inquisitor"

    def test_minus_16_is_classified(self):
        assert map_to_archetype(-16, 50) == "Firestarter"

    def test_custom_neutral_band(self):
        """Custom neutral_band parameter is respected."""
        # With band=30: score of 25 should be partial (focus in band)
        assert map_to_archetype(25, 50, neutral_band=30) == "Advocacy-leaning"
        # With band=10: score of 25 should be classified
        assert map_to_archetype(25, 50, neutral_band=10) == "Inquisitor"


# ---------------------------------------------------------------------------
# build_result()
# ---------------------------------------------------------------------------

class TestBuildResult:
    def test_micro_delta_applied_to_focus(self):
        axes = _make_axes(40.0, 50.0)
        micro = _make_micro(focus_delta=8.0, stance_delta=0.0)
        result = build_result(axes, micro_argument=micro)
        assert result.focus_score == pytest.approx(48.0)

    def test_micro_delta_applied_to_stance(self):
        axes = _make_axes(50.0, 40.0)
        micro = _make_micro(focus_delta=0.0, stance_delta=-5.0)
        result = build_result(axes, micro_argument=micro)
        assert result.stance_score == pytest.approx(35.0)

    def test_delta_clamped_at_plus_100(self):
        axes = _make_axes(95.0, 50.0)
        micro = _make_micro(focus_delta=10.0, stance_delta=0.0)
        result = build_result(axes, micro_argument=micro)
        assert result.focus_score == 100.0

    def test_delta_clamped_at_minus_100(self):
        axes = _make_axes(-95.0, 50.0)
        micro = _make_micro(focus_delta=-10.0, stance_delta=0.0)
        result = build_result(axes, micro_argument=micro)
        assert result.focus_score == -100.0

    def test_no_micro_arg_axes_unchanged(self):
        axes = _make_axes(60.0, -40.0)
        result = build_result(axes, micro_argument=None)
        assert result.focus_score == 60.0
        assert result.stance_score == -40.0

    def test_classified_result_note_contains_archetype(self):
        axes = _make_axes(50.0, 50.0)
        result = build_result(axes)
        assert "Inquisitor" in result.note

    def test_undetermined_result_note_mentions_neutral_band(self):
        axes = _make_axes(5.0, 50.0)  # focus in neutral band
        result = build_result(axes)
        assert result.archetype == "Advocacy-leaning"
        assert "leaning" in result.note.lower() or "neutral" in result.note.lower()

    def test_in_neutral_band_updated_after_delta(self):
        """Micro-arg delta can push a borderline score out of the neutral band."""
        axes = _make_axes(12.0, 50.0)  # focus barely in neutral band (12 ≤ 15)
        micro = _make_micro(focus_delta=5.0, stance_delta=0.0)  # pushes to 17
        result = build_result(axes, micro_argument=micro)
        assert result.focus_score == pytest.approx(17.0)
        assert result.in_neutral_band["focus"] is False
        assert result.archetype == "Inquisitor"

    def test_archetype_correct_all_quadrants(self):
        cases = [
            ((50, 50), "Inquisitor"),
            ((-50, 50), "Firestarter"),
            ((50, -50), "Architect"),
            ((-50, -50), "Bridge Builder"),
        ]
        for (f, s), expected in cases:
            axes = _make_axes(float(f), float(s))
            result = build_result(axes)
            assert result.archetype == expected, f"({f},{s}) expected {expected}"
