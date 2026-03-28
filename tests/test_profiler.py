"""
Tests for backend/profiler.py — Participant Superpower profiler + User behavioral observer.

Coverage:
  Signal detection (_score_utterance)
    - Logic signals detected (numbers, evidence language, connectives)
    - Narrative signals detected (story, emotion, metaphor)
    - Advocacy signals detected (we should, let's, I recommend)
    - Analysis signals detected (questions, exploratory language)
    - Question marks count as analysis hits
    - Utterance with no signals → all zeros
    - Signals accumulate (multiple hits per utterance)

  Signal aggregation (_aggregate_signals)
    - Empty list → (0.0, 0.0, 0.0)
    - Pure logic → focus_score = +100
    - Pure narrative → focus_score = -100
    - Pure advocacy → stance_score = +100
    - Pure analysis → stance_score = -100
    - Balanced signals → scores near 0
    - Confidence grows with utterance count
    - Confidence grows with signal density
    - Confidence capped at 0.9

  Observation confidence (_obs_confidence)
    - 0 utterances → 0.0
    - Positive utterances → positive and growing confidence
    - ~36 utterances → ≈ 0.95 (ceiling)
    - Monotonically increasing

  ParticipantProfiler
    - Single strong-logic utterance → Architect or Inquisitor (focus > 0)
    - Single strong-narrative utterance → Firestarter or Bridge Builder (focus < 0)
    - Inquisitor: logic + advocacy combination
    - Firestarter: narrative + advocacy combination
    - Architect: logic + analysis combination
    - Bridge Builder: narrative + analysis combination
    - Window size: old utterances evicted at window_size + 1
    - Carry-forward: classification available after 1 utterance
    - Multiple speakers tracked independently
    - get_classification returns None for unseen speaker
    - reset clears all windows
    - all_classifications returns all speakers
    - utterance_count tracks window size, not total utterances

  UserBehaviorObserver
    - Non-user utterances ignored
    - User utterances processed
    - Empty session → obs_confidence = 0.0, focus = 0, stance = 0
    - utterance_count tracks only user utterances
    - get_observation returns correct session_id and context
    - obs_confidence grows with user utterances
    - Logic-heavy user utterances → focus_score > 0
    - Advocacy-heavy user utterances → stance_score > 0
    - reset clears all signals
"""

from __future__ import annotations

import pytest

from backend.profiler import (
    ParticipantProfiler,
    UserBehaviorObserver,
    WindowClassification,
    UtteranceSignals,
    _aggregate_signals,
    _obs_confidence,
    _score_utterance,
)
from backend.models import SessionObservation


# ---------------------------------------------------------------------------
# Sample utterances — designed to trigger specific signals clearly
# ---------------------------------------------------------------------------

# High logic, no narrative
_LOGIC_UTT = (
    "The data shows a 47% improvement in conversion rate. "
    "The evidence from our analysis is clear — therefore we should look at the metrics carefully."
)

# High narrative, no logic
_NARRATIVE_UTT = (
    "Imagine what this journey could look like for our team. "
    "I remember when we first started — the excitement and passion were inspiring."
)

# High advocacy, no analysis
_ADVOCACY_UTT = (
    "We should move forward immediately. Let's commit to this plan — we need to act now."
)

# High analysis, no advocacy  (questions + exploratory language)
_ANALYSIS_UTT = (
    "What do you think about this approach? I'm curious — have you considered the alternatives? "
    "Perhaps there are other perspectives worth exploring?"
)

# Logic + Advocacy → Inquisitor
_INQUISITOR_UTT = (
    "The data clearly supports this. We need to act on the evidence — I recommend we commit to the metrics-driven approach."
)

# Narrative + Advocacy → Firestarter
_FIRESTARTER_UTT = (
    "Imagine the impact we could have! Let's move forward with this vision. "
    "I believe we should inspire the team and lead this journey together."
)

# Logic + Analysis → Architect
_ARCHITECT_UTT = (
    "The analysis shows three specific data points. What do you think the research indicates? "
    "I'd like to understand the evidence before we proceed."
)

# Narrative + Analysis → Bridge Builder
_BRIDGE_UTT = (
    "Picture how the team might experience this journey. "
    "What does everyone think? I'm curious about different perspectives and feedback."
)

# Neutral — no clear signals
_NEUTRAL_UTT = "Okay. Got it. Thanks. Sure. Understood."


# ---------------------------------------------------------------------------
# _score_utterance
# ---------------------------------------------------------------------------

class TestScoreUtterance:
    def test_logic_signals_detected(self):
        s = _score_utterance(_LOGIC_UTT)
        assert s.logic >= 3, f"Expected ≥3 logic hits, got {s.logic}"

    def test_narrative_signals_detected(self):
        s = _score_utterance(_NARRATIVE_UTT)
        assert s.narrative >= 3, f"Expected ≥3 narrative hits, got {s.narrative}"

    def test_advocacy_signals_detected(self):
        s = _score_utterance(_ADVOCACY_UTT)
        assert s.advocacy >= 2, f"Expected ≥2 advocacy hits, got {s.advocacy}"

    def test_analysis_signals_detected(self):
        s = _score_utterance(_ANALYSIS_UTT)
        assert s.analysis >= 3, f"Expected ≥3 analysis hits, got {s.analysis}"

    def test_question_marks_count_as_analysis(self):
        s_no_q = _score_utterance("Perhaps we should consider this")
        s_with_q = _score_utterance("Perhaps we should consider this?")
        assert s_with_q.analysis > s_no_q.analysis

    def test_neutral_utterance_all_zeros_or_low(self):
        s = _score_utterance(_NEUTRAL_UTT)
        assert s.total <= 2, f"Expected sparse signals for neutral text, got {s.total}"

    def test_returns_utterance_signals_type(self):
        s = _score_utterance("hello")
        assert isinstance(s, UtteranceSignals)

    def test_logic_not_in_narrative_text(self):
        s = _score_utterance("I remember when we felt so excited and inspired.")
        assert s.logic < s.narrative

    def test_total_property(self):
        s = UtteranceSignals(logic=2, narrative=1, advocacy=3, analysis=1)
        assert s.total == 7

    def test_number_triggers_logic(self):
        s = _score_utterance("We achieved 85% efficiency.")
        assert s.logic >= 1

    def test_percentage_triggers_logic(self):
        s = _score_utterance("Revenue grew 23 percent.")
        assert s.logic >= 1

    def test_we_should_triggers_advocacy(self):
        s = _score_utterance("We should move forward with the plan.")
        assert s.advocacy >= 1

    def test_lets_triggers_advocacy(self):
        s = _score_utterance("Let's commit to this decision.")
        assert s.advocacy >= 1

    def test_have_you_considered_triggers_analysis(self):
        s = _score_utterance("Have you considered the alternative approach?")
        assert s.analysis >= 1

    def test_imagine_triggers_narrative(self):
        s = _score_utterance("Imagine where we could be in five years.")
        assert s.narrative >= 1


# ---------------------------------------------------------------------------
# _aggregate_signals
# ---------------------------------------------------------------------------

class TestAggregateSignals:
    def test_empty_list_returns_zeros(self):
        focus, stance, conf = _aggregate_signals([])
        assert focus == 0.0
        assert stance == 0.0
        assert conf == 0.0

    def test_pure_logic_focus_near_100(self):
        signals = [UtteranceSignals(logic=5, narrative=0, advocacy=0, analysis=0)]
        focus, _, _ = _aggregate_signals(signals)
        assert focus == pytest.approx(100.0)

    def test_pure_narrative_focus_near_minus_100(self):
        signals = [UtteranceSignals(logic=0, narrative=5, advocacy=0, analysis=0)]
        focus, _, _ = _aggregate_signals(signals)
        assert focus == pytest.approx(-100.0)

    def test_pure_advocacy_stance_near_100(self):
        signals = [UtteranceSignals(logic=0, narrative=0, advocacy=5, analysis=0)]
        _, stance, _ = _aggregate_signals(signals)
        assert stance == pytest.approx(100.0)

    def test_pure_analysis_stance_near_minus_100(self):
        signals = [UtteranceSignals(logic=0, narrative=0, advocacy=0, analysis=5)]
        _, stance, _ = _aggregate_signals(signals)
        assert stance == pytest.approx(-100.0)

    def test_balanced_focus_near_zero(self):
        signals = [UtteranceSignals(logic=3, narrative=3, advocacy=0, analysis=0)]
        focus, _, _ = _aggregate_signals(signals)
        assert focus == pytest.approx(0.0)

    def test_confidence_grows_with_utterance_count(self):
        single = [UtteranceSignals(logic=2, narrative=0, advocacy=2, analysis=0)]
        five = single * 5
        _, _, conf_1 = _aggregate_signals(single)
        _, _, conf_5 = _aggregate_signals(five)
        assert conf_5 > conf_1

    def test_confidence_grows_with_signal_density(self):
        sparse = [UtteranceSignals(logic=1, narrative=0, advocacy=0, analysis=0)] * 3
        rich = [UtteranceSignals(logic=5, narrative=0, advocacy=5, analysis=0)] * 3
        _, _, conf_sparse = _aggregate_signals(sparse)
        _, _, conf_rich = _aggregate_signals(rich)
        assert conf_rich > conf_sparse

    def test_confidence_capped_at_0_9(self):
        # Many utterances with many signals
        signals = [UtteranceSignals(logic=10, narrative=0, advocacy=10, analysis=0)] * 20
        _, _, conf = _aggregate_signals(signals)
        assert conf <= 0.9

    def test_no_focus_signals_returns_zero_focus(self):
        signals = [UtteranceSignals(logic=0, narrative=0, advocacy=3, analysis=0)]
        focus, stance, _ = _aggregate_signals(signals)
        assert focus == 0.0
        assert stance == pytest.approx(100.0)

    def test_scores_rounded_to_one_decimal(self):
        signals = [UtteranceSignals(logic=2, narrative=1, advocacy=0, analysis=0)]
        focus, stance, _ = _aggregate_signals(signals)
        # (2-1)/(2+1)*100 = 33.33... → rounded to 33.3
        assert focus == pytest.approx(33.3, abs=0.1)


# ---------------------------------------------------------------------------
# _obs_confidence
# ---------------------------------------------------------------------------

class TestObsConfidence:
    def test_zero_utterances_returns_zero(self):
        assert _obs_confidence(0) == 0.0

    def test_negative_returns_zero(self):
        assert _obs_confidence(-5) == 0.0

    def test_positive_utterances_positive_confidence(self):
        assert _obs_confidence(5) > 0.0

    def test_twelve_utterances_approx_half(self):
        """At n=12 (half-life), confidence ≈ 0.63."""
        c = _obs_confidence(12)
        assert 0.58 < c < 0.68

    def test_ceiling_at_36(self):
        """At 36 utterances, close to ceiling (0.95)."""
        assert _obs_confidence(36) >= 0.94

    def test_large_n_capped_at_0_95(self):
        assert _obs_confidence(1000) == pytest.approx(0.95)

    def test_monotonically_increasing(self):
        prev = 0.0
        for n in range(1, 50):
            curr = _obs_confidence(n)
            assert curr >= prev, f"Confidence decreased at n={n}"
            prev = curr


# ---------------------------------------------------------------------------
# ParticipantProfiler — classification accuracy
# ---------------------------------------------------------------------------

def _run_profiler(utterances: list[str], window_size: int = 5) -> WindowClassification:
    """Helper: feed utterances to a fresh profiler and return final classification."""
    profiler = ParticipantProfiler(window_size=window_size)
    result = None
    for utt in utterances:
        result = profiler.add_utterance("speaker_A", utt)
    return result


class TestParticipantProfilerClassification:
    def test_logic_dominant_gives_positive_focus(self):
        result = _run_profiler([_LOGIC_UTT, _LOGIC_UTT, _LOGIC_UTT])
        assert result.focus_score > 0

    def test_narrative_dominant_gives_negative_focus(self):
        result = _run_profiler([_NARRATIVE_UTT, _NARRATIVE_UTT, _NARRATIVE_UTT])
        assert result.focus_score < 0

    def test_advocacy_dominant_gives_positive_stance(self):
        result = _run_profiler([_ADVOCACY_UTT, _ADVOCACY_UTT, _ADVOCACY_UTT])
        assert result.stance_score > 0

    def test_analysis_dominant_gives_negative_stance(self):
        result = _run_profiler([_ANALYSIS_UTT, _ANALYSIS_UTT, _ANALYSIS_UTT])
        assert result.stance_score < 0

    def test_inquisitor_classified(self):
        """Logic + Advocacy → Inquisitor."""
        result = _run_profiler([_INQUISITOR_UTT] * 5)
        assert result.superpower == "Inquisitor"

    def test_firestarter_classified(self):
        """Narrative + Advocacy → Firestarter."""
        result = _run_profiler([_FIRESTARTER_UTT] * 5)
        assert result.superpower == "Firestarter"

    def test_architect_classified(self):
        """Logic + Analysis → Architect."""
        result = _run_profiler([_ARCHITECT_UTT] * 5)
        assert result.superpower == "Architect"

    def test_bridge_builder_classified(self):
        """Narrative + Analysis → Bridge Builder."""
        result = _run_profiler([_BRIDGE_UTT] * 5)
        assert result.superpower == "Bridge Builder"

    def test_neutral_utterances_may_be_undetermined(self):
        """Balanced or empty signals should not produce high-confidence classification."""
        result = _run_profiler([_NEUTRAL_UTT] * 5)
        # Either Undetermined or very low confidence — neutral text shouldn't produce rich signals
        if result.superpower != "Undetermined":
            assert result.confidence < 0.5, (
                f"Expected low confidence for neutral text, got {result.confidence}"
            )

    def test_narrow_neutral_band_classifies_more(self):
        """A narrow neutral band of 1 should classify nearly any non-zero score."""
        profiler = ParticipantProfiler(neutral_band=1)
        result = profiler.add_utterance("A", "The data shows growth. We should act.")
        assert result.superpower != "Undetermined"


# ---------------------------------------------------------------------------
# ParticipantProfiler — window logic
# ---------------------------------------------------------------------------

class TestParticipantProfilerWindow:
    def test_first_utterance_always_returns_classification(self):
        """Carry-forward: a result is produced after the very first utterance."""
        profiler = ParticipantProfiler()
        result = profiler.add_utterance("A", _LOGIC_UTT)
        assert result is not None
        assert result.utterance_count == 1

    def test_utterance_count_reaches_window_size(self):
        profiler = ParticipantProfiler(window_size=3)
        for i in range(3):
            result = profiler.add_utterance("A", _LOGIC_UTT)
        assert result.utterance_count == 3

    def test_utterance_count_does_not_exceed_window_size(self):
        """Adding 7 utterances to a window of 3 should not give utterance_count > 3."""
        profiler = ParticipantProfiler(window_size=3)
        for _ in range(7):
            result = profiler.add_utterance("A", _LOGIC_UTT)
        assert result.utterance_count == 3

    def test_old_utterances_evicted(self):
        """Sliding window: filling with narrative, then flooding with logic → focus flips."""
        profiler = ParticipantProfiler(window_size=3, neutral_band=1)
        # Fill window with narrative
        for _ in range(3):
            profiler.add_utterance("A", _NARRATIVE_UTT)
        result_narrative = profiler.get_classification("A")
        assert result_narrative.focus_score < 0

        # Flood window with logic (evicts all narrative)
        for _ in range(3):
            profiler.add_utterance("A", _LOGIC_UTT)
        result_logic = profiler.get_classification("A")
        assert result_logic.focus_score > 0

    def test_carry_forward_low_utterance_count(self):
        """Even with 1 utterance the profiler returns a result, not None."""
        profiler = ParticipantProfiler()
        result = profiler.add_utterance("A", _LOGIC_UTT)
        assert result.superpower is not None  # may be Undetermined but not None

    def test_multiple_speakers_tracked_independently(self):
        profiler = ParticipantProfiler(neutral_band=1)
        profiler.add_utterance("logic_speaker", _LOGIC_UTT * 3)
        profiler.add_utterance("narrative_speaker", _NARRATIVE_UTT * 3)

        logic_result = profiler.get_classification("logic_speaker")
        narrative_result = profiler.get_classification("narrative_speaker")

        assert logic_result.focus_score > 0
        assert narrative_result.focus_score < 0

    def test_get_classification_none_for_unseen_speaker(self):
        profiler = ParticipantProfiler()
        assert profiler.get_classification("nobody") is None

    def test_reset_clears_all_windows(self):
        profiler = ParticipantProfiler()
        profiler.add_utterance("A", _LOGIC_UTT)
        profiler.reset()
        assert profiler.get_classification("A") is None
        assert profiler.speakers() == []

    def test_all_classifications_returns_all_speakers(self):
        profiler = ParticipantProfiler()
        profiler.add_utterance("A", _LOGIC_UTT)
        profiler.add_utterance("B", _NARRATIVE_UTT)
        all_c = profiler.all_classifications()
        assert set(all_c.keys()) == {"A", "B"}

    def test_speakers_returns_seen_speaker_ids(self):
        profiler = ParticipantProfiler()
        profiler.add_utterance("A", "hello")
        profiler.add_utterance("B", "world")
        assert set(profiler.speakers()) == {"A", "B"}

    def test_window_classification_fields(self):
        profiler = ParticipantProfiler()
        result = profiler.add_utterance("X", _LOGIC_UTT)
        assert result.speaker_id == "X"
        assert isinstance(result.focus_score, float)
        assert isinstance(result.stance_score, float)
        assert 0.0 <= result.confidence <= 0.9
        assert result.utterance_count == 1


# ---------------------------------------------------------------------------
# UserBehaviorObserver
# ---------------------------------------------------------------------------

class TestUserBehaviorObserver:
    def _make_observer(self, user_speaker: str = "speaker_0") -> UserBehaviorObserver:
        return UserBehaviorObserver(user_speaker=user_speaker)

    def test_non_user_utterances_ignored(self):
        obs = self._make_observer("speaker_0")
        obs.add_utterance("speaker_1", _LOGIC_UTT * 5)
        assert obs.utterance_count == 0

    def test_user_utterances_processed(self):
        obs = self._make_observer("speaker_0")
        obs.add_utterance("speaker_0", _LOGIC_UTT)
        assert obs.utterance_count == 1

    def test_mixed_utterances_only_user_counted(self):
        obs = self._make_observer("speaker_0")
        obs.add_utterance("speaker_0", _LOGIC_UTT)
        obs.add_utterance("speaker_1", _LOGIC_UTT)
        obs.add_utterance("speaker_0", _LOGIC_UTT)
        assert obs.utterance_count == 2

    def test_empty_session_zero_confidence(self):
        obs = self._make_observer()
        observation = obs.get_observation("sess-1", "board")
        assert observation.obs_confidence == 0.0
        assert observation.focus_score == 0.0
        assert observation.stance_score == 0.0

    def test_get_observation_session_id(self):
        obs = self._make_observer()
        observation = obs.get_observation("my-session-id", "team")
        assert observation.session_id == "my-session-id"

    def test_get_observation_context(self):
        obs = self._make_observer()
        observation = obs.get_observation("s", "client")
        assert observation.context == "client"

    def test_obs_confidence_grows_with_utterances(self):
        few = self._make_observer()
        many = self._make_observer()
        for _ in range(3):
            few.add_utterance("speaker_0", _LOGIC_UTT)
        for _ in range(20):
            many.add_utterance("speaker_0", _LOGIC_UTT)
        obs_few = few.get_observation("s", "team")
        obs_many = many.get_observation("s", "team")
        assert obs_many.obs_confidence > obs_few.obs_confidence

    def test_logic_heavy_user_gives_positive_focus(self):
        obs = self._make_observer()
        for _ in range(10):
            obs.add_utterance("speaker_0", _LOGIC_UTT)
        observation = obs.get_observation("s", "team")
        assert observation.focus_score > 0

    def test_advocacy_heavy_user_gives_positive_stance(self):
        obs = self._make_observer()
        for _ in range(10):
            obs.add_utterance("speaker_0", _ADVOCACY_UTT)
        observation = obs.get_observation("s", "team")
        assert observation.stance_score > 0

    def test_narrative_heavy_user_gives_negative_focus(self):
        obs = self._make_observer()
        for _ in range(10):
            obs.add_utterance("speaker_0", _NARRATIVE_UTT)
        observation = obs.get_observation("s", "team")
        assert observation.focus_score < 0

    def test_analysis_heavy_user_gives_negative_stance(self):
        obs = self._make_observer()
        for _ in range(10):
            obs.add_utterance("speaker_0", _ANALYSIS_UTT)
        observation = obs.get_observation("s", "team")
        assert observation.stance_score < 0

    def test_utterance_count_in_observation(self):
        obs = self._make_observer()
        obs.add_utterance("speaker_0", _LOGIC_UTT)
        obs.add_utterance("speaker_0", _LOGIC_UTT)
        observation = obs.get_observation("s", "team")
        assert observation.utterance_count == 2

    def test_reset_clears_all_signals(self):
        obs = self._make_observer()
        for _ in range(5):
            obs.add_utterance("speaker_0", _LOGIC_UTT)
        obs.reset()
        assert obs.utterance_count == 0
        observation = obs.get_observation("s", "team")
        assert observation.obs_confidence == 0.0

    def test_returns_session_observation_type(self):
        obs = self._make_observer()
        observation = obs.get_observation("s", "board")
        assert isinstance(observation, SessionObservation)

    def test_get_observation_does_not_mutate_state(self):
        """Calling get_observation twice should return the same result."""
        obs = self._make_observer()
        for _ in range(5):
            obs.add_utterance("speaker_0", _LOGIC_UTT)
        obs1 = obs.get_observation("s", "board")
        obs2 = obs.get_observation("s", "board")
        assert obs1.focus_score == obs2.focus_score
        assert obs1.obs_confidence == obs2.obs_confidence
