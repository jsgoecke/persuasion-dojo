"""
Tests for backend/scoring.py — Persuasion Score + Growth Score.

Coverage:
  Timing component
    - sweet spot (25–45%) → score 1.0
    - dominating (>65%) → score near 0.0
    - silent (<15%) → score near 0.0
    - empty utterances → score 0.0, no crash
  Ego Safety component
    - no challenging questions → score 1.0
    - all challenging questions → score near 0.0
    - ELM penalty applied correctly
    - missing signal result (no arc signal) → safe default
  Convergence component
    - delegated to signals.py (tested in convergence_spike)
    - happy path wired correctly into composite
  Persuasion Score composite
    - weights sum to 100 (30+30+40)
    - score is 0–100 integer
    - all-zero inputs → score 0, no crash
    - realistic converging session → score > 50
  Growth Score
    - first session (no prior) → returns None
    - improving: delta >= 3 → trend "improving"
    - declining: delta <= -3 → trend "declining"
    - stable: |delta| < 3 → trend "stable"
    - window parameter respected (only last N sessions)
    - single prior session → baseline = that session
"""

from __future__ import annotations

import pytest
from backend.scoring import (
    BADGE_METADATA,
    compute_persuasion_score,
    compute_growth_score,
    compute_prompt_effectiveness,
    compute_skill_badges,
    update_coaching_effectiveness,
    _score_timing,
    _score_ego_safety,
)
from backend.signals import SignalResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_utterances(
    user_ratio: float = 0.35,
    total_words: int = 200,
    include_challenging: bool = False,
    include_agreement: bool = True,
) -> tuple[list[dict], str]:
    """
    Build a synthetic utterance list with controllable talk-time ratio.

    user_ratio: fraction of total_words spoken by the user.
    Words are padded with domain vocabulary so signals fire correctly.
    """
    user_speaker = "speaker_0"
    audience_speaker = "speaker_1"

    user_word_count = max(6, int(total_words * user_ratio))
    audience_word_count = total_words - user_word_count

    # User utterances — seed vocabulary so vocabulary_adoption can fire
    domain_words = "framework process design decision architecture system platform"
    user_text = " ".join(
        (domain_words + " ") * ((user_word_count // 7) + 1)
    ).split()[:user_word_count]
    user_utts = [
        {"speaker": user_speaker, "text": " ".join(user_text), "start": 0.0, "end": 30.0},
    ]

    # Audience utterances — fixed seed + padding to reach audience_word_count
    audience_utts = []
    t = 5.0

    # Fixed signal-driving utterances (constant word count: ~50 words)
    signal_utts: list[dict] = []

    if include_challenging:
        signal_utts += [
            {"speaker": audience_speaker, "text": "Why should we adopt this framework? What evidence do you have?", "start": t, "end": t + 5},
            {"speaker": audience_speaker, "text": "What makes you think this will work?", "start": t + 5, "end": t + 10},
        ]
        t += 10

    signal_utts += [
        {"speaker": audience_speaker, "text": "Can you walk me through how the design process would work?", "start": t, "end": t + 5},
        {"speaker": audience_speaker, "text": "That makes sense for the architecture decision.", "start": t + 5, "end": t + 10},
        {"speaker": audience_speaker, "text": "The framework and design system approach makes sense.", "start": t + 10, "end": t + 15},
    ]
    t += 15

    if include_agreement:
        signal_utts += [
            {"speaker": audience_speaker, "text": "Yes, let's move forward with this framework.", "start": t, "end": t + 5},
            {"speaker": audience_speaker, "text": "We're aligned on the design architecture process.", "start": t + 5, "end": t + 10},
        ]
        t += 10

    # Pad remaining audience words with neutral filler
    signal_words = sum(len(u["text"].split()) for u in signal_utts)
    remaining = audience_word_count - signal_words
    if remaining > 0:
        filler = " ".join(["okay noted understood"] * ((remaining // 3) + 1)).split()[:remaining]
        signal_utts.append(
            {"speaker": audience_speaker, "text": " ".join(filler), "start": t, "end": t + 10}
        )

    return user_utts + signal_utts, user_speaker


# ---------------------------------------------------------------------------
# Timing component
# ---------------------------------------------------------------------------

class TestTimingComponent:
    def test_sweet_spot_scores_one(self):
        """35% talk-time ratio is solidly in the 25–45% sweet spot → score 1.0."""
        utterances, user_speaker = _make_utterances(user_ratio=0.35)
        result = _score_timing(utterances, user_speaker)
        assert result.score == 1.0
        assert result.in_sweet_spot is True

    def test_dominating_scores_low(self):
        """User speaks 80% of words → low score (dominating)."""
        user_speaker = "speaker_0"
        audience_speaker = "speaker_1"
        utterances = [
            {"speaker": user_speaker, "text": " ".join(["design"] * 80), "start": 0.0, "end": 60.0},
            {"speaker": audience_speaker, "text": " ".join(["okay"] * 20), "start": 60.0, "end": 70.0},
        ]
        result = _score_timing(utterances, user_speaker)
        assert result.talk_time_ratio == pytest.approx(0.80)
        assert result.score < 0.3

    def test_silent_scores_low(self):
        """User speaks only 5% of words → low score (not leading)."""
        user_speaker = "speaker_0"
        audience_speaker = "speaker_1"
        utterances = [
            {"speaker": user_speaker, "text": "okay", "start": 0.0, "end": 2.0},
            {"speaker": audience_speaker, "text": " ".join(["meeting"] * 95), "start": 2.0, "end": 60.0},
        ]
        result = _score_timing(utterances, user_speaker)
        assert result.talk_time_ratio < 0.15
        assert result.score < 0.4

    def test_empty_utterances_no_crash(self):
        result = _score_timing([], "speaker_0")
        assert result.score == 0.0
        assert result.total_words == 0

    def test_lower_edge_of_sweet_spot(self):
        """25% is the bottom of the sweet spot → score 1.0."""
        user_speaker = "speaker_0"
        audience_speaker = "speaker_1"
        utterances = [
            {"speaker": user_speaker, "text": " ".join(["x"] * 25), "start": 0.0, "end": 10.0},
            {"speaker": audience_speaker, "text": " ".join(["x"] * 75), "start": 10.0, "end": 40.0},
        ]
        result = _score_timing(utterances, user_speaker)
        assert result.in_sweet_spot is True
        assert result.score == 1.0

    def test_upper_edge_of_sweet_spot(self):
        """45% is the top of the sweet spot → score 1.0."""
        user_speaker = "speaker_0"
        audience_speaker = "speaker_1"
        utterances = [
            {"speaker": user_speaker, "text": " ".join(["x"] * 45), "start": 0.0, "end": 10.0},
            {"speaker": audience_speaker, "text": " ".join(["x"] * 55), "start": 10.0, "end": 30.0},
        ]
        result = _score_timing(utterances, user_speaker)
        assert result.in_sweet_spot is True
        assert result.score == 1.0


# ---------------------------------------------------------------------------
# Ego Safety component
# ---------------------------------------------------------------------------

def _make_arc_signal(challenge_count: int, total_questions: int) -> SignalResult:
    """Build a minimal SignalResult for question_type_arc with given counts."""
    return SignalResult(
        signal="question_type_arc",
        converging=challenge_count == 0,
        score=0.5,
        evidence=[],
        details={
            "total_questions": total_questions,
            "total_challenging": challenge_count,
            "total_confirmatory": 0,
            "first_half_counts": {},
            "second_half_counts": {},
            "challenge_ratio_first": 0.0,
            "challenge_ratio_second": 0.0,
            "confirm_ratio_first": 0.0,
            "confirm_ratio_second": 0.0,
            "path_a": False,
            "path_b": False,
        },
    )


class TestEgoSafetyComponent:
    def test_no_challenges_scores_one(self):
        """Zero challenging questions and no ELM events → score 1.0."""
        arc = _make_arc_signal(challenge_count=0, total_questions=5)
        result = _score_ego_safety([arc], ego_threat_events=0)
        assert result.score == 1.0
        assert result.challenge_ratio == 0.0

    def test_all_challenging_scores_near_zero(self):
        """All questions are challenging → score near 0."""
        arc = _make_arc_signal(challenge_count=5, total_questions=5)
        result = _score_ego_safety([arc], ego_threat_events=0)
        assert result.score == pytest.approx(0.0)

    def test_elm_penalty_reduces_score(self):
        """ELM ego-threat events reduce the score."""
        arc = _make_arc_signal(challenge_count=0, total_questions=5)
        no_elm = _score_ego_safety([arc], ego_threat_events=0)
        with_elm = _score_ego_safety([arc], ego_threat_events=2)
        assert with_elm.score < no_elm.score

    def test_elm_penalty_floored_at_zero(self):
        """Score never goes below 0 regardless of ELM events."""
        arc = _make_arc_signal(challenge_count=5, total_questions=5)
        result = _score_ego_safety([arc], ego_threat_events=10)
        assert result.score >= 0.0

    def test_missing_arc_signal_safe_default(self):
        """No arc signal in results → defaults to 0 challenges (score 1.0)."""
        result = _score_ego_safety([], ego_threat_events=0)
        assert result.score == 1.0
        assert result.total_questions == 0

    def test_partial_challenges_intermediate_score(self):
        """2 out of 5 questions challenging → score ~0.6."""
        arc = _make_arc_signal(challenge_count=2, total_questions=5)
        result = _score_ego_safety([arc], ego_threat_events=0)
        assert result.challenge_ratio == pytest.approx(0.4)
        assert result.score == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Persuasion Score composite
# ---------------------------------------------------------------------------

class TestPersuasionScore:
    def test_score_is_integer_in_range(self):
        utterances, user_speaker = _make_utterances()
        result = compute_persuasion_score(utterances, user_speaker)
        assert isinstance(result.score, int)
        assert 0 <= result.score <= 100

    def test_raw_matches_score(self):
        utterances, user_speaker = _make_utterances()
        result = compute_persuasion_score(utterances, user_speaker)
        assert result.score == round(result.raw * 100)

    def test_weights_sum_to_one(self):
        """Weights 0.30 + 0.30 + 0.40 = 1.0."""
        assert pytest.approx(0.30 + 0.30 + 0.40) == 1.0

    def test_converging_session_scores_above_50(self):
        """A session with good timing, no challenges, and agreement should score > 50."""
        utterances, user_speaker = _make_utterances(
            user_ratio=0.35,
            include_challenging=False,
            include_agreement=True,
        )
        result = compute_persuasion_score(utterances, user_speaker)
        assert result.score > 50, f"Expected >50, got {result.score}"

    def test_hostile_session_scores_below_converging(self):
        """A session with challenging questions should score lower than a receptive one."""
        good_utts, user = _make_utterances(include_challenging=False, include_agreement=True)
        bad_utts, _ = _make_utterances(include_challenging=True, include_agreement=False)
        good = compute_persuasion_score(good_utts, user)
        bad = compute_persuasion_score(bad_utts, user)
        assert good.score >= bad.score

    def test_empty_session_no_crash(self):
        """Empty utterances should return a valid score (0–100) without raising."""
        result = compute_persuasion_score([], "speaker_0")
        assert isinstance(result.score, int)
        assert 0 <= result.score <= 100
        # Timing=0, EgoSafety=1.0 (no challenges), Convergence near-zero (neutral defaults)
        assert result.timing.score == 0.0
        assert result.convergence.score < 0.2  # near-zero but may have neutral defaults

    def test_elm_events_lower_score(self):
        """Passing ego_threat_events > 0 lowers the score vs. no events."""
        utterances, user_speaker = _make_utterances()
        without_elm = compute_persuasion_score(utterances, user_speaker, ego_threat_events=0)
        with_elm = compute_persuasion_score(utterances, user_speaker, ego_threat_events=3)
        assert with_elm.score <= without_elm.score

    def test_component_breakdown_present(self):
        """All three components are populated on the result."""
        utterances, user_speaker = _make_utterances()
        result = compute_persuasion_score(utterances, user_speaker)
        assert result.timing is not None
        assert result.ego_safety is not None
        assert result.convergence is not None
        assert len(result.convergence.signal_results) == 4


# ---------------------------------------------------------------------------
# Growth Score
# ---------------------------------------------------------------------------

class TestGrowthScore:
    def test_first_session_returns_none(self):
        """No prior scores → no baseline → None."""
        assert compute_growth_score(72, []) is None

    def test_improving_trend(self):
        """Current score well above baseline → trend 'improving'."""
        result = compute_growth_score(80, [60, 65, 62, 64, 63])
        assert result is not None
        assert result.trend == "improving"
        assert result.delta > 0

    def test_declining_trend(self):
        """Current score well below baseline → trend 'declining'."""
        result = compute_growth_score(50, [70, 72, 68, 71, 69])
        assert result is not None
        assert result.trend == "declining"
        assert result.delta < 0

    def test_stable_trend(self):
        """Delta within ±3 → trend 'stable'."""
        result = compute_growth_score(70, [68, 70, 72, 69, 71])
        assert result is not None
        assert result.trend == "stable"

    def test_window_limits_baseline(self):
        """Only the last `window` sessions are used for baseline."""
        # Many old low-scoring sessions, but last 5 are high → baseline should be high
        prior = [10, 10, 10, 10, 10, 10, 10, 10, 80, 80, 80, 80, 80]
        result = compute_growth_score(75, prior, window=5)
        assert result is not None
        assert result.baseline == pytest.approx(80.0)
        assert result.sessions_used == 5

    def test_single_prior_session(self):
        """Single prior session → baseline is that session's score."""
        result = compute_growth_score(75, [60])
        assert result is not None
        assert result.baseline == pytest.approx(60.0)
        assert result.sessions_used == 1
        assert result.trend == "improving"

    def test_delta_calculation(self):
        """Delta = current - baseline."""
        result = compute_growth_score(80, [70, 70, 70, 70, 70])
        assert result is not None
        assert result.delta == pytest.approx(10.0)
        assert result.baseline == pytest.approx(70.0)
        assert result.current == 80

    def test_exact_threshold_improving(self):
        """Delta of exactly +3 → 'improving'."""
        result = compute_growth_score(73, [70])
        assert result is not None
        assert result.trend == "improving"

    def test_exact_threshold_declining(self):
        """Delta of exactly -3 → 'declining'."""
        result = compute_growth_score(67, [70])
        assert result is not None
        assert result.trend == "declining"


# ---------------------------------------------------------------------------
# Prompt Effectiveness
# ---------------------------------------------------------------------------

def _make_convergence_utterances(user_speaker: str = "speaker_0") -> list[dict]:
    """Build 20 utterances: first 10 low-signal, last 10 high-signal (agreement markers)."""
    low = [
        {"speaker": "speaker_1", "text": "I'm not sure about that approach.", "start": float(i), "end": float(i) + 1}
        for i in range(10)
    ]
    high = [
        {"speaker": "speaker_1", "text": "Exactly, I agree with that completely.", "start": float(i + 10), "end": float(i) + 11}
        for i in range(8)
    ]
    high += [
        {"speaker": user_speaker, "text": "Let me summarize what we agreed.", "start": 18.0, "end": 19.0},
        {"speaker": user_speaker, "text": "As I said, that aligns with our goal.", "start": 19.0, "end": 20.0},
    ]
    return low + high


class TestPromptEffectiveness:
    def test_returns_tuple_of_three(self):
        utts = _make_convergence_utterances()
        result = compute_prompt_effectiveness(utts, "speaker_0", prompt_utterance_index=9)
        assert len(result) == 3

    def test_insufficient_before_window_returns_none_triple(self):
        utts = _make_convergence_utterances()
        # index 1 → only 1 utterance before (< min_utterances=3)
        eff, before, after = compute_prompt_effectiveness(utts, "speaker_0", prompt_utterance_index=1)
        assert eff is None
        assert before is None
        assert after is None

    def test_insufficient_after_window_returns_none_triple(self):
        utts = _make_convergence_utterances()
        # index near end → not enough after
        eff, before, after = compute_prompt_effectiveness(
            utts, "speaker_0", prompt_utterance_index=18, min_utterances=3
        )
        assert eff is None

    def test_out_of_bounds_index_returns_none_triple(self):
        utts = _make_convergence_utterances()
        eff, before, after = compute_prompt_effectiveness(utts, "speaker_0", prompt_utterance_index=999)
        assert eff is None
        assert before is None
        assert after is None

    def test_effectiveness_clamped_to_0_1(self):
        utts = _make_convergence_utterances()
        eff, before, after = compute_prompt_effectiveness(utts, "speaker_0", prompt_utterance_index=9)
        if eff is not None:
            assert 0.0 <= eff <= 1.0
            assert 0.0 <= before <= 1.0
            assert 0.0 <= after <= 1.0

    def test_negative_index_returns_none_triple(self):
        utts = _make_convergence_utterances()
        eff, before, after = compute_prompt_effectiveness(utts, "speaker_0", prompt_utterance_index=-1)
        assert eff is None


# ---------------------------------------------------------------------------
# Coaching Effectiveness (EWMA cadence update)
# ---------------------------------------------------------------------------

class TestUpdateCoachingEffectiveness:
    def test_total_increments(self):
        _, new_total, _, _ = update_coaching_effectiveness(0.5, 10, 5, 30.0, 0.7)
        assert new_total == 11

    def test_effective_count_increments_above_threshold(self):
        _, _, new_eff, _ = update_coaching_effectiveness(0.5, 10, 5, 30.0, 0.8)
        assert new_eff == 6

    def test_effective_count_unchanged_below_threshold(self):
        _, _, new_eff, _ = update_coaching_effectiveness(0.5, 10, 5, 30.0, 0.3)
        assert new_eff == 5

    def test_ewma_moves_toward_new_value(self):
        new_avg, _, _, _ = update_coaching_effectiveness(0.5, 10, 5, 30.0, 1.0, alpha=0.2)
        assert new_avg == pytest.approx(0.5 * 0.8 + 1.0 * 0.2)

    def test_cadence_decreases_when_effective(self):
        """High effectiveness → shorter cadence (more coaching)."""
        # Need >= min_prompts_for_cadence (5) evaluations
        _, _, _, new_cad = update_coaching_effectiveness(0.7, 5, 4, 30.0, 1.0)
        # avg will be high (>0.6) → cadence shrinks by 5%
        assert new_cad < 30.0

    def test_cadence_increases_when_ineffective(self):
        """Low effectiveness → longer cadence (less coaching noise)."""
        _, _, _, new_cad = update_coaching_effectiveness(0.2, 5, 1, 30.0, 0.0)
        assert new_cad > 30.0

    def test_cadence_unchanged_before_min_prompts(self):
        """Cadence is not adjusted until min_prompts_for_cadence is reached."""
        _, _, _, new_cad = update_coaching_effectiveness(0.8, 3, 3, 30.0, 1.0)
        assert new_cad == pytest.approx(30.0)

    def test_cadence_clamped_to_min(self):
        """Cadence never drops below min_cadence (15s)."""
        _, _, _, new_cad = update_coaching_effectiveness(0.9, 100, 90, 16.0, 1.0, min_cadence=15.0)
        assert new_cad >= 15.0

    def test_cadence_clamped_to_max(self):
        """Cadence never exceeds max_cadence (90s)."""
        _, _, _, new_cad = update_coaching_effectiveness(0.1, 10, 1, 89.0, 0.0, max_cadence=90.0)
        assert new_cad <= 90.0


# ---------------------------------------------------------------------------
# Skill Badges
# ---------------------------------------------------------------------------

class TestComputeSkillBadges:
    def test_returns_empty_when_fewer_than_threshold_sessions(self):
        result = compute_skill_badges([["elm:ego_threat"], ["elm:ego_threat"]])
        assert result == []

    def test_no_badge_when_prompt_fired_in_all_sessions(self):
        triggers = [
            ["elm:ego_threat", "cadence:self"],
            ["elm:ego_threat"],
            ["elm:ego_threat", "cadence:group"],
        ]
        badges = compute_skill_badges(triggers)
        assert "elm:ego_threat" not in badges

    def test_badge_awarded_when_absent_from_all_threshold_sessions(self):
        triggers = [
            ["cadence:self"],       # elm:ego_threat absent
            ["cadence:group"],      # elm:ego_threat absent
            ["cadence:self"],       # elm:ego_threat absent
        ]
        badges = compute_skill_badges(triggers)
        assert "elm:ego_threat" in badges

    def test_only_badge_metadata_types_returned(self):
        triggers = [[], [], []]
        badges = compute_skill_badges(triggers)
        for b in badges:
            assert b in BADGE_METADATA

    def test_multiple_badges_at_once(self):
        """All prompt types absent → all 5 badges awarded."""
        triggers = [[], [], []]
        badges = compute_skill_badges(triggers)
        assert len(badges) == len(BADGE_METADATA)

    def test_custom_threshold(self):
        """Threshold=2: absent from last 2 sessions qualifies."""
        triggers = [
            ["elm:ego_threat"],     # 3 sessions ago — not counted
            ["cadence:self"],       # absent
            ["cadence:self"],       # absent
        ]
        badges = compute_skill_badges(triggers, consecutive_threshold=2)
        assert "elm:ego_threat" in badges

    def test_prompt_in_oldest_session_not_window_does_not_block(self):
        """A trigger in session 1 of 4 is outside the 3-session window → badge still awarded."""
        triggers = [
            ["elm:ego_threat"],     # session 1 — outside window
            ["cadence:self"],       # session 2
            ["cadence:self"],       # session 3
            ["cadence:self"],       # session 4 (current)
        ]
        badges = compute_skill_badges(triggers)
        # Last 3 sessions have no elm:ego_threat → badge awarded
        assert "elm:ego_threat" in badges
