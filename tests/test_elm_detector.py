"""
Tests for backend/elm_detector.py — ELM state detection.

Coverage:
  - Signal detection (ego threat, shortcut, consensus phrases)
  - Ego threat: first hostile → event; consecutive = same episode; 2 neutral → reset
  - Shortcut: 3 pure-agreement streak → event; question breaks streak
  - Consensus protection: phrase match → event; debounce same as ego_threat
  - User utterances silently ignored
  - Multiple speakers tracked independently
  - ego_threat_events / shortcut_events / consensus_events counts
  - current_state() and all_states() accessors
  - reset() clears all state
"""

import pytest
from backend.elm_detector import ELMDetector, ELMEvent

USER = "speaker_0"
COUNTERPART = "speaker_1"
OTHER = "speaker_2"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_detector() -> ELMDetector:
    return ELMDetector(user_speaker=USER)


# ---------------------------------------------------------------------------
# User utterances are ignored
# ---------------------------------------------------------------------------

class TestUserIgnored:
    def test_user_utterance_returns_none(self):
        det = make_detector()
        result = det.process_utterance(USER, "I disagree with this completely.")
        assert result is None

    def test_user_utterance_does_not_change_state(self):
        det = make_detector()
        det.process_utterance(USER, "I disagree with this completely.")
        assert det.ego_threat_events == 0
        assert det.current_state(USER) == "neutral"

    def test_user_hostile_does_not_appear_in_all_states(self):
        det = make_detector()
        det.process_utterance(USER, "I disagree, that doesn't make sense.")
        assert USER not in det.all_states()


# ---------------------------------------------------------------------------
# Ego threat detection
# ---------------------------------------------------------------------------

class TestEgoThreatDetection:
    def test_explicit_disagree(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "I disagree with your approach.")
        assert event is not None
        assert event.state == "ego_threat"
        assert event.speaker_id == COUNTERPART

    def test_i_dont_think(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "I don't think that will work.")
        assert event is not None
        assert event.state == "ego_threat"

    def test_not_convinced(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "I'm not convinced this is the right direction.")
        assert event is not None
        assert event.state == "ego_threat"

    def test_weve_always_done(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "We've always handled this differently.")
        assert event is not None
        assert event.state == "ego_threat"

    def test_with_all_due_respect(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "With all due respect, that's not how it works.")
        assert event is not None
        assert event.state == "ego_threat"

    def test_thats_a_stretch(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "That's a stretch — I don't see it.")
        assert event is not None
        assert event.state == "ego_threat"

    def test_why_would_you(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "Why would you approach it that way?")
        assert event is not None
        assert event.state == "ego_threat"

    def test_evidence_list_populated(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "I disagree and I'm not buying it.")
        assert event is not None
        assert len(event.evidence) > 0

    def test_utterance_text_captured(self):
        det = make_detector()
        text = "I disagree with your approach here."
        event = det.process_utterance(COUNTERPART, text)
        assert event is not None
        assert event.utterance == text

    def test_neutral_utterance_no_event(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "That sounds like a reasonable plan.")
        assert event is None

    def test_state_becomes_ego_threat(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree completely.")
        assert det.current_state(COUNTERPART) == "ego_threat"


# ---------------------------------------------------------------------------
# Ego threat episode deduplication (consecutive = same episode)
# ---------------------------------------------------------------------------

class TestEgoThreatEpisode:
    def test_consecutive_hostile_utterances_count_as_one_episode(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree with this approach.")
        det.process_utterance(COUNTERPART, "I'm not buying it either.")
        det.process_utterance(COUNTERPART, "That doesn't make sense at all.")
        assert det.ego_threat_events == 1

    def test_consecutive_hostile_returns_none_after_first(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree with this approach.")
        event2 = det.process_utterance(COUNTERPART, "I'm not buying it either.")
        assert event2 is None

    def test_debounce_resets_after_two_neutral_utterances(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree completely.")
        # Two neutral utterances to exit episode
        det.process_utterance(COUNTERPART, "Okay, I see your point.")
        det.process_utterance(COUNTERPART, "That makes sense, let me think about it.")
        # Now a new hostile utterance should trigger a new event
        event = det.process_utterance(COUNTERPART, "Actually no, I'm not convinced.")
        assert event is not None
        assert event.state == "ego_threat"
        assert det.ego_threat_events == 2

    def test_single_neutral_does_not_reset_episode(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree completely.")
        det.process_utterance(COUNTERPART, "Okay, I see your point.")  # 1 neutral
        # Hostile before debounce completes — still in episode
        event = det.process_utterance(COUNTERPART, "Actually no, I'm not buying it.")
        assert event is None
        assert det.ego_threat_events == 1

    def test_hostile_mid_debounce_resets_neutral_streak(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree completely.")
        det.process_utterance(COUNTERPART, "Okay, fair point.")          # neutral streak: 1
        det.process_utterance(COUNTERPART, "I'm not convinced though.")  # hostile — resets streak
        det.process_utterance(COUNTERPART, "Alright, I follow.")         # neutral streak: 1
        det.process_utterance(COUNTERPART, "That makes sense.")          # neutral streak: 2 → exits
        event = det.process_utterance(COUNTERPART, "I disagree again.")
        assert event is not None
        assert det.ego_threat_events == 2

    def test_state_returns_to_neutral_after_debounce(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree.")
        det.process_utterance(COUNTERPART, "Fair point.")
        det.process_utterance(COUNTERPART, "Makes sense, thanks.")
        assert det.current_state(COUNTERPART) == "neutral"


# ---------------------------------------------------------------------------
# Shortcut detection (peripheral-route pure agreement)
# ---------------------------------------------------------------------------

class TestShortcutDetection:
    def test_three_pure_agreements_trigger_shortcut(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Absolutely.")
        det.process_utterance(COUNTERPART, "Yes, totally.")
        event = det.process_utterance(COUNTERPART, "Agreed.")
        assert event is not None
        assert event.state == "shortcut"

    def test_fewer_than_three_agreements_no_event(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Absolutely.")
        event = det.process_utterance(COUNTERPART, "Yes, totally.")
        assert event is None

    def test_question_breaks_agreement_streak(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Absolutely.")
        det.process_utterance(COUNTERPART, "Yes, totally.")
        det.process_utterance(COUNTERPART, "Sure, but what do you mean by that?")  # has "?"
        event = det.process_utterance(COUNTERPART, "Agreed.")
        assert event is None

    def test_long_utterance_breaks_agreement_streak(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Absolutely.")
        det.process_utterance(COUNTERPART, "Yes, I think so.")
        # > 15 words — not a pure agreement
        long = "Agreed, though I want to note that we should carefully consider the downstream implications of this."
        det.process_utterance(COUNTERPART, long)
        event = det.process_utterance(COUNTERPART, "Sounds good.")
        assert event is None

    def test_ego_threat_phrase_breaks_agreement_streak(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Agreed.")
        det.process_utterance(COUNTERPART, "Yes.")
        # ego_threat has higher priority — it fires ego_threat event, not a shortcut
        event = det.process_utterance(COUNTERPART, "Sure, though I disagree on that.")
        assert event is not None
        assert event.state == "ego_threat"
        assert det.shortcut_events == 0

    def test_shortcut_exits_immediately_on_question(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Absolutely.")
        det.process_utterance(COUNTERPART, "Yes.")
        det.process_utterance(COUNTERPART, "Agreed.")  # triggers shortcut
        det.process_utterance(COUNTERPART, "What do you mean exactly?")  # has "?" → exit
        assert det.current_state(COUNTERPART) == "neutral"

    def test_shortcut_exits_immediately_on_substantive(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Absolutely.")
        det.process_utterance(COUNTERPART, "Yes.")
        det.process_utterance(COUNTERPART, "Agreed.")  # triggers shortcut
        # Substantive — not a pure agreement, no ego threat, no consensus
        det.process_utterance(COUNTERPART, "Let me think through the implications here.")
        assert det.current_state(COUNTERPART) == "neutral"

    def test_shortcut_streak_count(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Yes.")
        det.process_utterance(COUNTERPART, "Sure.")
        det.process_utterance(COUNTERPART, "Absolutely.")  # shortcut event #1
        assert det.shortcut_events == 1

    def test_shortcut_no_event_already_in_shortcut(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Yes.")
        det.process_utterance(COUNTERPART, "Sure.")
        det.process_utterance(COUNTERPART, "Absolutely.")  # triggers shortcut
        # Another pure agreement while already in shortcut — no new event
        det.process_utterance(COUNTERPART, "Agreed.")
        assert det.shortcut_events == 1

    def test_shortcut_not_triggered_during_ego_threat_episode(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree.")  # ego_threat episode
        # Pure agreement utterances while in ego episode — ignored for shortcut
        det.process_utterance(COUNTERPART, "Yes.")
        det.process_utterance(COUNTERPART, "Sure.")
        det.process_utterance(COUNTERPART, "Absolutely.")
        assert det.shortcut_events == 0

    def test_various_shortcut_phrases(self):
        phrases = [
            "Got it.",
            "Understood.",
            "Noted.",
            "Great.",
            "Perfect.",
            "Fantastic.",
            "Makes sense.",
            "Sounds good.",
            "Fair enough.",
        ]
        for i, phrase in enumerate(phrases):
            det = make_detector()
            det.process_utterance(COUNTERPART, "Yes.")
            det.process_utterance(COUNTERPART, "Sure.")
            event = det.process_utterance(COUNTERPART, phrase)
            assert event is not None and event.state == "shortcut", f"Failed for: {phrase!r}"


# ---------------------------------------------------------------------------
# Consensus protection detection
# ---------------------------------------------------------------------------

class TestConsensusProtection:
    def test_i_think_we_all_agree(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "I think we all agree on the direction.")
        assert event is not None
        assert event.state == "consensus_protection"

    def test_were_all_aligned(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "I think we're all aligned on this.")
        assert event is not None
        assert event.state == "consensus_protection"

    def test_lets_move_on(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "Let's just move on and decide.")
        assert event is not None
        assert event.state == "consensus_protection"

    def test_lets_not_debate(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "Let's not debate this anymore.")
        assert event is not None
        assert event.state == "consensus_protection"

    def test_we_dont_need_to_debate(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "We don't need to debate this.")
        assert event is not None
        assert event.state == "consensus_protection"

    def test_weve_all_agreed(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "We've all agreed on this already.")
        assert event is not None
        assert event.state == "consensus_protection"

    def test_consecutive_consensus_utterances_count_as_one_episode(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I think we all agree.")
        det.process_utterance(COUNTERPART, "Let's just move on.")
        assert det.consensus_events == 1

    def test_consensus_episode_resets_after_two_neutrals(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I think we all agree.")
        det.process_utterance(COUNTERPART, "Okay, that's a fair point to bring up.")
        det.process_utterance(COUNTERPART, "Let me think about the tradeoffs.")
        event = det.process_utterance(COUNTERPART, "I think we all agree on this too.")
        assert event is not None
        assert det.consensus_events == 2

    def test_state_becomes_consensus_protection(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I think we're all on the same page.")
        assert det.current_state(COUNTERPART) == "consensus_protection"

    def test_consensus_count_property(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I think we all agree.")
        assert det.consensus_events == 1


# ---------------------------------------------------------------------------
# Priority: ego_threat overrides consensus / shortcut
# ---------------------------------------------------------------------------

class TestPriority:
    def test_ego_threat_overrides_consensus_protection(self):
        det = make_detector()
        # Utterance with BOTH ego threat and consensus signals
        event = det.process_utterance(
            COUNTERPART,
            "With all due respect, I think we don't need to debate this."
        )
        assert event is not None
        assert event.state == "ego_threat"

    def test_ego_threat_overrides_shortcut(self):
        det = make_detector()
        # "sure" is shortcut, "I disagree" is ego threat
        event = det.process_utterance(COUNTERPART, "Sure, but I disagree with that.")
        assert event is not None
        assert event.state == "ego_threat"

    def test_ego_threat_overrides_during_consensus_episode(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Let's not debate this.")  # consensus episode
        event = det.process_utterance(COUNTERPART, "I disagree — I think we should debate it.")
        assert event is not None
        assert event.state == "ego_threat"
        assert det.current_state(COUNTERPART) == "ego_threat"


# ---------------------------------------------------------------------------
# Multi-speaker isolation
# ---------------------------------------------------------------------------

class TestMultiSpeaker:
    def test_speakers_tracked_independently(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree completely.")
        det.process_utterance(OTHER, "Yes, totally agree.")
        assert det.current_state(COUNTERPART) == "ego_threat"
        assert det.current_state(OTHER) == "neutral"

    def test_shortcut_streak_tracked_per_speaker(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Yes.")
        det.process_utterance(OTHER, "Yes.")
        det.process_utterance(COUNTERPART, "Sure.")
        det.process_utterance(OTHER, "Sure.")
        event_cp = det.process_utterance(COUNTERPART, "Agreed.")  # 3rd for COUNTERPART
        event_ot = det.process_utterance(OTHER, "Agreed.")        # 3rd for OTHER

        assert event_cp is not None and event_cp.state == "shortcut"
        assert event_ot is not None and event_ot.state == "shortcut"

    def test_ego_threat_events_count_across_speakers(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree.")
        det.process_utterance(OTHER, "That doesn't make sense.")
        assert det.ego_threat_events == 2

    def test_all_states_returns_all_tracked_speakers(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree.")
        det.process_utterance(OTHER, "Yes.")
        states = det.all_states()
        assert COUNTERPART in states
        assert OTHER in states
        assert USER not in states

    def test_unknown_speaker_returns_neutral(self):
        det = make_detector()
        assert det.current_state("speaker_99") == "neutral"

    def test_debounce_independent_per_speaker(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree.")
        det.process_utterance(OTHER, "I'm not convinced.")

        # Only COUNTERPART gets 2 neutral utterances
        det.process_utterance(COUNTERPART, "OK fair.")
        det.process_utterance(COUNTERPART, "I see your point.")
        # COUNTERPART should be neutral now; OTHER still in ego_threat episode
        assert det.current_state(COUNTERPART) == "neutral"
        assert det.current_state(OTHER) == "ego_threat"


# ---------------------------------------------------------------------------
# Counters and accessors
# ---------------------------------------------------------------------------

class TestCountersAndAccessors:
    def test_initial_counts_zero(self):
        det = make_detector()
        assert det.ego_threat_events == 0
        assert det.shortcut_events == 0
        assert det.consensus_events == 0

    def test_ego_threat_count_increments(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree.")
        # 2 neutrals to exit
        det.process_utterance(COUNTERPART, "OK.")
        det.process_utterance(COUNTERPART, "Makes sense.")
        det.process_utterance(COUNTERPART, "I'm not buying it.")
        assert det.ego_threat_events == 2

    def test_shortcut_count_increments(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "Yes.")
        det.process_utterance(COUNTERPART, "Sure.")
        det.process_utterance(COUNTERPART, "Agreed.")  # event 1
        # Exit shortcut
        det.process_utterance(COUNTERPART, "What do you mean?")
        det.process_utterance(COUNTERPART, "Yes.")
        det.process_utterance(COUNTERPART, "Sure.")
        det.process_utterance(COUNTERPART, "Absolutely.")  # event 2
        assert det.shortcut_events == 2

    def test_current_state_before_any_utterance(self):
        det = make_detector()
        assert det.current_state(COUNTERPART) == "neutral"

    def test_all_states_empty_initially(self):
        det = make_detector()
        assert det.all_states() == {}


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_state(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree.")
        det.reset()
        assert det.current_state(COUNTERPART) == "neutral"
        assert det.all_states() == {}

    def test_reset_clears_counts(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree.")
        det.process_utterance(OTHER, "Yes.")
        det.process_utterance(OTHER, "Sure.")
        det.process_utterance(OTHER, "Absolutely.")
        det.process_utterance(COUNTERPART, "I think we all agree.")
        det.reset()
        assert det.ego_threat_events == 0
        assert det.shortcut_events == 0
        assert det.consensus_events == 0

    def test_reset_allows_new_episode(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "I disagree.")
        det.reset()
        event = det.process_utterance(COUNTERPART, "I disagree again.")
        assert event is not None
        assert event.state == "ego_threat"
        assert det.ego_threat_events == 1


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------

class TestCaseInsensitivity:
    def test_uppercase_ego_threat(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "I DISAGREE with this approach.")
        assert event is not None
        assert event.state == "ego_threat"

    def test_uppercase_shortcut(self):
        det = make_detector()
        det.process_utterance(COUNTERPART, "YES.")
        det.process_utterance(COUNTERPART, "SURE.")
        event = det.process_utterance(COUNTERPART, "AGREED.")
        assert event is not None
        assert event.state == "shortcut"

    def test_mixed_case_consensus(self):
        det = make_detector()
        event = det.process_utterance(COUNTERPART, "I Think We All Agree on This.")
        assert event is not None
        assert event.state == "consensus_protection"
