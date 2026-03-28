"""
Self-assessment: classify the user's own Communicator Superpower.

Instrument design (see research notes in scoring.py for references):
  - 12 Likert items (7-point scale), 6 per axis
  - 3 forward + 3 reverse per subscale (50% reverse-scoring per Paulhus 1991)
  - Advocacy items use behavioral frequency framing to reduce social desirability bias
  - Micro-argument: 2-3 sentence open-response behavioral sample → Claude Haiku
  - Confidence: MAD consistency + response timing pacing + micro-argument presence
  - Archetype mapping: ±15-point neutral band per axis

Axes:
  Focus axis   (+100 = pure Logic,    −100 = pure Narrative)
  Stance axis  (+100 = pure Advocacy, −100 = pure Analysis)

Archetypes (from CLAUDE.md domain model):
  Logic + Advocacy   →  Inquisitor
  Narrative + Advocacy → Firestarter
  Logic + Analysis   →  Architect
  Narrative + Analysis → Bridge Builder
  Neutral on either axis → "Undetermined"

Typical usage:
    from backend.self_assessment import ITEMS, score_responses, classify_micro_argument, build_result

    # Present ITEMS to the user, collect AssessmentResponse list (with timing if available)
    axes = score_responses(responses)
    micro = classify_micro_argument(user_text)          # optional — None if skipped
    result = build_result(axes, micro_argument=micro)
"""

from __future__ import annotations

import json
import os
import statistics
from dataclasses import dataclass, field
from typing import Literal

import anthropic

from backend.pre_seeding import SuperpowerType


# ---------------------------------------------------------------------------
# Item definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AssessmentItem:
    """
    A single Likert item in the self-assessment.

    axis:    "focus"  (Logic ↔ Narrative)
             "stance" (Advocacy ↔ Analysis)
    reverse: True → high raw score means the OPPOSITE pole (must reverse-score)
             Focus forward  = Logic pole
             Focus reverse  = Narrative pole
             Stance forward = Advocacy pole
             Stance reverse = Analysis pole
    text:    The question shown to the user (7-point scale, 1=Strongly Disagree…7=Strongly Agree).
    """
    id: str
    axis: Literal["focus", "stance"]
    reverse: bool
    text: str


# 12 items: 6 focus (3F+3R), 6 stance (3F+3R)
# Advocacy items use behavioral frequency framing to reduce social desirability bias.
ITEMS: list[AssessmentItem] = [
    # --- Focus axis: forward (high score = Logic) ---
    AssessmentItem(
        id="F1",
        axis="focus",
        reverse=False,
        text="When making a decision at work, I need to review the supporting data before I feel confident committing.",
    ),
    AssessmentItem(
        id="F2",
        axis="focus",
        reverse=False,
        text="I find it easier to persuade someone by walking them through a step-by-step logical argument than through a story.",
    ),
    AssessmentItem(
        id="F3",
        axis="focus",
        reverse=False,
        text="After an important meeting, I'm more likely to write up a structured summary of conclusions than send an energising follow-up message.",
    ),
    # --- Focus axis: reverse (high raw score = Narrative → reverse to score for Logic) ---
    AssessmentItem(
        id="F4",
        axis="focus",
        reverse=True,
        text="When explaining a complex idea, I naturally reach for a story or analogy rather than an outline.",
    ),
    AssessmentItem(
        id="F5",
        axis="focus",
        reverse=True,
        text="I find that describing a vivid picture of where we're going moves people more than presenting evidence does.",
    ),
    AssessmentItem(
        id="F6",
        axis="focus",
        reverse=True,
        text="I'd rather sketch a compelling vision of the destination than map out the detailed steps to get there.",
    ),
    # --- Stance axis: forward (high score = Advocacy) — behavioral frequency framing ---
    AssessmentItem(
        id="S1",
        axis="stance",
        reverse=False,
        text="In my last three significant meetings, I proposed a specific course of action at least once.",
    ),
    AssessmentItem(
        id="S2",
        axis="stance",
        reverse=False,
        text="When I believe something is the right direction, I continue advocating for it even when others push back.",
    ),
    AssessmentItem(
        id="S3",
        axis="stance",
        reverse=False,
        text="I'm more comfortable staking out a clear position early than staying neutral while a group debates.",
    ),
    # --- Stance axis: reverse (high raw score = Analysis → reverse to score for Advocacy) ---
    AssessmentItem(
        id="S4",
        axis="stance",
        reverse=True,
        text="I often find myself playing devil's advocate, even for ideas I initially lean toward.",
    ),
    AssessmentItem(
        id="S5",
        axis="stance",
        reverse=True,
        text="Before taking a position, I try to fully understand all the perspectives in the room.",
    ),
    AssessmentItem(
        id="S6",
        axis="stance",
        reverse=True,
        text="My instinct when someone proposes a plan is to ask clarifying questions before agreeing or disagreeing.",
    ),
]

# Prompt shown to the user before the micro-argument open response
MICRO_ARGUMENT_PROMPT = (
    "To complete your profile, write 2–3 sentences taking a position on this:\n\n"
    "**Your team has been debating whether to launch a new product feature before it's "
    "fully tested. Make the case for the direction you'd recommend.**\n\n"
    "Be direct — there's no wrong answer."
)

# Neutral band: scores within ±15 on either axis are treated as undetermined for that axis
NEUTRAL_BAND = 15


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class AssessmentResponse:
    """User's response to a single item."""
    item_id: str
    raw_score: int            # 1–7 (Strongly Disagree → Strongly Agree)
    response_time_ms: int     # 0 if timing is not tracked


@dataclass
class ScoredAxes:
    """
    Intermediate result: two axis scores before micro-argument adjustment.

    focus_score:    -100…+100. Positive = Logic, Negative = Narrative.
    stance_score:   -100…+100. Positive = Advocacy, Negative = Analysis.
    confidence:     0.0–1.0 based on response consistency and timing.
    in_neutral_band: per-axis flag for undetermined zone.
    items_used:     number of valid responses included (should be 12).
    """
    focus_score: float
    stance_score: float
    confidence: float
    in_neutral_band: dict[str, bool]  # {"focus": bool, "stance": bool}
    items_used: int


@dataclass
class MicroArgumentResult:
    """
    Classification of the user's micro-argument open response.

    focus_delta:  -10…+10. Positive pushes focus_score toward Logic.
    stance_delta: -10…+10. Positive pushes stance_score toward Advocacy.
    """
    text: str
    focus_axis: Literal["logic", "narrative", "neutral"]
    stance_axis: Literal["advocacy", "analysis", "neutral"]
    focus_delta: float
    stance_delta: float
    reasoning: str


@dataclass
class AssessmentResult:
    """
    Final self-assessment result.

    archetype: One of the four Superpower types, or "Undetermined" when either axis
               falls within the neutral band (±15 points).
    """
    focus_score: float
    stance_score: float
    archetype: SuperpowerType | Literal["Undetermined"]
    confidence: float
    micro_argument: MicroArgumentResult | None
    in_neutral_band: dict[str, bool]
    items_used: int
    note: str


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_ITEM_INDEX: dict[str, AssessmentItem] = {item.id: item for item in ITEMS}

# Likert scale bounds
_SCALE_MIN = 1
_SCALE_MAX = 7
_SCALE_RANGE = _SCALE_MAX - _SCALE_MIN   # 6
_ITEMS_PER_AXIS = 6


def _reverse_score(raw: int) -> int:
    """Flip a 1–7 Likert response: 1↔7, 2↔6, 3↔5, 4↔4."""
    return (_SCALE_MAX + _SCALE_MIN) - raw


def _axis_raw_to_normalized(scored_sum: int, n_items: int) -> float:
    """
    Convert a sum of reverse-scored Likert responses to a -100…+100 float.

    Minimum sum: n_items * 1  →  -100
    Neutral sum: n_items * 4  →    0
    Maximum sum: n_items * 7  → +100
    """
    min_sum = n_items * _SCALE_MIN
    max_sum = n_items * _SCALE_MAX
    return ((scored_sum - min_sum) / (max_sum - min_sum)) * 200 - 100


def _compute_confidence(
    focus_scored: list[int],
    stance_scored: list[int],
    response_times_ms: list[int],
    has_micro_argument: bool,
) -> float:
    """
    Compute response confidence from three signals:
      1. MAD (mean absolute deviation) of reverse-scored responses — consistency
      2. Response timing pacing — flags satisficing (all-fast) and autopilot (uniform)
      3. Micro-argument presence — behavioral sample available

    Returns float in [0.0, 1.0].
    """
    all_scored = focus_scored + stance_scored

    # --- Component 1: consistency (low MAD = high confidence) ---
    mean_score = statistics.mean(all_scored)
    mad = statistics.mean(abs(s - mean_score) for s in all_scored)
    # MAD of 0 → 1.0, MAD of 3+ → 0.0 (3 is ~half the scale range)
    consistency = max(0.0, 1.0 - mad / 3.0)

    # --- Component 2: timing quality ---
    timing_quality = 1.0
    tracked = [t for t in response_times_ms if t > 0]
    if tracked:
        # Penalise fast responses (< 1000ms per Likert item — likely not reading)
        fast_ratio = sum(1 for t in tracked if t < 1000) / len(tracked)
        timing_quality -= fast_ratio * 0.40

        # Penalise uniform timing (CV < 0.20 = autopilot / batch clicking)
        if len(tracked) > 2:
            mean_t = statistics.mean(tracked)
            std_t = statistics.stdev(tracked)
            cv = std_t / mean_t if mean_t > 0 else 0.0
            if cv < 0.20:
                timing_quality -= 0.15

        timing_quality = max(0.0, timing_quality)

    # --- Component 3: behavioral sample ---
    behavior_bonus = 0.10 if has_micro_argument else 0.0

    # Combine: consistency and timing weighted equally; behavior is additive bonus
    base = (consistency * 0.55 + timing_quality * 0.45) * 0.90
    confidence = base + behavior_bonus

    return round(min(1.0, max(0.0, confidence)), 4)


def score_responses(responses: list[AssessmentResponse]) -> ScoredAxes:
    """
    Score a completed set of 12 Likert responses into two axis scores.

    Parameters
    ----------
    responses:
        List of AssessmentResponse, one per item. Missing items are skipped.
        Minimum 4 valid responses per axis required; fewer returns a low-confidence result.

    Returns
    -------
    ScoredAxes with focus_score, stance_score, confidence, and diagnostics.
    """
    focus_scored: list[int] = []
    stance_scored: list[int] = []
    response_times: list[int] = []

    response_map = {r.item_id: r for r in responses}

    for item in ITEMS:
        resp = response_map.get(item.id)
        if resp is None:
            continue
        raw = resp.raw_score
        if not (_SCALE_MIN <= raw <= _SCALE_MAX):
            continue  # ignore out-of-range
        scored = _reverse_score(raw) if item.reverse else raw
        if item.axis == "focus":
            focus_scored.append(scored)
        else:
            stance_scored.append(scored)
        response_times.append(resp.response_time_ms)

    # Guard: insufficient data
    if len(focus_scored) < 2 or len(stance_scored) < 2:
        return ScoredAxes(
            focus_score=0.0,
            stance_score=0.0,
            confidence=0.0,
            in_neutral_band={"focus": True, "stance": True},
            items_used=len(focus_scored) + len(stance_scored),
        )

    focus_score = _axis_raw_to_normalized(sum(focus_scored), len(focus_scored))
    stance_score = _axis_raw_to_normalized(sum(stance_scored), len(stance_scored))

    confidence = _compute_confidence(
        focus_scored,
        stance_scored,
        response_times,
        has_micro_argument=False,  # updated by build_result() if micro-arg is provided
    )

    return ScoredAxes(
        focus_score=round(focus_score, 1),
        stance_score=round(stance_score, 1),
        confidence=confidence,
        in_neutral_band={
            "focus": abs(focus_score) <= NEUTRAL_BAND,
            "stance": abs(stance_score) <= NEUTRAL_BAND,
        },
        items_used=len(focus_scored) + len(stance_scored),
    )


# ---------------------------------------------------------------------------
# Micro-argument classifier
# ---------------------------------------------------------------------------

_MICRO_ARG_SYSTEM_PROMPT = """\
You are an expert in the Communicator Superpower framework, which classifies persuasion style
across two axes: Logic–Narrative (Focus axis) and Advocacy–Analysis (Stance axis).

Given a short written argument (2-3 sentences) where the author is advocating for a position,
classify their natural communication style.

Focus axis:
  "logic"     — uses data, evidence, causal chains, structured reasoning
  "narrative" — uses vision, story, analogy, emotional framing
  "neutral"   — balanced or insufficient signal

Stance axis:
  "advocacy"  — takes a clear position, uses imperative language, argues for a direction
  "analysis"  — hedges, asks questions, explores tradeoffs, defers to evidence before deciding
  "neutral"   — balanced or insufficient signal

Deltas: How strongly does this text lean toward each pole?
  -10…+10 scale.
  Focus:  positive = Logic, negative = Narrative
  Stance: positive = Advocacy, negative = Analysis
  0 = neutral

Respond ONLY with valid JSON in this exact schema:
{
  "focus_axis": "logic" | "narrative" | "neutral",
  "stance_axis": "advocacy" | "analysis" | "neutral",
  "focus_delta": <float -10.0 to 10.0>,
  "stance_delta": <float -10.0 to 10.0>,
  "reasoning": "<one sentence, max 20 words>"
}

Rules:
- delta magnitude reflects signal clarity (10 = unmistakable, 5 = moderate, 1–2 = slight lean)
- delta direction must be consistent with focus_axis/stance_axis:
    focus_axis="logic" → focus_delta > 0
    focus_axis="narrative" → focus_delta < 0
    focus_axis="neutral" → focus_delta ∈ (-3, 3)
    stance_axis="advocacy" → stance_delta > 0
    stance_axis="analysis" → stance_delta < 0
    stance_axis="neutral" → stance_delta ∈ (-3, 3)
- reasoning must explain the specific language feature that drove the classification.
"""


def classify_micro_argument(
    text: str,
    *,
    client: anthropic.Anthropic | None = None,
    model: str = "claude-haiku-4-5",
    max_tokens: int = 200,
) -> MicroArgumentResult:
    """
    Classify the user's micro-argument open response.

    Uses Claude Haiku (same pattern as pre_seeding.classify) to avoid regex false-positives
    from keyword counting across mixed-signal text.

    Parameters
    ----------
    text: The user's 2-3 sentence argument.
    client: Anthropic client (created from env if not provided).

    Returns
    -------
    MicroArgumentResult with focus_delta and stance_delta adjustments.

    Raises
    ------
    ValueError: If text is empty or model returns invalid JSON.
    anthropic.APIError: On API failures (caller should handle).
    """
    if not text or not text.strip():
        raise ValueError("micro-argument text must be non-empty")

    text = text.strip()[:2000]  # cap at 2000 chars

    if client is None:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_MICRO_ARG_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Classify the communication style in this argument:\n\n{text}",
            }
        ],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Model returned non-JSON response: {raw!r}") from e

    focus_delta = float(parsed.get("focus_delta", 0.0))
    stance_delta = float(parsed.get("stance_delta", 0.0))

    # Clamp deltas to valid range
    focus_delta = max(-10.0, min(10.0, focus_delta))
    stance_delta = max(-10.0, min(10.0, stance_delta))

    valid_axes = {"logic", "narrative", "neutral"}
    focus_axis = parsed.get("focus_axis", "neutral")
    stance_axis = parsed.get("stance_axis", "neutral")

    if focus_axis not in valid_axes:
        focus_axis = "neutral"
    if stance_axis not in valid_axes:
        stance_axis = "neutral"

    return MicroArgumentResult(
        text=text,
        focus_axis=focus_axis,
        stance_axis=stance_axis,
        focus_delta=round(focus_delta, 1),
        stance_delta=round(stance_delta, 1),
        reasoning=parsed.get("reasoning", ""),
    )


# ---------------------------------------------------------------------------
# Archetype mapping
# ---------------------------------------------------------------------------

def map_to_archetype(
    focus_score: float,
    stance_score: float,
    *,
    neutral_band: int = NEUTRAL_BAND,
) -> SuperpowerType | Literal["Undetermined"]:
    """
    Map two axis scores to an archetype.

    Quadrant mapping:
      Logic (+) + Advocacy (+)  →  Inquisitor
      Narrative (−) + Advocacy (+)  →  Firestarter
      Logic (+) + Analysis (−)  →  Architect
      Narrative (−) + Analysis (−)  →  Bridge Builder

    Scores within ±neutral_band on either axis → "Undetermined".

    Parameters
    ----------
    focus_score:   -100…+100 (positive = Logic, negative = Narrative)
    stance_score:  -100…+100 (positive = Advocacy, negative = Analysis)
    neutral_band:  scores within ±this value are treated as undetermined (default: 15)
    """
    focus_undetermined = abs(focus_score) <= neutral_band
    stance_undetermined = abs(stance_score) <= neutral_band

    if focus_undetermined or stance_undetermined:
        return "Undetermined"

    logic = focus_score > 0
    advocacy = stance_score > 0

    if logic and advocacy:
        return "Inquisitor"
    if not logic and advocacy:
        return "Firestarter"
    if logic and not advocacy:
        return "Architect"
    # not logic and not advocacy
    return "Bridge Builder"


# ---------------------------------------------------------------------------
# Final result builder
# ---------------------------------------------------------------------------

def build_result(
    axes: ScoredAxes,
    *,
    micro_argument: MicroArgumentResult | None = None,
    neutral_band: int = NEUTRAL_BAND,
) -> AssessmentResult:
    """
    Combine Likert axis scores and optional micro-argument adjustment into a final result.

    Micro-argument deltas are added to axis scores (clamped to ±100).
    Confidence is recalculated with the has_micro_argument flag when a micro-argument is present.

    Parameters
    ----------
    axes:           Output of score_responses().
    micro_argument: Optional output of classify_micro_argument(). Pass None if skipped.
    neutral_band:   Neutral zone width per axis (default: 15).

    Returns
    -------
    AssessmentResult with archetype, confidence, and explanation note.
    """
    focus_score = axes.focus_score
    stance_score = axes.stance_score

    # Apply micro-argument deltas (scale: delta of ±10 on a ±100 axis)
    if micro_argument is not None:
        focus_score = round(
            max(-100.0, min(100.0, focus_score + micro_argument.focus_delta)), 1
        )
        stance_score = round(
            max(-100.0, min(100.0, stance_score + micro_argument.stance_delta)), 1
        )

    archetype = map_to_archetype(focus_score, stance_score, neutral_band=neutral_band)

    in_neutral_band = {
        "focus": abs(focus_score) <= neutral_band,
        "stance": abs(stance_score) <= neutral_band,
    }

    # Recompute confidence with micro-argument flag
    confidence = axes.confidence
    if micro_argument is not None:
        # Recalculate with has_micro_argument=True — adds the behavioral bonus
        # We don't have the raw scored lists here, so we apply the bonus directly
        confidence = round(min(1.0, confidence + 0.10), 4)

    # Build human-readable note
    if archetype == "Undetermined":
        neutral_axes = [ax for ax, flag in in_neutral_band.items() if flag]
        note = (
            f"Your {' and '.join(neutral_axes)} score(s) fall within the ±{neutral_band}-point "
            "neutral zone. More sessions will sharpen the classification."
        )
    else:
        focus_label = "Logic" if focus_score > 0 else "Narrative"
        stance_label = "Advocacy" if stance_score > 0 else "Analysis"
        note = (
            f"{archetype}: {focus_label} + {stance_label}. "
            f"Focus score {focus_score:+.0f}, Stance score {stance_score:+.0f}."
        )
        if confidence < 0.55:
            note += " Low confidence — consider retaking after your next meeting."

    return AssessmentResult(
        focus_score=focus_score,
        stance_score=stance_score,
        archetype=archetype,
        confidence=confidence,
        micro_argument=micro_argument,
        in_neutral_band=in_neutral_band,
        items_used=axes.items_used,
        note=note,
    )
