"""
Persuasion Score + Growth Score computation — pure functions, no I/O.

Architecture (from CLAUDE.md):
    Persuasion Score = Timing 30% + Ego Safety 30% + Convergence 40%

    Growth Score = delta of Persuasion Score vs. user's rolling baseline

Components
----------
Timing (30%)
    Measures whether the user's talk-time ratio is in the persuasive sweet spot.
    Optimal: 25–45% of total session talk time.
    - Dominates (>60%): audiences disengage → low score
    - Silent (<15%): not leading the room → low score
    - Sweet spot (25–45%): leading without monopolising → high score

Ego Safety (30%)
    Measures how much defensive/ego-threat pressure the user is generating.
    Proxy: audience challenge ratio from question_type_arc signal details.
    When elm_detector.py is built, this can be replaced with real ELM state counts.
    - High challenge ratio → audience is defending, not persuaded → low score
    - Low challenge ratio → room is receptive → high score

Convergence (40%)
    Composite of three validated NLP signals:
        vocabulary_adoption  33%
        question_type_arc    33%
        agreement_markers    34%
    Validated at 80% accuracy against 5 real annotated transcripts (2026-03-25).

Score ranges
    Persuasion Score:  0–100 integer (displayed in overlay)
    Growth Score:      float, positive = improving vs. baseline
                       None when no prior sessions exist

Disclosure (required in UI per CLAUDE.md):
    Persuasion Score is a heuristic index. Weights (Timing 30% / Ego Safety 30% /
    Convergence 40%) are calibrated by user feedback over time, not empirically
    derived. Disclose this in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from backend.signals import convergence_score, SignalResult


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class TimingComponent:
    score: float          # 0.0–1.0
    talk_time_ratio: float
    total_words: int
    user_words: int
    in_sweet_spot: bool   # True if ratio is 25–45%
    note: str


@dataclass
class EgoSafetyComponent:
    score: float          # 0.0–1.0
    challenge_ratio: float   # fraction of audience questions that were challenging
    challenge_count: int
    total_questions: int
    note: str


@dataclass
class ConvergenceComponent:
    score: float          # 0.0–1.0, combined from three signals
    signal_results: list[SignalResult]


@dataclass
class PersuasionScore:
    """
    Final Persuasion Score for a session.

    score:     0–100 integer shown in the overlay
    raw:       0.0–1.0 float before rounding
    timing:    Timing component (30%)
    ego_safety: Ego Safety component (30%)
    convergence: Convergence component (40%)
    """
    score: int
    raw: float
    timing: TimingComponent
    ego_safety: EgoSafetyComponent
    convergence: ConvergenceComponent

    TIMING_WEIGHT: float = field(default=0.30, init=False, repr=False)
    EGO_SAFETY_WEIGHT: float = field(default=0.30, init=False, repr=False)
    CONVERGENCE_WEIGHT: float = field(default=0.40, init=False, repr=False)


@dataclass
class GrowthScore:
    """
    Growth Score — how much the user improved vs. their own baseline.

    delta:          raw difference (current score – baseline)
    baseline:       rolling average of prior session scores (0–100)
    current:        this session's Persuasion Score (0–100)
    sessions_used:  number of prior sessions used to compute baseline
    trend:          "improving" | "stable" | "declining"
    """
    delta: float
    baseline: float
    current: int
    sessions_used: int
    trend: str   # "improving" | "stable" | "declining"


# ---------------------------------------------------------------------------
# Component scorers (pure functions)
# ---------------------------------------------------------------------------

def _score_timing(utterances: list[dict], user_speaker: str) -> TimingComponent:
    """
    Compute Timing component from utterance word counts.

    Optimal talk-time ratio: 25–45% of total words spoken.

    Score function:
      - ratio in [0.25, 0.45]  → 1.0  (sweet spot)
      - ratio < 0.15 or > 0.65 → 0.0  (too silent or dominating)
      - linear ramp between thresholds
    """
    user_words = sum(len(u["text"].split()) for u in utterances if u["speaker"] == user_speaker)
    total_words = sum(len(u["text"].split()) for u in utterances)

    if total_words == 0:
        return TimingComponent(
            score=0.0,
            talk_time_ratio=0.0,
            total_words=0,
            user_words=0,
            in_sweet_spot=False,
            note="No utterances found",
        )

    ratio = user_words / total_words
    in_sweet_spot = 0.25 <= ratio <= 0.45

    if in_sweet_spot:
        score = 1.0
    elif ratio < 0.25:
        # Ramp: 0.0 at ratio=0.0, 1.0 at ratio=0.25
        score = ratio / 0.25
    elif ratio <= 0.55:
        # Gradual decline: 1.0 at 0.45, 0.5 at 0.55
        score = 1.0 - ((ratio - 0.45) / 0.10) * 0.5
    else:
        # Steep decline: 0.5 at 0.55, 0.0 at 0.70
        score = max(0.0, 0.5 - ((ratio - 0.55) / 0.15) * 0.5)

    if ratio < 0.15:
        note = "Very little speaking — user not driving the room"
    elif ratio < 0.25:
        note = "Below sweet spot — user could speak more to lead"
    elif in_sweet_spot:
        note = "Optimal talk time — leading without monopolising"
    elif ratio <= 0.55:
        note = "Slightly above sweet spot — consider asking more questions"
    else:
        note = "Dominating airtime — audience disengaging"

    return TimingComponent(
        score=round(score, 4),
        talk_time_ratio=round(ratio, 4),
        total_words=total_words,
        user_words=user_words,
        in_sweet_spot=in_sweet_spot,
        note=note,
    )


def _score_ego_safety(
    signal_results: list[SignalResult],
    ego_threat_events: int = 0,
) -> EgoSafetyComponent:
    """
    Compute Ego Safety component.

    Uses two inputs:
      1. challenge_ratio from question_type_arc signal details (primary proxy)
      2. ego_threat_events from elm_detector (0 until elm_detector.py is built)

    Score:
      - No challenging questions AND no ELM events → 1.0
      - All questions are challenging AND multiple ELM events → 0.0
      - Linear blend between those poles
    """
    # Extract challenge signals from question_type_arc AND uptake_ratio
    arc_result = next(
        (r for r in signal_results if r.signal == "question_type_arc"),
        None,
    )
    uptake_result = next(
        (r for r in signal_results if r.signal == "uptake_ratio"),
        None,
    )

    challenge_count = 0
    total_questions = 0

    if arc_result and arc_result.details:
        d = arc_result.details
        challenge_count += d.get("total_challenging", 0)
        total_questions += d.get("total_questions", 0)

    # Blend in resistance markers from uptake analysis
    if uptake_result and uptake_result.details:
        d = uptake_result.details
        challenge_count += d.get("resistance_count", 0)
        total_questions += d.get("uptake_count", 0) + d.get("resistance_count", 0)

    challenge_ratio = challenge_count / total_questions if total_questions else 0.0

    # Primary score: inverse of challenge ratio
    # challenge_ratio=0.0 → score=1.0 (room not pushing back)
    # challenge_ratio=0.5 → score=0.5
    # challenge_ratio=1.0 → score=0.0
    challenge_score = 1.0 - challenge_ratio

    # ELM penalty: each ego-threat event reduces score by 0.15, floor 0.0
    elm_penalty = min(0.6, ego_threat_events * 0.15)
    score = max(0.0, challenge_score - elm_penalty)

    if total_questions == 0:
        note = "No audience questions detected — cannot assess challenge pressure"
    elif challenge_ratio == 0.0 and ego_threat_events == 0:
        note = "Room fully receptive — no defensive signals"
    elif challenge_ratio < 0.20:
        note = "Minimal pushback — room largely receptive"
    elif challenge_ratio < 0.40:
        note = "Moderate challenge pressure — some resistance in the room"
    else:
        note = "High challenge pressure — audience is in defensive mode"

    if ego_threat_events > 0:
        note += f" (+{ego_threat_events} ego-threat event{'s' if ego_threat_events > 1 else ''})"

    return EgoSafetyComponent(
        score=round(score, 4),
        challenge_ratio=round(challenge_ratio, 4),
        challenge_count=challenge_count,
        total_questions=total_questions,
        note=note,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_persuasion_score(
    utterances: list[dict],
    user_speaker: str,
    ego_threat_events: int = 0,
) -> PersuasionScore:
    """
    Compute the Persuasion Score for a session.

    Parameters
    ----------
    utterances : list[dict]
        Full session utterance list. Each dict must have:
            speaker (str), text (str), start (float), end (float)
    user_speaker : str
        Speaker ID of the user being coached.
    ego_threat_events : int
        Count of ELM ego-threat events detected during the session.
        Pass 0 until elm_detector.py is integrated.

    Returns
    -------
    PersuasionScore
        .score  — 0–100 integer for display in overlay
        .raw    — 0.0–1.0 float before rounding
        .timing, .ego_safety, .convergence — component breakdowns
    """
    # --- Convergence (40%) ---
    conv_combined, signal_results = convergence_score(utterances, user_speaker)
    convergence = ConvergenceComponent(score=conv_combined, signal_results=signal_results)

    # --- Timing (30%) ---
    timing = _score_timing(utterances, user_speaker)

    # --- Ego Safety (30%) ---
    ego_safety = _score_ego_safety(signal_results, ego_threat_events)

    # --- Weighted composite ---
    raw = (
        timing.score * 0.30
        + ego_safety.score * 0.30
        + convergence.score * 0.40
    )
    score = round(raw * 100)

    return PersuasionScore(
        score=score,
        raw=round(raw, 4),
        timing=timing,
        ego_safety=ego_safety,
        convergence=convergence,
    )


def compute_growth_score(
    current_score: int,
    prior_scores: Sequence[int],
    *,
    window: int = 5,
) -> GrowthScore | None:
    """
    Compute Growth Score vs. the user's rolling baseline.

    Parameters
    ----------
    current_score : int
        This session's Persuasion Score (0–100).
    prior_scores : Sequence[int]
        Historical Persuasion Scores for this user, oldest first.
        Pass an empty list for a user's first session.
    window : int
        Number of recent sessions to average for the baseline (default: 5).

    Returns
    -------
    GrowthScore  — delta, baseline, trend
    None         — if prior_scores is empty (no baseline yet)
    """
    if not prior_scores:
        return None

    recent = list(prior_scores[-window:])
    baseline = sum(recent) / len(recent)
    delta = current_score - baseline

    if delta >= 3:
        trend = "improving"
    elif delta <= -3:
        trend = "declining"
    else:
        trend = "stable"

    return GrowthScore(
        delta=round(delta, 1),
        baseline=round(baseline, 1),
        current=current_score,
        sessions_used=len(recent),
        trend=trend,
    )


# ---------------------------------------------------------------------------
# Prompt effectiveness — convergence delta around a coaching prompt
# ---------------------------------------------------------------------------

def compute_prompt_effectiveness(
    utterances: list[dict],
    user_speaker: str,
    prompt_utterance_index: int,
    *,
    window_size: int = 8,
    min_utterances: int = 3,
) -> tuple[float | None, float | None, float | None]:
    """
    Measure whether convergence improved after a coaching prompt.

    Slices utterances into a before-window and an after-window around the
    prompt's utterance_index, runs convergence_score() on each, and returns
    the delta.

    Parameters
    ----------
    utterances : list[dict]
        Full session utterance list (speaker, text, start, end).
    user_speaker : str
        Speaker ID of the coached user.
    prompt_utterance_index : int
        Index into utterances when the prompt was generated.
    window_size : int
        Number of utterances to examine in each window (default 8).
    min_utterances : int
        Minimum utterances required in each window for a meaningful
        measurement. Returns (None, None, None) if either window is
        too small.

    Returns
    -------
    (effectiveness, convergence_before, convergence_after)
        effectiveness: after - before, clamped to [0.0, 1.0].
        Returns (None, None, None) when data is insufficient.
    """
    if prompt_utterance_index < 0 or prompt_utterance_index >= len(utterances):
        return (None, None, None)

    before = utterances[max(0, prompt_utterance_index - window_size):prompt_utterance_index]
    after = utterances[prompt_utterance_index + 1:prompt_utterance_index + 1 + window_size]

    if len(before) < min_utterances or len(after) < min_utterances:
        return (None, None, None)

    score_before, _ = convergence_score(before, user_speaker)
    score_after, _ = convergence_score(after, user_speaker)

    effectiveness = max(0.0, min(1.0, score_after - score_before + 0.5))
    return (
        round(effectiveness, 4),
        round(score_before, 4),
        round(score_after, 4),
    )


# ---------------------------------------------------------------------------
# Coaching effectiveness aggregation
# ---------------------------------------------------------------------------

def update_coaching_effectiveness(
    avg_effectiveness: float,
    total_prompts: int,
    effective_prompts: int,
    suggested_cadence_s: float,
    prompt_effectiveness: float,
    *,
    alpha: float = 0.2,
    min_prompts_for_cadence: int = 5,
    min_cadence: float = 15.0,
    max_cadence: float = 90.0,
) -> tuple[float, int, int, float]:
    """
    EWMA update of aggregate coaching effectiveness for one archetype pairing.

    Parameters
    ----------
    avg_effectiveness : float
        Current EWMA effectiveness (0.0–1.0).
    total_prompts : int
        Total prompts evaluated so far.
    effective_prompts : int
        Count of prompts with effectiveness > 0.5.
    suggested_cadence_s : float
        Current suggested cadence in seconds.
    prompt_effectiveness : float
        This prompt's effectiveness score (0.0–1.0).
    alpha : float
        EWMA smoothing factor (default 0.2 — adapts over ~5 sessions).
    min_prompts_for_cadence : int
        Minimum evaluated prompts before adjusting cadence.

    Returns
    -------
    (new_avg, new_total, new_effective, new_cadence)
    """
    new_total = total_prompts + 1
    new_effective = effective_prompts + (1 if prompt_effectiveness > 0.5 else 0)
    new_avg = (1 - alpha) * avg_effectiveness + alpha * prompt_effectiveness

    new_cadence = suggested_cadence_s
    if new_total >= min_prompts_for_cadence:
        if new_avg > 0.6:
            # Effective coaching → slightly shorter cadence (more coaching)
            new_cadence = max(min_cadence, suggested_cadence_s * 0.95)
        elif new_avg < 0.3:
            # Ineffective coaching → longer cadence (reduce noise)
            new_cadence = min(max_cadence, suggested_cadence_s * 1.1)

    return (
        round(new_avg, 4),
        new_total,
        new_effective,
        round(new_cadence, 1),
    )


# ---------------------------------------------------------------------------
# Skill Badges — prompt frequency decay
# ---------------------------------------------------------------------------

BADGE_METADATA: dict[str, tuple[str, str]] = {
    "elm:ego_threat":           ("Psychological Safety",   "you make people feel safe to disagree"),
    "elm:shortcut":             ("Deep Engagement",        "people think before they agree"),
    "elm:consensus_protection": ("Healthy Dissent",        "you welcome the hard question"),
    "cadence:self":             ("Mode Awareness",         "you know when to shift"),
    "cadence:group":            ("Room Reader",            "you know when to invite the room"),
}


def compute_skill_badges(
    recent_sessions_triggers: list[list[str]],
    *,
    consecutive_threshold: int = 3,
) -> list[str]:
    """
    Return trigger_types that have not fired in the last `consecutive_threshold`
    consecutive sessions — indicating the user has internalized the skill.

    Parameters
    ----------
    recent_sessions_triggers : list of sessions ordered oldest-to-newest;
        each entry is the list of triggered_by values for that session.
    consecutive_threshold : int
        Sessions without the prompt before awarding a badge (default 3).

    Returns
    -------
    List of trigger_types (keys of BADGE_METADATA) that qualify for a badge.
    Returns [] when fewer than consecutive_threshold sessions are available.
    """
    if len(recent_sessions_triggers) < consecutive_threshold:
        return []

    last_n = recent_sessions_triggers[-consecutive_threshold:]
    return [
        trigger_type
        for trigger_type in BADGE_METADATA
        if not any(trigger_type in session_triggers for session_triggers in last_n)
    ]
