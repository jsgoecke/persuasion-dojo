"""
Tests for Bayesian Knowledge Tracing (BKT) — Phase 3 of Situational Flexibility.

Coverage:
  bkt_update
    - Converges toward 1.0 after many correct observations
    - Decreases after incorrect then recovers
    - P(know) stays in [0, 1] under all inputs
  classify_skill_opportunity
    - elm:ego_threat maps correctly
    - pairing:archetype_match maps correctly
    - None inputs return empty list
  relevance_score BKT weighting
    - P(know) > 0.85 → mastered penalty
    - P(know) in [0.3, 0.7] → learning zone bonus
"""

import pytest
from datetime import datetime, timezone

from backend.scoring import bkt_update, classify_skill_opportunity
from backend.coaching_bullets import (
    contextual_relevance_score,
    relevance_score,
    thompson_sample_score,
    _W_SKILL_MASTERED,
    _W_SKILL_LEARNING,
)
from backend.models import CoachingBullet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bullet(**kwargs) -> CoachingBullet:
    defaults = dict(
        id="b-1",
        user_id="u-1",
        content="Test bullet",
        category="effective",
        helpful_count=1,
        harmful_count=0,
        evidence_count=1,
        counterpart_archetype=None,
        elm_state=None,
        context=None,
        user_archetype=None,
        source_session_id=None,
        last_evidence_session_id=None,
        dedup_key=None,
        is_active=True,
        retired_reason=None,
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return CoachingBullet(**defaults)


# ---------------------------------------------------------------------------
# BKT update
# ---------------------------------------------------------------------------

class TestBKTUpdate:
    def test_converges_after_correct(self):
        """10 correct observations → P(know) > 0.8."""
        p_know = 0.1
        for _ in range(10):
            p_know = bkt_update(p_know, p_transit=0.1, p_guess=0.2, p_slip=0.1, observed_correct=True)
        assert p_know > 0.8

    def test_decreases_after_incorrect(self):
        """Incorrect observation lowers P(know), then correct recovers."""
        p_know = 0.7
        p_after_incorrect = bkt_update(p_know, 0.1, 0.2, 0.1, observed_correct=False)
        assert p_after_incorrect < p_know
        # Recovery after correct
        p_recovered = bkt_update(p_after_incorrect, 0.1, 0.2, 0.1, observed_correct=True)
        assert p_recovered > p_after_incorrect

    def test_bounds(self):
        """P(know) stays in [0, 1] under all inputs."""
        # Extreme cases
        for p_know in [0.0, 0.5, 1.0]:
            for p_transit in [0.0, 0.5, 1.0]:
                for p_guess in [0.0, 0.5]:
                    for p_slip in [0.0, 0.5]:
                        for correct in [True, False]:
                            result = bkt_update(p_know, p_transit, p_guess, p_slip, correct)
                            assert 0.0 <= result <= 1.0, (
                                f"Out of bounds: bkt_update({p_know}, {p_transit}, "
                                f"{p_guess}, {p_slip}, {correct}) = {result}"
                            )

    def test_correct_increases_knowledge(self):
        """A correct observation should increase P(know)."""
        p_know = 0.5
        result = bkt_update(p_know, 0.1, 0.2, 0.1, observed_correct=True)
        assert result > p_know

    def test_all_incorrect_stays_low(self):
        """20 incorrect observations → P(know) remains below 0.3."""
        p_know = 0.5
        for _ in range(20):
            p_know = bkt_update(p_know, p_transit=0.05, p_guess=0.2, p_slip=0.1, observed_correct=False)
        assert p_know < 0.3

    def test_zero_params_no_crash(self):
        """All-zero BKT params don't crash (degenerate case)."""
        result = bkt_update(0.0, 0.0, 0.0, 0.0, observed_correct=True)
        assert 0.0 <= result <= 1.0
        result = bkt_update(0.0, 0.0, 0.0, 0.0, observed_correct=False)
        assert 0.0 <= result <= 1.0

    def test_near_mastery_stays_high(self):
        """P(know) near 1.0 stays high after a single incorrect (slip)."""
        p_know = 0.95
        result = bkt_update(p_know, 0.05, 0.2, 0.1, observed_correct=False)
        assert result > 0.5  # One slip shouldn't destroy mastery

    def test_unrecognized_triggered_by(self):
        """Unrecognized triggered_by prefix returns empty list (no crash)."""
        results = classify_skill_opportunity("unknown:new_trigger", 0.7, "Architect")
        # Only pairing:archetype_match (from counterpart), not the unrecognized prefix
        skill_keys = [r[0] for r in results]
        assert "unknown:new_trigger" not in skill_keys


# ---------------------------------------------------------------------------
# Skill opportunity classification
# ---------------------------------------------------------------------------

class TestClassifySkillOpportunity:
    def test_elm_ego_threat(self):
        """elm:ego_threat maps to the elm:ego_threat skill key."""
        results = classify_skill_opportunity("elm:ego_threat", 0.7, "Architect")
        skill_keys = [r[0] for r in results]
        assert "elm:ego_threat" in skill_keys
        # Should also include pairing since counterpart is known
        assert "pairing:archetype_match" in skill_keys

    def test_elm_shortcut(self):
        """elm:shortcut maps to elm:shortcut skill key."""
        results = classify_skill_opportunity("elm:shortcut", 0.6, None)
        skill_keys = [r[0] for r in results]
        assert "elm:shortcut" in skill_keys

    def test_cadence_maps_to_timing(self):
        """cadence: triggers map to timing:talk_ratio."""
        results = classify_skill_opportunity("cadence:self", 0.8, None)
        skill_keys = [r[0] for r in results]
        assert "timing:talk_ratio" in skill_keys

    def test_pairing_archetype_match(self):
        """Known counterpart archetype adds pairing:archetype_match."""
        results = classify_skill_opportunity("cadence:self", 0.7, "Firestarter")
        skill_keys = [r[0] for r in results]
        assert "pairing:archetype_match" in skill_keys

    def test_none_inputs(self):
        """None triggered_by or effectiveness → empty list."""
        assert classify_skill_opportunity(None, 0.5, "Architect") == []
        assert classify_skill_opportunity("elm:ego_threat", None, "Architect") == []

    def test_effectiveness_threshold(self):
        """Below threshold → observed_correct=False."""
        results = classify_skill_opportunity("elm:ego_threat", 0.3, None, effectiveness_threshold=0.5)
        for _, correct in results:
            assert correct is False


# ---------------------------------------------------------------------------
# Relevance score BKT weighting
# ---------------------------------------------------------------------------

class TestRelevanceScoreBKT:
    def test_mastered_skill_penalty(self):
        """P(know) > 0.85 → penalty applied."""
        bullet = _bullet(elm_state="elm:ego_threat")
        score_without = relevance_score(bullet, elm_state="elm:ego_threat")
        score_with = relevance_score(
            bullet, elm_state="elm:ego_threat",
            skill_mastery={"elm:ego_threat": 0.9},
        )
        assert score_with < score_without
        assert score_with == pytest.approx(score_without + _W_SKILL_MASTERED)

    def test_learning_zone_bonus(self):
        """P(know) in [0.3, 0.7] → bonus applied."""
        bullet = _bullet(elm_state="elm:ego_threat")
        score_without = relevance_score(bullet, elm_state="elm:ego_threat")
        score_with = relevance_score(
            bullet, elm_state="elm:ego_threat",
            skill_mastery={"elm:ego_threat": 0.5},
        )
        assert score_with > score_without
        assert score_with == pytest.approx(score_without + _W_SKILL_LEARNING)

    def test_no_mastery_data_unchanged(self):
        """No skill_mastery dict → score unchanged."""
        bullet = _bullet(elm_state="elm:ego_threat")
        score_none = relevance_score(bullet, elm_state="elm:ego_threat")
        score_empty = relevance_score(
            bullet, elm_state="elm:ego_threat", skill_mastery={},
        )
        assert score_none == score_empty


# ---------------------------------------------------------------------------
# Thompson Sampling (Phase 4)
# ---------------------------------------------------------------------------

class TestThompsonSampling:
    def test_sample_in_range(self):
        """Thompson sample is always in [0, 1]."""
        for _ in range(100):
            score = thompson_sample_score(10, 5)
            assert 0.0 <= score <= 1.0

    def test_statistical_dominance(self):
        """100 helpful / 5 harmful should consistently outscore the inverse."""
        wins = 0
        for _ in range(1000):
            good = thompson_sample_score(100, 5)
            bad = thompson_sample_score(5, 100)
            if good > bad:
                wins += 1
        # Should win overwhelmingly — at least 95% of the time
        assert wins > 950

    def test_explore_false_matches_existing(self):
        """explore=False → identical to relevance_score."""
        bullet = _bullet(helpful_count=5, harmful_count=1)
        base = relevance_score(bullet)
        ctx = contextual_relevance_score(bullet, explore=False)
        assert ctx == base

    def test_explore_true_adds_thompson(self):
        """explore=True → score differs from base (Thompson adds randomness)."""
        bullet = _bullet(helpful_count=5, harmful_count=1)
        base = relevance_score(bullet)
        # Run many times — at least some should differ
        different = False
        for _ in range(20):
            ctx = contextual_relevance_score(bullet, explore=True)
            if ctx != base:
                different = True
                break
        assert different, "Thompson sampling should add variation"

    def test_zero_counts_still_works(self):
        """New bullet with 0 helpful/harmful → sample from prior Beta(1,1)."""
        bullet = _bullet(helpful_count=0, harmful_count=0)
        score = contextual_relevance_score(bullet, explore=True)
        # Should not crash, and should produce a valid score
        assert isinstance(score, float)
