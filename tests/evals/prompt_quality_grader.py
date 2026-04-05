"""
LLM-as-judge eval — Sonnet grades Haiku coaching prompts on 5 quality rubrics.

Extends coaching_prompts.py (which tests structural properties) with *quality*
assessment via Sonnet judgment. Catches prompt drift: verbosity, loss of
specificity, preamble creep, negative framing, generic advice.

Architecture:
  Fixture → CoachingEngine (Haiku) → prompt text
    → Sonnet judge grades 5 rubrics → Y/N per rubric → assert ≥4/5 pass

Rubric dimensions (atomic Y/N):
  1. Actionable — tells the user exactly what to do or say next
  2. Specific   — references the person, archetype, or situation
  3. Concise    — ≤20 words, no preamble or filler
  4. Positive   — frames action positively (what to do, not avoid)
  5. Contextual — reflects ELM state or group dynamic, not just archetype

Skip condition: ANTHROPIC_API_KEY not set → all tests skip.
Run locally with:

    ANTHROPIC_API_KEY=<key> pytest tests/evals/prompt_quality_grader.py -v
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import pytest

logger = logging.getLogger(__name__)

from backend.coaching_engine import CoachingEngine, CoachingPrompt
from backend.elm_detector import ELMEvent
from backend.models import ProfileSnapshot
from backend.profiler import WindowClassification

# ---------------------------------------------------------------------------
# Skip marker
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.eval,
    pytest.mark.timeout(120),
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="requires ANTHROPIC_API_KEY — skipped in CI",
    ),
]

# ---------------------------------------------------------------------------
# Shared fixture builders (mirror coaching_prompts.py)
# ---------------------------------------------------------------------------

_SPEAKER = "speaker_1"


def _engine() -> CoachingEngine:
    """Engine with generous timeout (real API call) and zero cadence floors."""
    return CoachingEngine(
        user_speaker="speaker_0",
        haiku_timeout_s=30.0,
        elm_cadence_floor_s=0.0,
        general_cadence_floor_s=0.0,
    )


def _participant(superpower: str, *, speaker_id: str = _SPEAKER) -> WindowClassification:
    focus = 55.0 if superpower in ("Architect", "Inquisitor") else -55.0
    stance = 55.0 if superpower in ("Inquisitor", "Firestarter") else -55.0
    return WindowClassification(
        speaker_id=speaker_id,
        superpower=superpower,  # type: ignore[arg-type]
        confidence=0.72,
        focus_score=focus,
        stance_score=stance,
        utterance_count=5,
    )


def _user(
    archetype: str,
    context: str = "board",
    *,
    context_shifts: bool = False,
    core_archetype: str | None = None,
) -> ProfileSnapshot:
    focus = 55.0 if archetype in ("Architect", "Inquisitor") else -55.0
    stance = 55.0 if archetype in ("Inquisitor", "Firestarter") else -55.0
    ca = core_archetype or archetype
    return ProfileSnapshot(
        archetype=archetype,  # type: ignore[arg-type]
        focus_score=focus,
        stance_score=stance,
        focus_variance=0.0,
        stance_variance=0.0,
        confidence=0.72,
        context=context,
        context_sessions=5,
        is_context_specific=context_shifts,
        core_archetype=ca,  # type: ignore[arg-type]
        core_sessions=10,
        context_shifts=context_shifts,
    )


def _ego_event(text: str = "I disagree — we've always done it differently.", speaker_id: str = _SPEAKER) -> ELMEvent:
    return ELMEvent(speaker_id=speaker_id, state="ego_threat", evidence=["I disagree"], utterance=text)


def _shortcut_event(text: str = "Yes, absolutely, sounds good.", speaker_id: str = _SPEAKER) -> ELMEvent:
    return ELMEvent(speaker_id=speaker_id, state="shortcut", evidence=["absolutely"], utterance=text)


def _consensus_event(text: str = "We all agree — let's move on.", speaker_id: str = _SPEAKER) -> ELMEvent:
    return ELMEvent(speaker_id=speaker_id, state="consensus_protection", evidence=["we all agree"], utterance=text)


# ---------------------------------------------------------------------------
# Sonnet grader
# ---------------------------------------------------------------------------

RUBRIC_SYSTEM = """\
You are an expert evaluator of real-time coaching prompts for business meetings.

You will receive:
1. A coaching prompt (the text shown to the user during a meeting)
2. The scenario context (user archetype, counterpart archetype, trigger)

Grade the prompt on exactly 5 dimensions. For each, answer Y or N:

1. ACTIONABLE: Does this prompt tell the user exactly what to do or say next? \
A vague "be more empathetic" is N. "Acknowledge Sarah's concern, then share one data point" is Y.

2. SPECIFIC: Does the prompt reference the specific person, archetype, or situation \
rather than being generic advice anyone could use in any meeting?

3. CONCISE: Is the prompt 20 words or fewer with no preamble, filler, hedging, \
or meta-commentary like "Here's a tip:" or "You might want to..."?

4. POSITIVE_FRAME: Does the prompt frame the action positively (what TO do) rather \
than negatively (what to AVOID)? "Ask a question" is Y. "Don't lecture" is N.

5. CONTEXTUAL: Does the prompt reflect the current trigger (ego threat, shortcut, \
consensus protection, or cadence context) and not just the counterpart's archetype? \
A prompt that would be equally valid without knowing the trigger is N.

Respond with ONLY a JSON object mapping each rubric name to "Y" or "N":
{"ACTIONABLE": "Y", "SPECIFIC": "Y", "CONCISE": "Y", "POSITIVE_FRAME": "Y", "CONTEXTUAL": "N"}
"""


async def grade_prompt(
    prompt_text: str,
    user_archetype: str,
    counterpart_archetype: str,
    trigger: str,
    context: str = "board",
) -> dict[str, bool]:
    """
    Call Sonnet to grade a coaching prompt on 5 rubric dimensions.

    Returns {rubric_name: True/False}.
    """
    from anthropic import AsyncAnthropic

    scenario = (
        f"User archetype: {user_archetype}\n"
        f"Counterpart archetype: {counterpart_archetype}\n"
        f"Trigger: {trigger}\n"
        f"Meeting context: {context}\n"
        f"Coaching prompt to evaluate: \"{prompt_text}\""
    )

    _ALL_FALSE = {
        "ACTIONABLE": False, "SPECIFIC": False, "CONCISE": False,
        "POSITIVE_FRAME": False, "CONTEXTUAL": False,
    }

    client = AsyncAnthropic()
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                system=RUBRIC_SYSTEM,
                messages=[{"role": "user", "content": scenario}],
            ),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        logger.warning("Sonnet grader timed out after 30s")
        return _ALL_FALSE

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3].rstrip()

    try:
        grades = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Sonnet returned malformed JSON: %s", raw[:200])
        return _ALL_FALSE

    return {
        k: v.upper() == "Y"
        for k, v in grades.items()
    }


# ---------------------------------------------------------------------------
# 15 test scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    # ELM ego_threat (1-5)
    {
        "name": "ego_threat_architect_vs_inquisitor",
        "user": "Architect", "counterpart": "Inquisitor", "trigger": "ego_threat",
        "elm_text": "I disagree — that data doesn't make sense.",
    },
    {
        "name": "ego_threat_firestarter_vs_bridge_builder",
        "user": "Firestarter", "counterpart": "Bridge Builder", "trigger": "ego_threat",
        "elm_text": "I'm not buying this — we've always led with story.",
    },
    {
        "name": "ego_threat_bridge_builder_vs_architect",
        "user": "Bridge Builder", "counterpart": "Architect", "trigger": "ego_threat",
        "elm_text": "That doesn't add up — show me the actual numbers.",
    },
    {
        "name": "ego_threat_inquisitor_vs_firestarter",
        "user": "Inquisitor", "counterpart": "Firestarter", "trigger": "ego_threat",
        "elm_text": "You don't understand what the team agreed to.",
    },
    {
        "name": "ego_threat_architect_vs_architect",
        "user": "Architect", "counterpart": "Architect", "trigger": "ego_threat",
        "elm_text": "Your analysis is fundamentally flawed.",
    },
    # Shortcut (6-7)
    {
        "name": "shortcut_firestarter_vs_inquisitor",
        "user": "Firestarter", "counterpart": "Inquisitor", "trigger": "shortcut",
        "elm_text": "Yes, sounds good, agreed.",
    },
    {
        "name": "shortcut_bridge_builder_vs_architect",
        "user": "Bridge Builder", "counterpart": "Architect", "trigger": "shortcut",
        "elm_text": "Absolutely, totally, that works for me.",
    },
    # Consensus (8)
    {
        "name": "consensus_inquisitor_vs_bridge_builder",
        "user": "Inquisitor", "counterpart": "Bridge Builder", "trigger": "consensus_protection",
        "elm_text": "I think we're all aligned — let's not debate this further.",
    },
    # General cadence — self layer (9-11)
    {
        "name": "cadence_self_architect_board",
        "user": "Architect", "counterpart": "Inquisitor", "trigger": "cadence:self",
        "context": "board",
    },
    {
        "name": "cadence_self_firestarter_client",
        "user": "Firestarter", "counterpart": "Architect", "trigger": "cadence:self",
        "context": "client",
    },
    {
        "name": "cadence_self_bridge_builder_team",
        "user": "Bridge Builder", "counterpart": "Firestarter", "trigger": "cadence:self",
        "context": "team",
    },
    # General cadence — additional self-layer contexts (12-13)
    {
        "name": "cadence_self_inquisitor_team",
        "user": "Inquisitor", "counterpart": "Bridge Builder", "trigger": "cadence:self",
        "context": "team",
    },
    {
        "name": "cadence_self_architect_1on1",
        "user": "Architect", "counterpart": "Firestarter", "trigger": "cadence:self",
        "context": "1:1",
    },
    # Same-type pairings (14-15)
    {
        "name": "consensus_bridge_builder_vs_bridge_builder",
        "user": "Bridge Builder", "counterpart": "Bridge Builder", "trigger": "consensus_protection",
        "elm_text": "Let's just go with what everyone seems comfortable with.",
    },
    {
        "name": "ego_threat_firestarter_vs_firestarter",
        "user": "Firestarter", "counterpart": "Firestarter", "trigger": "ego_threat",
        "elm_text": "My vision is the one that will actually inspire the team, not yours.",
    },
]


async def _generate_prompt(scenario: dict) -> CoachingPrompt | None:
    """Generate a coaching prompt from a scenario dict."""
    engine = _engine()
    trigger = scenario["trigger"]
    context = scenario.get("context", "board")

    elm_event = None
    if trigger == "ego_threat":
        elm_event = _ego_event(scenario["elm_text"])
    elif trigger == "shortcut":
        elm_event = _shortcut_event(scenario["elm_text"])
    elif trigger == "consensus_protection":
        elm_event = _consensus_event(scenario["elm_text"])
    # cadence triggers: elm_event stays None

    return await engine.process(
        elm_event=elm_event,
        participant_profile=_participant(scenario["counterpart"]),
        user_profile=_user(scenario["user"], context=context),
    )


# ---------------------------------------------------------------------------
# Individual scenario tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
async def test_scenario_quality(scenario: dict):
    """Each scenario should pass ≥4/5 rubrics."""
    prompt = await _generate_prompt(scenario)
    assert prompt is not None, f"No prompt generated for {scenario['name']}"

    grades = await grade_prompt(
        prompt_text=prompt.text,
        user_archetype=scenario["user"],
        counterpart_archetype=scenario["counterpart"],
        trigger=scenario["trigger"],
        context=scenario.get("context", "board"),
    )

    passed = sum(1 for v in grades.values() if v)
    failed_rubrics = [k for k, v in grades.items() if not v]

    assert passed >= 4, (
        f"{scenario['name']}: {passed}/5 rubrics passed. "
        f"Failed: {failed_rubrics}. "
        f"Prompt: {prompt.text!r}"
    )


# ---------------------------------------------------------------------------
# Aggregate quality gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_aggregate_rubric_pass_rate():
    """Across all 15 scenarios, ≥80% of rubric grades should pass (60/75)."""
    total_rubrics = 0
    passed_rubrics = 0
    hard_failures = []

    for scenario in SCENARIOS:
        prompt = await _generate_prompt(scenario)
        if prompt is None:
            hard_failures.append(f"{scenario['name']}: no prompt generated")
            total_rubrics += 5
            continue

        grades = await grade_prompt(
            prompt_text=prompt.text,
            user_archetype=scenario["user"],
            counterpart_archetype=scenario["counterpart"],
            trigger=scenario["trigger"],
            context=scenario.get("context", "board"),
        )

        scenario_passed = sum(1 for v in grades.values() if v)
        passed_rubrics += scenario_passed
        total_rubrics += len(grades)

        # Hard failure: any scenario ≤2/5
        if scenario_passed <= 2:
            failed = [k for k, v in grades.items() if not v]
            hard_failures.append(
                f"{scenario['name']}: {scenario_passed}/5 (hard fail). "
                f"Failed: {failed}. Prompt: {prompt.text!r}"
            )

    pct = passed_rubrics / total_rubrics * 100 if total_rubrics else 0

    assert not hard_failures, (
        f"Hard failures (≤2/5 rubrics):\n" + "\n".join(hard_failures)
    )

    assert pct >= 80, (
        f"Aggregate rubric pass rate {passed_rubrics}/{total_rubrics} ({pct:.0f}%) < 80%"
    )
