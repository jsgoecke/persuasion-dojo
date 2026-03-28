"""
Unit tests for backend/signals.py — convergence signal detectors.

Tests the research-grounded signals:
  1. Language Style Matching (LSM) — function-word alignment
  2. Pronoun Convergence — we/our vs I/you shift
  3. Uptake Ratio — building-on vs resistance
  4. Question-Type Arc — challenge → clarifying → confirmatory

These use synthetic transcripts. Real annotated transcripts should be used
for calibration via scripts/convergence_spike.py.
"""

import pytest
from backend.signals import (
    language_style_matching,
    pronoun_convergence,
    uptake_ratio,
    question_type_arc,
    convergence_score,
    _classify_question,
    _compute_lsm,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_transcript(turns: list[tuple[str, str, float]]) -> list[dict]:
    """Build utterance list from (speaker, text, start_seconds) tuples."""
    return [
        {"speaker": speaker, "text": text, "start": start, "end": start + 5.0}
        for speaker, text, start in turns
    ]


USER = "speaker_0"
AUD1 = "speaker_1"
AUD2 = "speaker_2"


# ---------------------------------------------------------------------------
# _classify_question (kept from prior version)
# ---------------------------------------------------------------------------

class TestClassifyQuestion:
    def test_challenging_why(self):
        assert _classify_question("Why would we do that?") == "challenging"

    def test_challenging_evidence(self):
        assert _classify_question("What evidence do you have for this?") == "challenging"

    def test_clarifying_how_would(self):
        assert _classify_question("How would that work in practice?") == "clarifying"

    def test_clarifying_can_you_explain(self):
        assert _classify_question("Can you explain the activation flow?") == "clarifying"

    def test_not_a_question(self):
        assert _classify_question("I agree with that approach.") == "not_question"


# ---------------------------------------------------------------------------
# _compute_lsm
# ---------------------------------------------------------------------------

class TestLSM:
    def test_identical_function_words_perfect_score(self):
        """Identical text should produce near-perfect LSM."""
        words = "the quick and the slow but we can not do it for you".split()
        lsm = _compute_lsm(words, words)
        assert lsm > 0.95

    def test_different_styles_lower_score(self):
        """Very different function-word profiles should score lower."""
        # Speaker A: lots of "I" and "my" (personal, assertive)
        words_a = "I think my approach is right and I believe we should do it my way I feel strongly".split()
        # Speaker B: lots of "it" and "that" (impersonal, analytical)
        words_b = "that approach seems viable if the data supports it then the outcome should be positive".split()
        lsm = _compute_lsm(words_a, words_b)
        assert lsm < 0.90  # measurably different

    def test_empty_input(self):
        assert _compute_lsm([], ["the", "quick"]) == 0.0
        assert _compute_lsm(["the"], []) == 0.0


# ---------------------------------------------------------------------------
# language_style_matching
# ---------------------------------------------------------------------------

class TestLanguageStyleMatching:
    def test_converging_aligned_conversation(self):
        """Speakers using similar function-word patterns should score well."""
        utterances = make_transcript([
            (USER, "I think we should move forward with the plan and allocate resources.", 0.0),
            (AUD1, "I agree that we need to allocate the right resources for this.", 5.0),
            (USER, "The team can start on this if we have the budget approved.", 10.0),
            (AUD1, "Yes the budget should be approved and we can have the team start.", 15.0),
            (USER, "We would need to coordinate with the other teams for support.", 20.0),
            (AUD1, "We should definitely coordinate because the other teams will need to adjust.", 25.0),
            (USER, "I think if we do this right we can have results by the end of quarter.", 30.0),
            (AUD1, "I believe we can achieve results if the team is focused on this.", 35.0),
        ])
        result = language_style_matching(utterances, USER)
        assert result.score > 0.0  # should get a positive score

    def test_insufficient_data(self):
        utterances = make_transcript([
            (USER, "Hi.", 0.0),
            (AUD1, "Hello.", 5.0),
        ])
        result = language_style_matching(utterances, USER)
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# pronoun_convergence
# ---------------------------------------------------------------------------

class TestPronounConvergence:
    def test_shift_to_we(self):
        """Conversation that shifts from I/you to we/our should score high."""
        utterances = make_transcript([
            # First third: I/you framing
            (USER, "I think you should consider my proposal for your team.", 0.0),
            (AUD1, "I don't see how your idea helps my department.", 5.0),
            (AUD2, "I need you to explain how this affects my team.", 10.0),
            # Middle: transitioning
            (USER, "Let me show you what we could achieve together.", 15.0),
            (AUD1, "I see how we might benefit from this approach.", 20.0),
            (AUD2, "We should look at how our teams could collaborate.", 25.0),
            # Final third: we/our framing
            (USER, "Our combined effort would drive results for us all.", 30.0),
            (AUD1, "We should move forward together with our shared plan.", 35.0),
            (AUD2, "Let's get our teams aligned on this together.", 40.0),
        ])
        result = pronoun_convergence(utterances, USER)
        assert result.score > 0.3
        assert result.details["shift"] > 0

    def test_stays_individual(self):
        """Conversation staying in I/you mode should score lower."""
        utterances = make_transcript([
            (USER, "I want you to understand my point here.", 0.0),
            (AUD1, "I disagree with your approach to my team.", 5.0),
            (AUD2, "You should reconsider your position on this.", 10.0),
            (USER, "I stand by my recommendation for your team.", 15.0),
            (AUD1, "I still think your idea has problems.", 20.0),
            (AUD2, "You need to address my concerns first.", 25.0),
        ])
        result = pronoun_convergence(utterances, USER)
        assert result.details["we_ratio_final_third"] < 0.4

    def test_insufficient_utterances(self):
        utterances = make_transcript([
            (USER, "Hi.", 0.0),
            (AUD1, "Hello.", 5.0),
        ])
        result = pronoun_convergence(utterances, USER)
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# uptake_ratio
# ---------------------------------------------------------------------------

class TestUptakeRatio:
    def test_building_on(self):
        """Utterances with uptake markers should produce high score."""
        utterances = make_transcript([
            (USER, "I propose we adopt a product-led growth strategy.", 0.0),
            (AUD1, "Building on that, we could start with the self-serve tier.", 5.0),
            (AUD2, "To add to that point, the data supports this direction.", 10.0),
            (USER, "Exactly right. And the activation funnel would drive retention.", 15.0),
            (AUD1, "That makes sense. Let's move forward with a pilot.", 20.0),
            (AUD2, "Agreed. I'm on board with this approach.", 25.0),
        ])
        result = uptake_ratio(utterances, USER)
        assert result.score > 0.3
        assert result.details["uptake_count"] >= 3

    def test_resistance_dominant(self):
        """Utterances with resistance markers should produce lower ratio."""
        utterances = make_transcript([
            (USER, "I propose we change our approach entirely.", 0.0),
            (AUD1, "But that won't work for our current setup.", 5.0),
            (AUD2, "However I think we need a different direction.", 10.0),
            (USER, "Let me explain why this makes sense.", 15.0),
            (AUD1, "I don't think the data supports this.", 20.0),
            (AUD2, "The problem with that is the cost.", 25.0),
        ])
        result = uptake_ratio(utterances, USER)
        assert result.details["resistance_count"] > result.details["uptake_count"]

    def test_insufficient_data(self):
        utterances = make_transcript([(USER, "Hi.", 0.0)])
        result = uptake_ratio(utterances, USER)
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# question_type_arc
# ---------------------------------------------------------------------------

class TestQuestionTypeArc:
    def test_converging_arc(self):
        """Questions shift from challenging to clarifying."""
        utterances = make_transcript([
            (USER, "I propose product-led growth.", 0.0),
            (AUD1, "Why would we do that?", 5.0),
            (AUD2, "What evidence do you have?", 10.0),
            (AUD1, "Isn't this risky?", 15.0),
            (USER, "Here's the data — retention improves 40%.", 20.0),
            (AUD2, "How would the rollout work?", 50.0),
            (AUD1, "Can you explain the timeline?", 55.0),
            (AUD2, "What resources would we need?", 60.0),
        ])
        result = question_type_arc(utterances, USER)
        assert result.converging is True

    def test_not_converging_flat_adversarial(self):
        """Questions remain adversarial throughout."""
        utterances = make_transcript([
            (USER, "Here's why we should change.", 0.0),
            (AUD1, "Why would we do that?", 5.0),
            (AUD2, "What evidence do you have?", 10.0),
            (USER, "Let me show the data.", 20.0),
            (AUD1, "But what about the risk?", 50.0),
            (AUD2, "Why should we believe these numbers?", 55.0),
            (AUD1, "Have you considered the downside?", 60.0),
        ])
        result = question_type_arc(utterances, USER)
        assert result.converging is False

    def test_insufficient_questions_neutral_score(self):
        """Fewer than 3 questions — neutral score, not zero."""
        utterances = make_transcript([
            (USER, "Here's my proposal.", 0.0),
            (AUD1, "Interesting idea.", 5.0),
        ])
        result = question_type_arc(utterances, USER)
        assert result.score == 0.5  # neutral, not penalizing


# ---------------------------------------------------------------------------
# convergence_score (combined)
# ---------------------------------------------------------------------------

class TestConvergenceScore:
    def test_converging_conversation(self):
        """A clearly converging conversation should score reasonably."""
        utterances = make_transcript([
            (USER, "I think we should move forward with the plan and allocate resources for our team.", 0.0),
            (AUD1, "Why would we do that? What evidence do you have?", 5.0),
            (AUD2, "I'm not convinced your approach is right for my team.", 10.0),
            (USER, "The data shows our retention improves by 40% when we adopt this approach together.", 15.0),
            (AUD1, "How would that work for our enterprise accounts?", 20.0),
            (AUD2, "Can you explain the timeline for our rollout?", 25.0),
            (USER, "We would start together with a pilot and then our teams can expand.", 30.0),
            (AUD1, "Building on that, we could coordinate our resources for a joint launch.", 35.0),
            (AUD2, "That makes sense. Let's move forward together with our plan.", 40.0),
            (AUD1, "Agreed. We should get our teams aligned on this.", 45.0),
        ])
        score, results = convergence_score(utterances, USER)
        assert 0.0 <= score <= 1.0
        assert len(results) == 4

    def test_score_range(self):
        """Score is always 0.0–1.0."""
        utterances = make_transcript([
            (USER, "Here is my proposal.", 0.0),
            (AUD1, "No.", 5.0),
        ])
        score, _ = convergence_score(utterances, USER)
        assert 0.0 <= score <= 1.0

    def test_returns_four_signal_results(self):
        """Combined score returns all four signal results."""
        utterances = make_transcript([
            (USER, "I think we need to move forward together.", 0.0),
            (AUD1, "That makes sense for our team.", 5.0),
            (USER, "We should coordinate our resources.", 10.0),
            (AUD1, "Building on that, we could start next week.", 15.0),
        ])
        _, results = convergence_score(utterances, USER)
        signal_names = {r.signal for r in results}
        assert signal_names == {
            "language_style_matching",
            "pronoun_convergence",
            "uptake_ratio",
            "question_type_arc",
        }
