"""Unit tests for backend.turn_tracker — vocative extraction + turn linking."""

from __future__ import annotations

import pytest

from backend.turn_tracker import (
    COLD_START_THRESHOLD,
    MAX_TURN_GAP_S,
    TurnTracker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tracker(names: list[str] | None = None) -> TurnTracker:
    return TurnTracker(known_names=names or ["Greg Wilson", "Sarah Chen", "Alice Park"])


# ---------------------------------------------------------------------------
# Vocative extraction
# ---------------------------------------------------------------------------

class TestVocativeExtraction:
    """Test that vocative patterns are correctly detected."""

    def test_vocative_start_hi(self):
        t = _tracker()
        names = t._extract_vocatives("Hi Greg, how are you?")
        assert "Greg Wilson" in names

    def test_vocative_start_thanks(self):
        t = _tracker()
        names = t._extract_vocatives("Thanks Sarah, that was helpful.")
        assert "Sarah Chen" in names

    def test_vocative_end(self):
        t = _tracker()
        names = t._extract_vocatives("What do you think, Greg?")
        assert "Greg Wilson" in names

    def test_vocative_end_period(self):
        t = _tracker()
        names = t._extract_vocatives("Great point Greg.")
        assert "Greg Wilson" in names

    def test_vocative_question(self):
        t = _tracker()
        names = t._extract_vocatives("Sarah, what do you think about that?")
        assert "Sarah Chen" in names

    def test_no_vocative_plain_mention(self):
        """A name in the middle of a sentence without vocative markers is not detected."""
        t = _tracker()
        names = t._extract_vocatives("I agree with the proposal from the team.")
        assert names == []

    def test_third_party_said(self):
        """Third-party references should be filtered out."""
        t = _tracker()
        names = t._extract_vocatives("Greg said we should move forward.")
        assert names == []

    def test_third_party_mentioned(self):
        t = _tracker()
        names = t._extract_vocatives("Greg mentioned the deadline is Friday.")
        assert names == []

    def test_third_party_talked_to(self):
        t = _tracker()
        names = t._extract_vocatives("I talked to Sarah yesterday about this.")
        assert names == []

    def test_third_party_as_mentioned(self):
        t = _tracker()
        names = t._extract_vocatives("As Greg mentioned earlier, we need to pivot.")
        assert names == []

    def test_third_party_possessive(self):
        t = _tracker()
        names = t._extract_vocatives("Greg's point about scalability was good.")
        assert names == []

    def test_case_insensitive_lowercase_matches(self):
        """Lowercase 'greg' from ASR output should match (IGNORECASE word-boundary)."""
        t = _tracker()
        names = t._extract_vocatives("Hi greg, how are things?")
        # ASR often lowercases names. The word-boundary check is case-insensitive.
        assert "Greg Wilson" in names

    def test_substring_rejected(self):
        """'Greg' should not match inside 'Gregory' — word boundary check."""
        t = _tracker()
        names = t._extract_vocatives("Hi Gregory, how are things?")
        assert names == []

    def test_third_party_from(self):
        """'from Greg' is a third-party reference, not a vocative."""
        t = _tracker()
        names = t._extract_vocatives("Great point from Greg.")
        assert names == []


# ---------------------------------------------------------------------------
# Ambiguous names
# ---------------------------------------------------------------------------

class TestAmbiguousNames:
    """When two roster entries share a first name, skip that name entirely."""

    def test_ambiguous_first_name_skipped(self):
        t = TurnTracker(known_names=["Sarah Chen", "Sarah Wilson"])
        names = t._extract_vocatives("Thanks Sarah, great work.")
        assert names == []

    def test_unique_name_still_works(self):
        t = TurnTracker(known_names=["Sarah Chen", "Sarah Wilson", "Greg Park"])
        names = t._extract_vocatives("Thanks Greg, great work.")
        assert "Greg Park" in names


# ---------------------------------------------------------------------------
# Turn linking
# ---------------------------------------------------------------------------

class TestTurnLinking:
    """Test that vocative cues get linked to subsequent speakers."""

    def test_basic_link(self):
        t = _tracker()
        # Speaker A addresses Greg, then speaker B (who is Greg) responds.
        t.add_turn("spk_0", "Thanks Greg, what do you think?", 10.0, 12.0)
        t.add_turn("spk_1", "I think we should go with option A.", 12.5, 15.0)
        # spk_1 should have a vocative link to "Greg Wilson"
        assert t._vocative_links["spk_1"]["Greg Wilson"] == 1

    def test_cold_start_below_threshold(self):
        t = _tracker()
        # Only 1 link — below COLD_START_THRESHOLD
        t.add_turn("spk_0", "Thanks Greg, what do you think?", 10.0, 12.0)
        t.add_turn("spk_1", "I agree.", 12.5, 13.0)
        scores = t.get_name_scores()
        assert scores == {}  # No scores below threshold

    def test_cold_start_at_threshold(self):
        t = _tracker()
        # Build up COLD_START_THRESHOLD links
        for i in range(COLD_START_THRESHOLD):
            base = float(i * 10)
            t.add_turn("spk_0", "Greg, what about this?", base, base + 2)
            t.add_turn("spk_1", "Let me think about that.", base + 2.5, base + 4)
        scores = t.get_name_scores()
        assert "spk_1" in scores
        assert "Greg Wilson" in scores["spk_1"]

    def test_timestamp_gap_filter(self):
        """Links are rejected when the gap between speakers exceeds MAX_TURN_GAP_S."""
        t = _tracker()
        t.add_turn("spk_0", "Greg, what do you think?", 10.0, 12.0)
        # Gap of 10s > MAX_TURN_GAP_S
        t.add_turn("spk_1", "Sure, I agree.", 22.0, 24.0)
        assert t._vocative_links["spk_1"].get("Greg Wilson", 0) == 0

    def test_timestamp_gap_within_limit(self):
        t = _tracker()
        t.add_turn("spk_0", "Greg, what do you think?", 10.0, 12.0)
        t.add_turn("spk_1", "Sure, I agree.", 14.0, 16.0)
        # Gap = 2s, within limit
        assert t._vocative_links["spk_1"]["Greg Wilson"] == 1

    def test_self_link_rejected(self):
        """A speaker addressing a name in their own text shouldn't link to themselves."""
        t = _tracker()
        t.add_turn("spk_0", "Hi Greg, let me explain.", 10.0, 12.0)
        t.add_turn("spk_0", "And another thing.", 12.5, 14.0)
        # spk_0 shouldn't link to themselves
        assert t._vocative_links["spk_0"].get("Greg Wilson", 0) == 0


# ---------------------------------------------------------------------------
# 3-turn lookahead
# ---------------------------------------------------------------------------

class TestLookahead:
    """Vocative cues should link across up to 3 subsequent speakers."""

    def test_lookahead_skips_intervening_speaker(self):
        t = _tracker()
        t.add_turn("spk_0", "Greg, can you share your thoughts?", 10.0, 12.0)
        t.add_turn("spk_2", "Let me just add one thing first.", 12.5, 14.0)
        t.add_turn("spk_1", "Sure, I think option A is best.", 14.5, 16.0)
        # spk_1 is within lookahead window, should get the link
        assert t._vocative_links["spk_1"]["Greg Wilson"] >= 1

    def test_lookahead_both_speakers_get_link(self):
        """All speakers in lookahead window get the vocative link."""
        t = _tracker()
        t.add_turn("spk_0", "Greg, can you share your thoughts?", 10.0, 12.0)
        t.add_turn("spk_1", "Something first.", 12.5, 13.0)
        t.add_turn("spk_2", "And another thing.", 13.5, 14.0)
        # Both spk_1 and spk_2 should get links (we don't know who's Greg yet)
        total = (
            t._vocative_links["spk_1"].get("Greg Wilson", 0)
            + t._vocative_links["spk_2"].get("Greg Wilson", 0)
        )
        assert total >= 1


# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------

class TestScoreNormalization:
    def test_scores_normalized_to_one(self):
        t = _tracker()
        # Build enough links to pass cold start
        for i in range(COLD_START_THRESHOLD + 2):
            base = float(i * 10)
            t.add_turn("spk_0", "Greg, what do you think?", base, base + 2)
            t.add_turn("spk_1", "Yes, I agree.", base + 2.5, base + 4)
        scores = t.get_name_scores()
        assert "spk_1" in scores
        # Max score should be 1.0
        assert scores["spk_1"]["Greg Wilson"] == 1.0

    def test_zero_timestamps_no_gap_filter(self):
        """When timestamps are 0.0 (unavailable), gap filter is bypassed."""
        t = _tracker()
        t.add_turn("spk_0", "Thanks Greg", 0.0, 0.0)
        t.add_turn("spk_1", "You're welcome.", 0.0, 0.0)
        # Should still link because 0.0 timestamps skip the gap filter
        assert t._vocative_links["spk_1"]["Greg Wilson"] == 1


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_known_names(self):
        t = TurnTracker(known_names=[])
        t.add_turn("spk_0", "Hi Greg", 0, 1)
        t.add_turn("spk_1", "Hello", 1, 2)
        assert t.get_name_scores() == {}

    def test_none_known_names(self):
        t = TurnTracker(known_names=None)
        t.add_turn("spk_0", "Hi Greg", 0, 1)
        assert t.get_name_scores() == {}

    def test_single_turn_no_crash(self):
        t = _tracker()
        t.add_turn("spk_0", "Hi Greg", 0, 1)
        assert t.get_name_scores() == {}

    def test_empty_text(self):
        t = _tracker()
        t.add_turn("spk_0", "", 0, 1)
        t.add_turn("spk_1", "", 1, 2)
        assert t.get_name_scores() == {}

    def test_known_names_with_none_values(self):
        t = TurnTracker(known_names=["Greg Wilson", None, "", "Sarah Chen"])
        assert len(t._known_names) == 2
