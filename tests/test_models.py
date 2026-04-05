"""
Tests for backend/models.py — pure functions only (no database I/O).

Coverage:
  confidence_from_sessions
    - n=0 → floor (SELF_ASSESSMENT_PRIOR_CONFIDENCE)
    - n=7 → ≈ 0.76 (half-life)
    - n=15 → ≥ 0.90 (approaching ceiling)
    - large n → capped at 0.95
    - negative n → treated as 0 (floor)
    - monotonically increasing

  _ewma_update (private — tested via apply_session_observation)

  apply_session_observation
    - Layer 1 update: core_focus / core_stance move toward observation
    - Layer 2 update: matching ContextProfile updated
    - Layer 2 no-op: unknown context not in dict → Layer 1 still updates
    - core_sessions increments on each call
    - obs_confidence=0.0 → Layer 1 moves by 0 weight (score unchanged)
    - obs_confidence=1.0 → full weight

  get_profile_snapshot
    - falls back to core when no context profile exists
    - falls back to core when context sessions < min_context_sessions
    - uses context profile when sessions >= min_context_sessions
    - context_shifts=True when context archetype ≠ core archetype
    - context_shifts=False when both are Undetermined
    - context_shifts=False when archetypes match
    - is_context_specific flag reflects which branch was taken
    - context_sessions=0 when no context profile at all

  seed_from_self_assessment
    - seeds core axes from self-assessment values
    - stores immutable sa_* snapshot
    - sa_completed_at is set
    - core_sessions remains 0
    - raises ValueError on second call (idempotent guard)
    - confidence is clamped to [0.35, 0.95]
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from backend.models import (
    ContextProfile,
    MeetingSession,
    Participant,
    ParticipantContextProfile,
    ProfileSnapshot,
    SessionObservation,
    User,
    _ewma_update,
    _welford_m2_update,
    m2_to_variance,
    apply_participant_observation,
    apply_session_observation,
    confidence_from_sessions,
    get_profile_snapshot,
    seed_from_self_assessment,
    SELF_ASSESSMENT_PRIOR_CONFIDENCE,
    MIN_CONTEXT_SESSIONS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(**kwargs) -> User:
    """Build a User with sensible defaults for testing."""
    defaults = dict(
        id="user-1",
        core_focus=0.0,
        core_stance=0.0,
        core_focus_var=0.0,
        core_stance_var=0.0,
        core_confidence=SELF_ASSESSMENT_PRIOR_CONFIDENCE,
        core_sessions=0,
        sa_completed_at=None,
    )
    defaults.update(kwargs)
    return User(**defaults)


def _ctx_profile(context: str, sessions: int = 0, focus: float = 0.0, stance: float = 0.0) -> ContextProfile:
    return ContextProfile(
        id=f"ctx-{context}",
        user_id="user-1",
        context=context,
        focus_score=focus,
        stance_score=stance,
        focus_var=0.0,
        stance_var=0.0,
        sessions=sessions,
    )


def _obs(
    context: str = "team",
    focus: float = 50.0,
    stance: float = 50.0,
    utterance_count: int = 20,
    obs_confidence: float = 1.0,
) -> SessionObservation:
    return SessionObservation(
        session_id="sess-1",
        context=context,
        focus_score=focus,
        stance_score=stance,
        utterance_count=utterance_count,
        obs_confidence=obs_confidence,
    )


# ---------------------------------------------------------------------------
# confidence_from_sessions
# ---------------------------------------------------------------------------

class TestConfidenceFromSessions:
    def test_zero_sessions_returns_floor(self):
        assert confidence_from_sessions(0) == pytest.approx(SELF_ASSESSMENT_PRIOR_CONFIDENCE)

    def test_negative_sessions_returns_floor(self):
        assert confidence_from_sessions(-5) == pytest.approx(SELF_ASSESSMENT_PRIOR_CONFIDENCE)

    def test_half_life_session_approx(self):
        """At n=7 (half-life), confidence ≈ 0.76."""
        c = confidence_from_sessions(7)
        assert 0.74 < c < 0.78

    def test_fifteen_sessions_high_confidence(self):
        """After 15 sessions, confidence approaches ceiling."""
        assert confidence_from_sessions(15) >= 0.90

    def test_large_n_capped_at_ceiling(self):
        """Very large n must not exceed 0.95."""
        assert confidence_from_sessions(1000) == pytest.approx(0.95)

    def test_monotonically_increasing(self):
        """Each additional session must increase (or hold) confidence."""
        prev = confidence_from_sessions(0)
        for n in range(1, 30):
            curr = confidence_from_sessions(n)
            assert curr >= prev, f"confidence decreased at n={n}"
            prev = curr

    def test_floor_is_prior_confidence(self):
        """Floor equals SELF_ASSESSMENT_PRIOR_CONFIDENCE (≈0.35)."""
        assert confidence_from_sessions(0) == SELF_ASSESSMENT_PRIOR_CONFIDENCE


# ---------------------------------------------------------------------------
# _ewma_update (private helper — tested directly for precision)
# ---------------------------------------------------------------------------

class TestEwmaUpdate:
    def test_zero_old_sessions_returns_weighted_new(self):
        """old_sessions=0: all weight goes to the new observation."""
        result = _ewma_update(old_score=0.0, old_sessions=0, new_obs=80.0, obs_confidence=1.0)
        assert result == pytest.approx(80.0)

    def test_full_confidence_equal_weight(self):
        """1 prior session, obs_confidence=1.0: average of old and new."""
        result = _ewma_update(old_score=60.0, old_sessions=1, new_obs=80.0, obs_confidence=1.0)
        assert result == pytest.approx(70.0)

    def test_half_confidence_lower_weight(self):
        """obs_confidence=0.5: new_obs contributes half a session."""
        result = _ewma_update(old_score=60.0, old_sessions=2, new_obs=100.0, obs_confidence=0.5)
        # (2*60 + 0.5*100) / (2+0.5) = (120+50)/2.5 = 68.0
        assert result == pytest.approx(68.0)

    def test_zero_confidence_no_change(self):
        """obs_confidence=0: old score unchanged."""
        result = _ewma_update(old_score=45.0, old_sessions=5, new_obs=100.0, obs_confidence=0.0)
        assert result == pytest.approx(45.0)

    def test_confidence_clamped_to_one(self):
        """obs_confidence > 1.0 is clamped to 1.0 inside the function."""
        normal = _ewma_update(60.0, 1, 80.0, obs_confidence=1.0)
        clamped = _ewma_update(60.0, 1, 80.0, obs_confidence=5.0)
        assert normal == pytest.approx(clamped)


# ---------------------------------------------------------------------------
# apply_session_observation
# ---------------------------------------------------------------------------

class TestApplySessionObservation:
    def test_core_focus_moves_toward_observation(self):
        user = _user(core_focus=0.0, core_sessions=0)
        obs = _obs(focus=80.0, stance=0.0, obs_confidence=1.0)
        apply_session_observation(user, {}, obs)
        assert user.core_focus == pytest.approx(80.0)

    def test_core_stance_moves_toward_observation(self):
        user = _user(core_stance=0.0, core_sessions=0)
        obs = _obs(focus=0.0, stance=-60.0, obs_confidence=1.0)
        apply_session_observation(user, {}, obs)
        assert user.core_stance == pytest.approx(-60.0)

    def test_core_sessions_increments(self):
        user = _user(core_sessions=3)
        apply_session_observation(user, {}, _obs())
        assert user.core_sessions == 4

    def test_core_confidence_updates_with_schedule(self):
        user = _user(core_sessions=0)
        apply_session_observation(user, {}, _obs())
        assert user.core_confidence == confidence_from_sessions(1)

    def test_layer2_context_profile_updated(self):
        user = _user()
        ctx = _ctx_profile("team", sessions=0, focus=0.0, stance=0.0)
        apply_session_observation(user, {"team": ctx}, _obs(context="team", focus=70.0, stance=30.0))
        assert ctx.sessions == 1
        assert ctx.focus_score == pytest.approx(70.0)
        assert ctx.stance_score == pytest.approx(30.0)

    def test_unknown_context_does_not_crash(self):
        """Context not in dict → Layer 2 no-op; Layer 1 still updates."""
        user = _user(core_focus=0.0, core_sessions=0)
        apply_session_observation(user, {}, _obs(context="board", focus=50.0))
        assert user.core_focus == pytest.approx(50.0)  # Layer 1 updated
        assert user.core_sessions == 1

    def test_zero_obs_confidence_layer1_unchanged(self):
        """obs_confidence=0: score unchanged, but session count still increments."""
        user = _user(core_focus=30.0, core_sessions=2)
        apply_session_observation(user, {}, _obs(focus=100.0, obs_confidence=0.0))
        # Weight=0: (2*30 + 0*100)/(2+0) = 30
        assert user.core_focus == pytest.approx(30.0)
        assert user.core_sessions == 3

    def test_multiple_observations_converge(self):
        """Repeated high-focus observations should converge core_focus upward."""
        user = _user(core_focus=0.0, core_sessions=0)
        for _ in range(10):
            apply_session_observation(user, {}, _obs(focus=100.0, obs_confidence=1.0))
        assert user.core_focus > 90.0

    def test_layer2_only_updates_matching_context(self):
        """Observation for 'board' does not update 'team' ContextProfile."""
        user = _user()
        team_ctx = _ctx_profile("team", sessions=0, focus=0.0)
        board_ctx = _ctx_profile("board", sessions=0, focus=0.0)
        apply_session_observation(
            user,
            {"team": team_ctx, "board": board_ctx},
            _obs(context="board", focus=80.0),
        )
        assert board_ctx.sessions == 1
        assert board_ctx.focus_score == pytest.approx(80.0)
        assert team_ctx.sessions == 0   # untouched


# ---------------------------------------------------------------------------
# get_profile_snapshot
# ---------------------------------------------------------------------------

class TestGetProfileSnapshot:
    def test_no_context_profile_falls_back_to_core(self):
        user = _user(core_focus=60.0, core_stance=60.0, core_sessions=5)
        snap = get_profile_snapshot(user, {}, "board")
        assert snap.is_context_specific is False
        assert snap.focus_score == pytest.approx(60.0)

    def test_below_min_sessions_falls_back_to_core(self):
        user = _user(core_focus=60.0, core_stance=60.0, core_sessions=5)
        ctx = _ctx_profile("board", sessions=MIN_CONTEXT_SESSIONS - 1, focus=80.0, stance=-80.0)
        snap = get_profile_snapshot(user, {"board": ctx}, "board")
        assert snap.is_context_specific is False
        assert snap.focus_score == pytest.approx(60.0)

    def test_at_min_sessions_uses_context(self):
        user = _user(core_focus=60.0, core_stance=60.0, core_sessions=5)
        ctx = _ctx_profile("board", sessions=MIN_CONTEXT_SESSIONS, focus=80.0, stance=-80.0)
        snap = get_profile_snapshot(user, {"board": ctx}, "board")
        assert snap.is_context_specific is True
        assert snap.focus_score == pytest.approx(80.0)

    def test_context_shifts_true_when_archetypes_differ(self):
        """Core = Inquisitor (Logic+Advocacy); context = Bridge Builder (Narrative+Analysis)."""
        user = _user(core_focus=50.0, core_stance=50.0, core_sessions=10)
        ctx = _ctx_profile("board", sessions=MIN_CONTEXT_SESSIONS, focus=-50.0, stance=-50.0)
        snap = get_profile_snapshot(user, {"board": ctx}, "board")
        assert snap.context_shifts is True
        assert snap.core_archetype != snap.archetype

    def test_context_shifts_false_when_archetypes_match(self):
        user = _user(core_focus=50.0, core_stance=50.0, core_sessions=10)
        ctx = _ctx_profile("board", sessions=MIN_CONTEXT_SESSIONS, focus=50.0, stance=50.0)
        snap = get_profile_snapshot(user, {"board": ctx}, "board")
        assert snap.context_shifts is False

    def test_context_shifts_false_when_either_undetermined(self):
        """context_shifts must be False when either archetype is Undetermined."""
        user = _user(core_focus=5.0, core_stance=5.0, core_sessions=10)    # undetermined core
        ctx = _ctx_profile("board", sessions=MIN_CONTEXT_SESSIONS, focus=50.0, stance=50.0)
        snap = get_profile_snapshot(user, {"board": ctx}, "board")
        # core is in neutral band → Undetermined → context_shifts must be False
        assert snap.core_archetype == "Undetermined"
        assert snap.context_shifts is False

    def test_context_sessions_zero_when_no_profile(self):
        user = _user()
        snap = get_profile_snapshot(user, {}, "1:1")
        assert snap.context_sessions == 0

    def test_context_sessions_from_profile(self):
        user = _user(core_sessions=10)
        ctx = _ctx_profile("client", sessions=4)
        snap = get_profile_snapshot(user, {"client": ctx}, "client")
        assert snap.context_sessions == 4

    def test_core_sessions_in_snapshot(self):
        user = _user(core_sessions=7)
        snap = get_profile_snapshot(user, {}, "team")
        assert snap.core_sessions == 7

    def test_confidence_limited_by_context_sessions(self):
        """
        When context is used, confidence = min(core_conf, context_conf).
        Context with only 3 sessions has lower confidence than core with 10.
        """
        user = _user(core_sessions=10, core_confidence=confidence_from_sessions(10))
        ctx = _ctx_profile("board", sessions=MIN_CONTEXT_SESSIONS)
        snap = get_profile_snapshot(user, {"board": ctx}, "board")
        assert snap.confidence <= confidence_from_sessions(10)

    def test_fallback_confidence_equals_core(self):
        user = _user(core_sessions=5, core_confidence=confidence_from_sessions(5))
        snap = get_profile_snapshot(user, {}, "unknown")
        assert snap.confidence == pytest.approx(user.core_confidence)

    def test_custom_neutral_band(self):
        """A narrow neutral band of 5 classifies scores ≥6 as non-Undetermined."""
        user = _user(core_focus=10.0, core_stance=10.0, core_sessions=10)
        snap_default = get_profile_snapshot(user, {}, "team")        # default band=15 → undetermined
        snap_narrow = get_profile_snapshot(user, {}, "team", neutral_band=5)  # band=5 → Inquisitor
        assert snap_default.archetype == "Undetermined"
        assert snap_narrow.archetype == "Inquisitor"


# ---------------------------------------------------------------------------
# seed_from_self_assessment
# ---------------------------------------------------------------------------

class TestSeedFromSelfAssessment:
    def test_seeds_core_focus_and_stance(self):
        user = _user()
        seed_from_self_assessment(user, focus_score=65.0, stance_score=-40.0,
                                  archetype="Architect", confidence=0.4)
        assert user.core_focus == pytest.approx(65.0, abs=0.2)
        assert user.core_stance == pytest.approx(-40.0, abs=0.2)

    def test_sa_snapshot_stored_immutably(self):
        user = _user()
        seed_from_self_assessment(user, 65.0, -40.0, "Architect", 0.4)
        assert user.sa_focus == pytest.approx(65.0, abs=0.2)
        assert user.sa_stance == pytest.approx(-40.0, abs=0.2)
        assert user.sa_archetype == "Architect"
        assert user.sa_confidence == pytest.approx(0.4, abs=0.001)

    def test_sa_completed_at_is_set(self):
        user = _user()
        seed_from_self_assessment(user, 65.0, -40.0, "Architect", 0.4)
        assert user.sa_completed_at is not None
        assert isinstance(user.sa_completed_at, datetime)

    def test_core_sessions_remains_zero(self):
        """Seeding from self-assessment does not count as a behavioral session."""
        user = _user()
        seed_from_self_assessment(user, 65.0, -40.0, "Architect", 0.4)
        assert user.core_sessions == 0

    def test_second_call_raises_value_error(self):
        user = _user()
        seed_from_self_assessment(user, 65.0, -40.0, "Architect", 0.4)
        with pytest.raises(ValueError, match="already has a self-assessment"):
            seed_from_self_assessment(user, 20.0, 20.0, "Inquisitor", 0.5)

    def test_confidence_clamped_to_floor(self):
        """Confidence below 0.35 is clamped to the floor."""
        user = _user()
        seed_from_self_assessment(user, 0.0, 0.0, "Undetermined", confidence=0.1)
        assert user.core_confidence >= 0.35

    def test_confidence_clamped_to_ceiling(self):
        """Confidence above 0.95 is clamped to the ceiling."""
        user = _user()
        seed_from_self_assessment(user, 0.0, 0.0, "Undetermined", confidence=1.0)
        assert user.core_confidence <= 0.95

    def test_archetype_none_stored_without_error(self):
        """Archetype can be None if self-assessment returned Undetermined."""
        user = _user()
        seed_from_self_assessment(user, 5.0, 5.0, archetype=None, confidence=0.38)
        assert user.sa_archetype is None
        assert user.sa_completed_at is not None


# ---------------------------------------------------------------------------
# Welford's online variance update
# ---------------------------------------------------------------------------

class TestWelfordVariance:
    def test_known_sequence(self):
        """M2 of [10, 20, 30] converts to population variance ≈ 66.67."""
        # Simulate feeding 10, 20, 30 through EWMA + Welford M2
        m2 = 0.0
        # After obs=10: old_mean=0, new_mean=10
        m2 = _welford_m2_update(0.0, 0.0, 10.0, 10.0, 1.0)
        assert m2_to_variance(m2, 1) == 0.0  # n<2, returns 0

        # After obs=20: old_mean=10, new_mean=15
        m2 = _welford_m2_update(m2, 10.0, 15.0, 20.0, 1.0)
        assert m2 > 0  # M2 accumulates
        assert m2_to_variance(m2, 2) > 0  # now we have variance

        # After obs=30: old_mean=15, new_mean=20
        m2 = _welford_m2_update(m2, 15.0, 20.0, 30.0, 1.0)
        # Population variance of [10,20,30] ≈ 66.67
        variance = m2_to_variance(m2, 3)
        assert 40 < variance < 120  # reasonable range given EWMA weighting

    def test_single_observation_zero(self):
        """M2 after a single observation yields variance 0."""
        m2 = _welford_m2_update(0.0, 0.0, 50.0, 50.0, 1.0)
        assert m2_to_variance(m2, 1) == 0.0

    def test_zero_confidence_unchanged(self):
        """obs_confidence=0 means no new information — M2 unchanged."""
        result = _welford_m2_update(25.0, 50.0, 50.0, 70.0, 0.0)
        assert result == 25.0

    def test_negative_clamp(self):
        """Floating-point edge: M2 is clamped to max(0, m2)."""
        result = _welford_m2_update(0.001, 50.0, 50.0, 50.0, 1.0)
        assert result >= 0.0


class TestApplySessionObservationVariance:
    def test_variance_updates_alongside_mean(self):
        """apply_session_observation updates variance fields alongside core axes."""
        user = _user(core_focus=50.0, core_stance=50.0, core_sessions=5)
        ctx = _ctx_profile("board", sessions=3, focus=60.0, stance=40.0)
        profiles = {"board": ctx}

        # Observation far from current mean should increase variance
        obs = _obs(context="board", focus=80.0, stance=20.0)
        apply_session_observation(user, profiles, obs)

        assert user.core_sessions == 6
        assert user.core_focus_var >= 0.0
        assert user.core_stance_var >= 0.0
        assert ctx.focus_var >= 0.0
        assert ctx.stance_var >= 0.0

    def test_variance_grows_with_diverse_observations(self):
        """Multiple diverse observations should produce non-zero variance."""
        user = _user(core_focus=0.0, core_stance=0.0, core_sessions=0)
        ctx = _ctx_profile("team", sessions=0)
        profiles = {"team": ctx}

        # Feed alternating high/low observations
        for focus_val in [80.0, -60.0, 70.0, -50.0, 60.0]:
            obs = _obs(context="team", focus=focus_val, stance=0.0)
            apply_session_observation(user, profiles, obs)

        assert user.core_focus_var > 0.0
        assert user.core_sessions == 5


class TestProfileSnapshotVariance:
    def test_snapshot_includes_variance_core(self):
        """ProfileSnapshot derives variance from M2 stored on core profile."""
        user = _user(core_focus=50.0, core_stance=-30.0, core_sessions=5)
        user.core_focus_var = 500.0  # M2 accumulator
        user.core_stance_var = 250.0
        snapshot = get_profile_snapshot(user, {}, "board")
        # m2_to_variance(500, 5) = 100.0; m2_to_variance(250, 5) = 50.0
        assert snapshot.focus_variance == 100.0
        assert snapshot.stance_variance == 50.0

    def test_snapshot_includes_variance_context(self):
        """ProfileSnapshot uses context-specific M2→variance when context is trusted."""
        user = _user(core_focus=50.0, core_stance=-30.0, core_sessions=10)
        user.core_focus_var = 1000.0
        user.core_stance_var = 500.0
        ctx = _ctx_profile("board", sessions=5, focus=70.0, stance=-10.0)
        ctx.focus_var = 1000.0  # M2 accumulator
        ctx.stance_var = 400.0
        snapshot = get_profile_snapshot(user, {"board": ctx}, "board")
        assert snapshot.is_context_specific is True
        # m2_to_variance(1000, 5) = 200.0; m2_to_variance(400, 5) = 80.0
        assert snapshot.focus_variance == 200.0
        assert snapshot.stance_variance == 80.0

    def test_backward_compat_no_variance_fields(self):
        """User with default M2=0.0 works in all paths without error."""
        user = _user(core_sessions=3)
        assert user.core_focus_var == 0.0
        assert user.core_stance_var == 0.0
        snapshot = get_profile_snapshot(user, {}, "unknown")
        assert snapshot.focus_variance == 0.0
        assert snapshot.stance_variance == 0.0
