"""
Profile classification benchmark — curated transcript fixtures.

Tests profiler accuracy and convergence speed across 20 scenarios:
  - 4 pure archetype (5 strong utterances each)
  - 4 mixed-start convergence (3 noise + 5 strong)
  - 4 context-shifting (board vs. team tone, 10 utterances)
  - 4 minimal input (3 utterances)
  - 4 edge cases (filler, sarcasm, questions-as-advocacy, silence-heavy)

Headline metric: ≥75% correct (12/16 assertable fixtures).
No API keys required — runs in CI.
"""

from __future__ import annotations

import pytest

from backend.profiler import ParticipantProfiler, WindowClassification


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _run_profiler(
    utterances: list[str],
    speaker: str = "speaker_A",
    window_size: int = 5,
) -> WindowClassification:
    """Feed utterances and return final classification."""
    profiler = ParticipantProfiler(window_size=window_size)
    result = None
    for utt in utterances:
        result = profiler.add_utterance(speaker, utt)
    return result


def _run_profiler_at_steps(
    utterances: list[str],
    speaker: str = "speaker_A",
    window_size: int = 5,
) -> list[WindowClassification]:
    """Feed utterances and return classification after each one."""
    profiler = ParticipantProfiler(window_size=window_size)
    results = []
    for utt in utterances:
        results.append(profiler.add_utterance(speaker, utt))
    return results


# ---------------------------------------------------------------------------
# Transcript fixtures — 20 scenarios
# ---------------------------------------------------------------------------

# ── Pure archetype (5 strong utterances) ─────────────────────────────────

PURE_ARCHITECT = [
    "The data shows a 47% drop in conversion. What do you think about the analysis?",
    "Our metrics indicate the root cause is in the funnel. I'd like to understand the specifics.",
    "The benchmark against last quarter's report is clear. Have you considered the evidence?",
    "Looking at the KPIs and statistics, the correlation is significant. What are your thoughts?",
    "I'd like to explore this data further before proceeding. The research suggests caution.",
]

PURE_FIRESTARTER = [
    "Imagine the impact we could have on the entire industry! We should move forward now.",
    "I remember when we launched the last product — the excitement was incredible. Let's commit to this vision.",
    "Picture this: a journey that inspires the whole team. We need to lead with passion.",
    "The story of our company is about bold moves. I believe we should push ahead immediately.",
    "I'm so excited about this scenario! Let's move forward and inspire everyone to join us.",
]

PURE_INQUISITOR = [
    "The evidence is clear — 23% growth. We need to commit to this metrics-driven approach now.",
    "Our data proves the hypothesis. I recommend we act on the research immediately.",
    "The statistics show exactly what the analysis predicted. We should move forward with the plan.",
    "Because the evidence supports it, we must decide today. The baseline data is compelling.",
    "Therefore, based on our analysis, we need to act. The proof is in the numbers — let's commit.",
]

PURE_BRIDGE_BUILDER = [
    "Picture how the team might feel about this. What does everyone think?",
    "I remember a similar scenario where listening to different perspectives helped. I'm curious about your thoughts.",
    "Imagine if we explored this together. What are your feelings on the journey ahead?",
    "The story here reminds me of when we found common ground. Perhaps we could consider alternatives?",
    "I wonder how the team envisions this. Let me tell you about a time when feedback changed everything.",
]

# ── Mixed start → convergence (3 noise + 5 strong) ──────────────────────

NOISE_UTTS = [
    "Okay, sure, that makes sense.",
    "Right, moving on to the next topic then.",
    "Yeah, I agree with that point.",
]

MIXED_ARCHITECT = NOISE_UTTS + PURE_ARCHITECT
MIXED_FIRESTARTER = NOISE_UTTS + PURE_FIRESTARTER
MIXED_INQUISITOR = NOISE_UTTS + PURE_INQUISITOR
MIXED_BRIDGE_BUILDER = NOISE_UTTS + PURE_BRIDGE_BUILDER

# ── Context-shifting (board vs. team, 10 utterances) ────────────────────
# First 5 lean one way, last 5 lean strongly toward the target archetype

CONTEXT_ARCHITECT = [
    # Board-mode: more formal, data-heavy
    "The quarterly report shows a 12% decline. What does the team think about this data?",
    "Our analysis of the benchmark metrics is concerning. Have you considered the root cause?",
    "The evidence from our research points to operational inefficiency. What are your thoughts?",
    "I'd like to understand the statistics before we proceed.",
    "The KPIs and measurements tell a specific story. Let's explore the data further.",
    # Continued in team meeting — same pattern
    "Looking at the analysis here, the data is consistent with our hypothesis.",
    "The report confirms what the research suggested. What do you think?",
    "I'd like to review the evidence one more time before proceeding.",
    "The correlation between these metrics is notable. Perhaps we should explore further?",
    "Based on the data and analysis, I'm curious about alternative interpretations.",
]

CONTEXT_FIRESTARTER = [
    "I had an exciting idea this morning. We should rally the team around this vision.",
    "Imagine what would happen if we doubled down here. Let's move forward!",
    "The energy in the room is great. I believe we need to act on this momentum.",
    "Picture the journey ahead — we should commit to this inspiring path.",
    "When I think about our story, I feel passionate. Let's decide now!",
    "This scenario excites me. We need to push ahead with this vision.",
    "Imagine the team's reaction! We should move forward immediately.",
    "I believe we must lead with passion here. Let's commit!",
    "The dream we're building is powerful. We need to act on it now.",
    "This is our moment. I'm excited — let's move forward together!",
]

CONTEXT_INQUISITOR = [
    "The data speaks clearly — 35% improvement. We should act.",
    "Our evidence proves the approach works. I recommend we commit.",
    "The metrics are definitive. We need to move forward based on the research.",
    "Because the analysis supports it, we must decide. The numbers are clear.",
    "The statistics leave no doubt. We should implement immediately.",
    "Our hypothesis was validated — the evidence is compelling. Let's commit.",
    "The proof is in the data. I recommend we act now.",
    "Based on the research, we need to move forward decisively.",
    "The benchmark shows a clear advantage. We must commit to this approach.",
    "Therefore, the evidence demands action. We should proceed immediately.",
]

CONTEXT_BRIDGE_BUILDER = [
    "I'm curious how everyone sees this. What are your thoughts?",
    "Picture what this might look like from different perspectives. Perhaps we should explore?",
    "I wonder about the feelings around this decision. What does the team think?",
    "Imagine walking in their shoes. I'd like to understand everyone's feedback.",
    "The narrative here is complex. Have you considered how others might feel?",
    "What are your perspectives on this scenario? I'm curious about alternative views.",
    "Perhaps we could reflect on this together. What do you think?",
    "I remember a time when listening to everyone's input changed the outcome entirely.",
    "The story has many sides. I wonder what feedback the team has?",
    "Imagine if we explored all perspectives. What does everyone think we should consider?",
]

# ── Minimal input (3 utterances) ─────────────────────────────────────────

MINIMAL_ARCHITECT = [
    "The data shows 15% growth. What do you think?",
    "Our analysis suggests the evidence is strong.",
    "I'd like to understand the metrics before we decide.",
]

MINIMAL_FIRESTARTER = [
    "Imagine the possibilities! We should move forward.",
    "I feel excited about this vision. Let's commit now.",
    "Picture the journey — we need to lead with passion.",
]

MINIMAL_INQUISITOR = [
    "The evidence proves it — we need to act on this data.",
    "Our research shows 28% improvement. I recommend we commit.",
    "The statistics are clear. We should move forward immediately.",
]

MINIMAL_BRIDGE_BUILDER = [
    "What do you think about this? I'm curious to hear perspectives.",
    "Imagine how the team feels. Perhaps we should explore?",
    "I wonder what everyone thinks. What are your thoughts?",
]

# ── Edge cases ───────────────────────────────────────────────────────────

EDGE_FILLER_HEAVY = [
    "Um, yeah, so, uh, okay.",
    "Sure, right, got it.",
    "Yep, makes sense, okay.",
    "Mm-hmm, yeah, I see.",
    "Right, okay, understood.",
]

EDGE_SARCASM = [
    "Oh sure, let's just throw more data at it because that always works, right?",
    "What a brilliant plan. Obviously the metrics will magically fix themselves.",
    "Yes, I'm clearly very excited about this amazing vision of more meetings.",
    "Perhaps we should consider that this is exactly like the last time it failed?",
    "Definitely let's move forward with this — what could possibly go wrong?",
]

EDGE_QUESTIONS_AS_ADVOCACY = [
    "Don't you think we should just act on this data already?",
    "Isn't it obvious that the metrics prove we need to move forward?",
    "Why aren't we committing to what the evidence clearly shows?",
    "Shouldn't we decide now based on the analysis? The data is right there.",
    "How long are we going to wait when the research is this compelling?",
]

EDGE_LONG_PAUSES = [
    "...",
    "The data shows 10% growth.",
    "...",
    "...",
    "I'd like to understand the analysis. What do you think?",
]


# ---------------------------------------------------------------------------
# Fixture registry — (utterances, expected_archetype, scenario_name)
# ---------------------------------------------------------------------------

FIXTURES: list[tuple[list[str], str, str]] = [
    # Pure (1-4)
    (PURE_ARCHITECT, "Architect", "pure_architect"),
    (PURE_FIRESTARTER, "Firestarter", "pure_firestarter"),
    (PURE_INQUISITOR, "Inquisitor", "pure_inquisitor"),
    (PURE_BRIDGE_BUILDER, "Bridge Builder", "pure_bridge_builder"),
    # Mixed (5-8)
    (MIXED_ARCHITECT, "Architect", "mixed_architect"),
    (MIXED_FIRESTARTER, "Firestarter", "mixed_firestarter"),
    (MIXED_INQUISITOR, "Inquisitor", "mixed_inquisitor"),
    (MIXED_BRIDGE_BUILDER, "Bridge Builder", "mixed_bridge_builder"),
    # Context-shifting (9-12)
    (CONTEXT_ARCHITECT, "Architect", "context_architect"),
    (CONTEXT_FIRESTARTER, "Firestarter", "context_firestarter"),
    (CONTEXT_INQUISITOR, "Inquisitor", "context_inquisitor"),
    (CONTEXT_BRIDGE_BUILDER, "Bridge Builder", "context_bridge_builder"),
    # Minimal (13-16)
    (MINIMAL_ARCHITECT, "Architect", "minimal_architect"),
    (MINIMAL_FIRESTARTER, "Firestarter", "minimal_firestarter"),
    (MINIMAL_INQUISITOR, "Inquisitor", "minimal_inquisitor"),
    (MINIMAL_BRIDGE_BUILDER, "Bridge Builder", "minimal_bridge_builder"),
    # Edge cases (17-20) — expected varies
    (EDGE_FILLER_HEAVY, "Undetermined", "edge_filler"),
    (EDGE_SARCASM, None, "edge_sarcasm"),          # behavior documented, not asserted
    (EDGE_QUESTIONS_AS_ADVOCACY, None, "edge_questions_as_advocacy"),
    (EDGE_LONG_PAUSES, None, "edge_long_pauses"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPureArchetypeAccuracy:
    """4 pure fixtures → correct archetype, confidence ≥0.65."""

    @pytest.mark.parametrize("utterances,expected,name", FIXTURES[:4], ids=[f[2] for f in FIXTURES[:4]])
    def test_pure_classification(self, utterances, expected, name):
        result = _run_profiler(utterances)
        assert result.superpower == expected, (
            f"{name}: expected {expected}, got {result.superpower} "
            f"(focus={result.focus_score:.1f}, stance={result.stance_score:.1f})"
        )
        assert result.confidence >= 0.65, (
            f"{name}: confidence {result.confidence} < 0.65"
        )


class TestConvergenceAt8Utterances:
    """Mixed-start fixtures converge to correct type by utterance 8."""

    @pytest.mark.parametrize("utterances,expected,name", FIXTURES[4:8], ids=[f[2] for f in FIXTURES[4:8]])
    def test_convergence(self, utterances, expected, name):
        # Window size 5 means by utterance 8, all 5 noise utts are evicted
        steps = _run_profiler_at_steps(utterances)
        # Check classification at utterance 8 (index 7)
        at_8 = steps[7] if len(steps) > 7 else steps[-1]
        assert at_8.superpower == expected, (
            f"{name}: at utterance 8, expected {expected}, got {at_8.superpower} "
            f"(focus={at_8.focus_score:.1f}, stance={at_8.stance_score:.1f})"
        )


class TestConfidenceMonotonicIncrease:
    """Confidence at utterance 5 ≥ confidence at utterance 3 (same speaker, consistent signals)."""

    @pytest.mark.parametrize("utterances,expected,name", FIXTURES[:4], ids=[f[2] for f in FIXTURES[:4]])
    def test_monotonic(self, utterances, expected, name):
        steps = _run_profiler_at_steps(utterances)
        conf_3 = steps[2].confidence if len(steps) > 2 else 0.0
        conf_5 = steps[4].confidence if len(steps) > 4 else steps[-1].confidence
        assert conf_5 >= conf_3, (
            f"{name}: confidence dropped from {conf_3} (utt 3) to {conf_5} (utt 5)"
        )


class TestMinimalInputGraceful:
    """3-utterance fixtures → either correct or Unknown, never wrong archetype with high confidence."""

    @pytest.mark.parametrize("utterances,expected,name", FIXTURES[12:16], ids=[f[2] for f in FIXTURES[12:16]])
    def test_minimal(self, utterances, expected, name):
        result = _run_profiler(utterances)
        if result.superpower == expected:
            # Correct — good
            return
        if result.superpower == "Undetermined":
            # Acceptable — not enough signal
            return
        # Wrong archetype — only acceptable at very low confidence
        assert result.confidence < 0.55, (
            f"{name}: wrong archetype {result.superpower} (expected {expected}) "
            f"with confidence {result.confidence} ≥ 0.55"
        )


class TestWindowSlidesCorrectly:
    """Feed 10 utterances (5 Architect + 5 Firestarter) → final classification is Firestarter."""

    def test_window_slide(self):
        utterances = PURE_ARCHITECT + PURE_FIRESTARTER
        result = _run_profiler(utterances, window_size=5)
        assert result.superpower == "Firestarter", (
            f"Expected Firestarter after window slide, got {result.superpower}"
        )


class TestNoiseResilience:
    """Insert 2 filler utterances mid-stream → classification unchanged vs. clean stream."""

    @pytest.mark.parametrize("utterances,expected,name", FIXTURES[:4], ids=[f[2] for f in FIXTURES[:4]])
    def test_noise_resilience(self, utterances, expected, name):
        # Clean: 5 strong utterances
        clean_result = _run_profiler(utterances)
        # Noisy: same utterances but with 2 filler injected at positions 2 and 4
        noisy = list(utterances)
        noisy.insert(2, "Okay, sure, got it.")
        noisy.insert(5, "Right, makes sense.")
        noisy_result = _run_profiler(noisy)
        assert noisy_result.superpower == clean_result.superpower, (
            f"{name}: noise changed classification from "
            f"{clean_result.superpower} to {noisy_result.superpower}"
        )


class TestAggregateAccuracyMatrix:
    """Run all 20 fixtures → ≥75% correct (12/16 assertable). Headline metric."""

    def test_aggregate_accuracy(self):
        correct = 0
        total = 0
        failures = []

        for utterances, expected, name in FIXTURES:
            if expected is None or expected == "Undetermined":
                # Edge cases — skip for accuracy count
                continue
            total += 1
            result = _run_profiler(utterances)
            if result.superpower == expected:
                correct += 1
            else:
                failures.append(
                    f"  {name}: expected {expected}, got {result.superpower} "
                    f"(focus={result.focus_score:.1f}, stance={result.stance_score:.1f}, "
                    f"conf={result.confidence:.3f})"
                )

        pct = correct / total * 100 if total else 0
        assert correct >= 12, (  # 75% of 16 fixtures with expected archetypes
            f"Aggregate accuracy {correct}/{total} ({pct:.0f}%) < 75%.\n"
            f"Failures:\n" + "\n".join(failures)
        )


class TestEdgeCaseBehavior:
    """Document edge case behavior — no hard pass/fail, just capture results."""

    def test_filler_heavy_is_undetermined(self):
        result = _run_profiler(EDGE_FILLER_HEAVY)
        # Filler should produce Undetermined or very low confidence
        if result.superpower != "Undetermined":
            assert result.confidence < 0.5, (
                f"Filler-heavy text classified as {result.superpower} "
                f"with confidence {result.confidence} (expected Undetermined or low conf)"
            )

    def test_sarcasm_documented(self):
        """Sarcasm may confuse signals — just verify no crash and capture result."""
        result = _run_profiler(EDGE_SARCASM)
        assert result is not None
        assert result.utterance_count == 5

    def test_questions_as_advocacy_documented(self):
        """Rhetorical questions with advocacy intent — may lean Analysis or Inquisitor."""
        result = _run_profiler(EDGE_QUESTIONS_AS_ADVOCACY)
        assert result is not None
        # These utterances contain both advocacy words (should, move forward)
        # and question marks (analysis signal) — documenting actual behavior
        assert result.utterance_count == 5

    def test_long_pauses_documented(self):
        """Mostly empty utterances — should have very low confidence."""
        result = _run_profiler(EDGE_LONG_PAUSES)
        assert result is not None
        # 5 utterances in window (empty ones still count for base confidence)
        # but signal density is low, so confidence should be moderate
        assert result.confidence < 0.8
