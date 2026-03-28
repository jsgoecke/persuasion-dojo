"""
LLM eval tests for CoachingEngine prompt generation.

10 fixtures covering the matrix of Superpower type × ELM state.
Each test makes a real Claude Haiku API call and asserts structural
and quality properties on the returned CoachingPrompt — not exact text.

Property assertions (see _assert_prompt_quality):
  - ≤ 18 words
  - No preamble ("Here is a tip:", "I suggest...", etc.)
  - Positive framing (not avoidance-first "Don't...", "Avoid...", "Stop...")
  - layer  matches the expected coaching layer
  - triggered_by matches the expected trigger string
  - is_fallback is False (fresh generation, not a cached fallback)
  - speaker_id matches the counterpart for ELM-triggered prompts

Fixture matrix:
  ELM ego_threat   (4):  Inquisitor×Architect, Firestarter×BridgeBuilder,
                          Architect×Firestarter, BridgeBuilder×Inquisitor
  ELM shortcut     (2):  Inquisitor agreeing fast, Firestarter rubber-stamping
  ELM consensus    (1):  BridgeBuilder closing down dissent
  General cadence  (3):  Architect/board, Firestarter/client, Inquisitor+shift/team

Skip condition: tests are skipped when ANTHROPIC_API_KEY is not set so CI
passes without billing.  Run locally with:

    ANTHROPIC_API_KEY=<key> pytest tests/evals/coaching_prompts.py -v
"""

from __future__ import annotations

import os

import pytest

from backend.coaching_engine import CoachingEngine, CoachingPrompt
from backend.elm_detector import ELMEvent
from backend.models import ProfileSnapshot
from backend.profiler import WindowClassification

# ---------------------------------------------------------------------------
# Skip marker
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="requires ANTHROPIC_API_KEY — skipped in CI",
    ),
]

# ---------------------------------------------------------------------------
# Shared fixture builders
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
    """Build a WindowClassification for a counterpart with the given archetype."""
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
    """Build a ProfileSnapshot for the app user with the given archetype."""
    focus = 55.0 if archetype in ("Architect", "Inquisitor") else -55.0
    stance = 55.0 if archetype in ("Inquisitor", "Firestarter") else -55.0
    ca = core_archetype or archetype
    return ProfileSnapshot(
        archetype=archetype,  # type: ignore[arg-type]
        focus_score=focus,
        stance_score=stance,
        confidence=0.72,
        context=context,
        context_sessions=5,
        is_context_specific=context_shifts,
        core_archetype=ca,  # type: ignore[arg-type]
        core_sessions=10,
        context_shifts=context_shifts,
    )


def _ego_event(
    text: str = "I disagree — we've always done it differently.",
    speaker_id: str = _SPEAKER,
) -> ELMEvent:
    return ELMEvent(
        speaker_id=speaker_id,
        state="ego_threat",
        evidence=["I disagree", "we've always done"],
        utterance=text,
    )


def _shortcut_event(
    text: str = "Yes, absolutely, sounds good.",
    speaker_id: str = _SPEAKER,
) -> ELMEvent:
    return ELMEvent(
        speaker_id=speaker_id,
        state="shortcut",
        evidence=["absolutely", "sounds good"],
        utterance=text,
    )


def _consensus_event(
    text: str = "I think we all agree — let's move on and not debate this.",
    speaker_id: str = _SPEAKER,
) -> ELMEvent:
    return ELMEvent(
        speaker_id=speaker_id,
        state="consensus_protection",
        evidence=["we all agree", "let's move on", "not debate this"],
        utterance=text,
    )


# ---------------------------------------------------------------------------
# Quality assertion helper
# ---------------------------------------------------------------------------

def _assert_prompt_quality(
    prompt: CoachingPrompt,
    *,
    layer: str,
    triggered_by: str,
    speaker_id: str = "",
) -> None:
    """
    Assert the structural and quality properties every coaching prompt must satisfy.

    This is property-based — we test the rules the system prompt enforces,
    not exact text, so results remain valid across minor model version bumps.
    """
    assert prompt is not None, "engine returned None — no prompt generated"
    assert not prompt.is_fallback, (
        f"expected a fresh prompt, not a cached fallback: {prompt.text!r}"
    )
    assert prompt.layer == layer, (
        f"wrong layer: got {prompt.layer!r}, expected {layer!r}"
    )
    assert prompt.triggered_by == triggered_by, (
        f"wrong trigger: got {prompt.triggered_by!r}, expected {triggered_by!r}"
    )
    if speaker_id:
        assert prompt.speaker_id == speaker_id, (
            f"wrong speaker: got {prompt.speaker_id!r}, expected {speaker_id!r}"
        )

    # ── Word count ────────────────────────────────────────────────────────
    words = prompt.text.split()
    assert len(words) <= 18, (
        f"prompt too long ({len(words)} words): {prompt.text!r}"
    )
    assert len(words) >= 3, (
        f"prompt suspiciously short ({len(words)} words): {prompt.text!r}"
    )

    # ── No preamble ───────────────────────────────────────────────────────
    text_lower = prompt.text.lower()
    preambles = (
        "here is",
        "here's a",
        "here's your",
        "i suggest",
        "i recommend",
        "you should",
        "try to ",
        "coaching tip:",
        "tip:",
    )
    for preamble in preambles:
        assert not text_lower.startswith(preamble), (
            f"preamble detected ({preamble!r}): {prompt.text!r}"
        )

    # ── Positive framing — not avoidance-first ────────────────────────────
    first_word = words[0].rstrip(".,!?").lower()
    avoidance_starters = {"don't", "dont", "avoid", "stop", "never", "refrain", "do not"}
    assert first_word not in avoidance_starters, (
        f"avoidance framing (starts with {first_word!r}): {prompt.text!r}"
    )

    # ── Coherent text ─────────────────────────────────────────────────────
    assert prompt.text.strip(), "prompt text is blank"


# ---------------------------------------------------------------------------
# Fixture 1: Ego threat — Inquisitor counterpart, Architect user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ego_threat_inquisitor_vs_architect():
    """
    Inquisitor counterpart (logic + advocate) pushes back defensively.
    User is an Architect (logic + analyst).
    Expected: audience-layer prompt that de-escalates and matches Inquisitor's
    need for evidence rather than assertion.
    """
    engine = _engine()
    prompt = await engine.process(
        elm_event=_ego_event(
            "I disagree with that framing — that data doesn't make sense."
        ),
        participant_profile=_participant("Inquisitor"),
        user_profile=_user("Architect"),
    )
    _assert_prompt_quality(
        prompt,
        layer="audience",
        triggered_by="elm:ego_threat",
        speaker_id=_SPEAKER,
    )


# ---------------------------------------------------------------------------
# Fixture 2: Ego threat — Firestarter counterpart, Bridge Builder user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ego_threat_firestarter_vs_bridge_builder():
    """
    Firestarter counterpart (narrative + advocate) feels identity-threatened.
    User is a Bridge Builder (narrative + analyst).
    Expected: audience-layer prompt to acknowledge energy and restore safety.
    """
    engine = _engine()
    prompt = await engine.process(
        elm_event=_ego_event(
            "With all due respect, I'm not buying this — we've always led with story."
        ),
        participant_profile=_participant("Firestarter"),
        user_profile=_user("Bridge Builder"),
    )
    _assert_prompt_quality(
        prompt,
        layer="audience",
        triggered_by="elm:ego_threat",
        speaker_id=_SPEAKER,
    )


# ---------------------------------------------------------------------------
# Fixture 3: Ego threat — Architect counterpart, Firestarter user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ego_threat_architect_vs_firestarter():
    """
    Architect counterpart (logic + analyst) challenges the user's narrative approach.
    User is a Firestarter (narrative + advocate).
    Expected: audience-layer prompt that grounds the Firestarter's narrative in
    data to satisfy the Architect's systematic mindset.
    """
    engine = _engine()
    prompt = await engine.process(
        elm_event=_ego_event(
            "That doesn't add up — I'm not convinced without the actual numbers."
        ),
        participant_profile=_participant("Architect"),
        user_profile=_user("Firestarter"),
    )
    _assert_prompt_quality(
        prompt,
        layer="audience",
        triggered_by="elm:ego_threat",
        speaker_id=_SPEAKER,
    )


# ---------------------------------------------------------------------------
# Fixture 4: Ego threat — Bridge Builder counterpart, Inquisitor user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ego_threat_bridge_builder_vs_inquisitor():
    """
    Bridge Builder counterpart (narrative + analyst) feels the Inquisitor's
    relentless questioning is threatening group cohesion.
    Expected: audience-layer prompt to soften the challenge.
    """
    engine = _engine()
    prompt = await engine.process(
        elm_event=_ego_event(
            "Actually, I think you don't understand what the team agreed to."
        ),
        participant_profile=_participant("Bridge Builder"),
        user_profile=_user("Inquisitor"),
    )
    _assert_prompt_quality(
        prompt,
        layer="audience",
        triggered_by="elm:ego_threat",
        speaker_id=_SPEAKER,
    )


# ---------------------------------------------------------------------------
# Fixture 5: Shortcut — Inquisitor counterpart agreeing without engagement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shortcut_inquisitor_agreeing_fast():
    """
    Inquisitor counterpart (normally data-demanding) has been agreeing with
    everything — no questions, no pushback. Peripheral route engagement.
    Expected: audience-layer prompt inviting the Inquisitor's natural scrutiny.
    """
    engine = _engine()
    prompt = await engine.process(
        elm_event=_shortcut_event("Yes, sounds good, agreed."),
        participant_profile=_participant("Inquisitor"),
        user_profile=_user("Architect"),
    )
    _assert_prompt_quality(
        prompt,
        layer="audience",
        triggered_by="elm:shortcut",
        speaker_id=_SPEAKER,
    )


# ---------------------------------------------------------------------------
# Fixture 6: Shortcut — Firestarter counterpart rubber-stamping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shortcut_firestarter_rubber_stamping():
    """
    Firestarter counterpart (normally opinionated storyteller) is giving
    reflexive agreement with no energy or narrative pushback.
    Expected: audience-layer prompt that invites the Firestarter's real view.
    """
    engine = _engine()
    prompt = await engine.process(
        elm_event=_shortcut_event("Absolutely, totally, that works for me."),
        participant_profile=_participant("Firestarter"),
        user_profile=_user("Bridge Builder"),
    )
    _assert_prompt_quality(
        prompt,
        layer="audience",
        triggered_by="elm:shortcut",
        speaker_id=_SPEAKER,
    )


# ---------------------------------------------------------------------------
# Fixture 7: Consensus protection — Bridge Builder closing down dissent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consensus_protection_bridge_builder():
    """
    Bridge Builder counterpart (harmony-oriented) prematurely signals closure
    to avoid conflict — classic groupthink risk.
    Expected: audience-layer prompt to explicitly open space for dissent.
    """
    engine = _engine()
    prompt = await engine.process(
        elm_event=_consensus_event(
            "I think we're all aligned here — let's not debate this further."
        ),
        participant_profile=_participant("Bridge Builder"),
        user_profile=_user("Inquisitor"),
    )
    _assert_prompt_quality(
        prompt,
        layer="audience",
        triggered_by="elm:consensus_protection",
        speaker_id=_SPEAKER,
    )


# ---------------------------------------------------------------------------
# Fixture 8: General cadence — Architect user in board meeting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cadence_architect_board():
    """
    No ELM event. Architect user (logic + analyst) in a board meeting
    with an Inquisitor counterpart.
    Expected: self-layer cadence prompt tailored to the board context.
    """
    engine = _engine()
    prompt = await engine.process(
        elm_event=None,
        participant_profile=_participant("Inquisitor"),
        user_profile=_user("Architect", context="board"),
    )
    _assert_prompt_quality(
        prompt,
        layer="self",
        triggered_by="cadence:self",
    )


# ---------------------------------------------------------------------------
# Fixture 9: General cadence — Firestarter user in client pitch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cadence_firestarter_client():
    """
    No ELM event. Firestarter user (narrative + advocate) in a client meeting
    with an Architect counterpart.
    Expected: self-layer cadence prompt that helps the Firestarter modulate
    their energy for a more analytical audience.
    """
    engine = _engine()
    prompt = await engine.process(
        elm_event=None,
        participant_profile=_participant("Architect"),
        user_profile=_user("Firestarter", context="client"),
    )
    _assert_prompt_quality(
        prompt,
        layer="self",
        triggered_by="cadence:self",
    )


# ---------------------------------------------------------------------------
# Fixture 10: General cadence — Inquisitor user with context shift
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cadence_inquisitor_context_shift():
    """
    No ELM event. Inquisitor user (core) who shows as Firestarter in team
    meetings (context shift). Counterpart is a Bridge Builder.
    Expected: self-layer prompt that acknowledges the context shift — the
    coaching note should reflect the user's situational style difference.
    """
    engine = _engine()
    prompt = await engine.process(
        elm_event=None,
        participant_profile=_participant("Bridge Builder"),
        user_profile=_user(
            "Firestarter",  # context-specific archetype
            context="team",
            context_shifts=True,
            core_archetype="Inquisitor",
        ),
    )
    _assert_prompt_quality(
        prompt,
        layer="self",
        triggered_by="cadence:self",
    )
