"""
Tests for backend/coaching_engine.py — real-time coaching prompt generation.

Coverage:
  - User speaking suppression
  - ELM cadence floor (10 s)
  - General cadence floor (60 s)
  - ELM prompts: layer, triggered_by, speaker_id, text, is_fallback=False
  - General prompts: layer, triggered_by, is_fallback=False
  - Fallback: timeout → cached; first-call timeout → None; exception → same path
  - Cache management: success updates cache; fallback does not overwrite cache
  - Reset: clears last_prompt_time and cache
  - Priority: ELM floor < general floor
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.coaching_engine import CoachingEngine, CoachingPrompt
from backend.elm_detector import ELMEvent
from backend.models import ProfileSnapshot
from backend.profiler import WindowClassification


# ---------------------------------------------------------------------------
# Test helpers / factories
# ---------------------------------------------------------------------------

_RESPONSE_TEXT = "Ask a clarifying question right now."


def make_mock_client(text: str = _RESPONSE_TEXT) -> Any:
    """Anthropic client that immediately returns a fixed coaching tip."""
    content = MagicMock()
    content.text = text
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


def make_timeout_client() -> Any:
    """Anthropic client that always raises asyncio.TimeoutError."""
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=asyncio.TimeoutError())
    return client


def make_error_client() -> Any:
    """Anthropic client that always raises a generic API error."""
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=Exception("API error"))
    return client


def make_engine(
    client: Any = None,
    elm_floor: float = 0.0,
    general_floor: float = 0.0,
    timeout: float = 999.0,
    user_archetype: str | None = None,
    participants: list[dict[str, str]] | None = None,
) -> CoachingEngine:
    """Engine with floors disabled by default for most tests."""
    return CoachingEngine(
        user_speaker="speaker_0",
        anthropic_client=client or make_mock_client(),
        elm_cadence_floor_s=elm_floor,
        general_cadence_floor_s=general_floor,
        haiku_timeout_s=timeout,
        user_archetype=user_archetype,
        participants=participants,
    )


def make_elm_event(
    state: str = "ego_threat",
    speaker_id: str = "speaker_1",
) -> ELMEvent:
    return ELMEvent(
        speaker_id=speaker_id,
        state=state,
        evidence=["I disagree"],
        utterance="I disagree with this completely.",
    )


def make_snapshot(
    archetype: str = "Inquisitor",
    core_archetype: str = "Inquisitor",
    context: str = "board",
) -> ProfileSnapshot:
    return ProfileSnapshot(
        archetype=archetype,
        focus_score=60.0,
        stance_score=40.0,
        focus_variance=0.0,
        stance_variance=0.0,
        confidence=0.7,
        context=context,
        context_sessions=5,
        is_context_specific=True,
        core_archetype=core_archetype,
        core_sessions=8,
        context_shifts=(
            archetype != core_archetype
            and archetype != "Undetermined"
            and core_archetype != "Undetermined"
        ),
    )


def make_classification(superpower: str = "Architect") -> WindowClassification:
    return WindowClassification(
        speaker_id="speaker_1",
        superpower=superpower,
        confidence=0.6,
        focus_score=70.0,
        stance_score=-30.0,
        utterance_count=4,
    )


# ---------------------------------------------------------------------------
# User speaking suppression
# ---------------------------------------------------------------------------

class TestUserSpeakingSuppression:
    @pytest.mark.asyncio
    async def test_elm_event_suppressed_when_user_speaking(self):
        """ELM (audience-layer) prompts are suppressed when user is speaking,
        but a self-layer general prompt fires instead."""
        engine = make_engine()
        result = await engine.process(
            elm_event=make_elm_event(),
            user_is_speaking=True,
        )
        # ELM prompt suppressed, but self-layer general fires
        assert result is not None
        assert result.triggered_by == "cadence:self"

    @pytest.mark.asyncio
    async def test_general_cadence_fires_when_user_speaking(self):
        """Self-layer prompts fire on user utterances — this is how
        'you've been advocating too long' coaching works."""
        engine = make_engine()
        result = await engine.process(user_is_speaking=True)
        assert result is not None
        assert result.layer == "self"

    @pytest.mark.asyncio
    async def test_prompt_fires_when_user_not_speaking(self):
        engine = make_engine()
        result = await engine.process(
            elm_event=make_elm_event(),
            user_is_speaking=False,
        )
        assert result is not None


# ---------------------------------------------------------------------------
# ELM cadence floor
# ---------------------------------------------------------------------------

class TestELMCadenceFloor:
    @pytest.mark.asyncio
    async def test_elm_blocked_within_floor(self):
        engine = make_engine(elm_floor=10.0)
        # Fire first prompt to set last_prompt_time
        await engine.process(elm_event=make_elm_event())
        # Immediately try again — within floor
        result = await engine.process(elm_event=make_elm_event())
        assert result is None

    @pytest.mark.asyncio
    async def test_elm_fires_after_floor_elapsed(self):
        engine = make_engine(elm_floor=10.0)
        # Simulate last prompt was 15 seconds ago
        engine._last_prompt_time = time.monotonic() - 15.0
        result = await engine.process(elm_event=make_elm_event())
        assert result is not None

    @pytest.mark.asyncio
    async def test_elm_fires_when_no_prior_prompt(self):
        engine = make_engine(elm_floor=10.0)
        # _last_prompt_time starts at 0.0 → elapsed is very large
        result = await engine.process(elm_event=make_elm_event())
        assert result is not None

    @pytest.mark.asyncio
    async def test_elm_uses_its_own_floor_not_general_floor(self):
        # ELM floor=10, general floor=60. After 15s, ELM should fire.
        engine = make_engine(elm_floor=10.0, general_floor=60.0)
        engine._last_prompt_time = time.monotonic() - 15.0
        result = await engine.process(elm_event=make_elm_event())
        assert result is not None

    @pytest.mark.asyncio
    async def test_general_blocked_when_only_15s_elapsed(self):
        engine = make_engine(elm_floor=10.0, general_floor=60.0)
        engine._last_prompt_time = time.monotonic() - 15.0
        # No ELM event — checks general floor (60s), 15s < 60s → blocked
        result = await engine.process(elm_event=None)
        assert result is None


# ---------------------------------------------------------------------------
# General cadence floor
# ---------------------------------------------------------------------------

class TestGeneralCadenceFloor:
    @pytest.mark.asyncio
    async def test_general_blocked_within_60s(self):
        engine = make_engine(general_floor=60.0)
        await engine.process(elm_event=None)
        result = await engine.process(elm_event=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_general_fires_after_60s(self):
        engine = make_engine(general_floor=60.0)
        engine._last_prompt_time = time.monotonic() - 65.0
        result = await engine.process(elm_event=None)
        assert result is not None

    @pytest.mark.asyncio
    async def test_general_fires_on_first_call(self):
        engine = make_engine(general_floor=60.0)
        result = await engine.process(elm_event=None)
        assert result is not None


# ---------------------------------------------------------------------------
# ELM prompt properties
# ---------------------------------------------------------------------------

class TestELMPromptProperties:
    @pytest.mark.asyncio
    async def test_elm_prompt_layer_is_audience(self):
        engine = make_engine()
        result = await engine.process(elm_event=make_elm_event("ego_threat"))
        assert result is not None
        assert result.layer == "audience"

    @pytest.mark.asyncio
    async def test_elm_ego_threat_triggered_by(self):
        engine = make_engine()
        result = await engine.process(elm_event=make_elm_event("ego_threat"))
        assert result is not None
        assert result.triggered_by == "elm:ego_threat"

    @pytest.mark.asyncio
    async def test_elm_shortcut_triggered_by(self):
        engine = make_engine()
        result = await engine.process(elm_event=make_elm_event("shortcut"))
        assert result is not None
        assert result.triggered_by == "elm:shortcut"

    @pytest.mark.asyncio
    async def test_elm_consensus_triggered_by(self):
        engine = make_engine()
        result = await engine.process(
            elm_event=make_elm_event("consensus_protection")
        )
        assert result is not None
        assert result.triggered_by == "elm:consensus_protection"

    @pytest.mark.asyncio
    async def test_elm_speaker_id_propagated(self):
        engine = make_engine()
        result = await engine.process(
            elm_event=make_elm_event(speaker_id="speaker_3")
        )
        assert result is not None
        assert result.speaker_id == "speaker_3"

    @pytest.mark.asyncio
    async def test_elm_text_from_client(self):
        engine = make_engine(client=make_mock_client("Back off and ask a question."))
        result = await engine.process(elm_event=make_elm_event())
        assert result is not None
        assert result.text == "Back off and ask a question."

    @pytest.mark.asyncio
    async def test_elm_is_fallback_false(self):
        engine = make_engine()
        result = await engine.process(elm_event=make_elm_event())
        assert result is not None
        assert result.is_fallback is False

    @pytest.mark.asyncio
    async def test_elm_prompt_uses_participant_profile(self):
        """Participant profile fields are passed to the client (content check)."""
        client = make_mock_client()
        engine = make_engine(client=client)
        await engine.process(
            elm_event=make_elm_event(),
            participant_profile=make_classification("Bridge Builder"),
        )
        call_kwargs = client.messages.create.call_args
        user_content = call_kwargs[1]["messages"][0]["content"]
        assert "Bridge Builder" in user_content

    @pytest.mark.asyncio
    async def test_elm_prompt_uses_user_archetype(self):
        client = make_mock_client()
        engine = make_engine(client=client, user_archetype="Firestarter")
        await engine.process(
            elm_event=make_elm_event(),
        )
        call_kwargs = client.messages.create.call_args
        user_content = call_kwargs[1]["messages"][0]["content"]
        assert "Firestarter" in user_content


# ---------------------------------------------------------------------------
# General prompt properties
# ---------------------------------------------------------------------------

class TestGeneralPromptProperties:
    @pytest.mark.asyncio
    async def test_general_layer_is_self(self):
        engine = make_engine()
        result = await engine.process(elm_event=None)
        assert result is not None
        assert result.layer == "self"

    @pytest.mark.asyncio
    async def test_general_triggered_by_cadence_self(self):
        engine = make_engine()
        result = await engine.process(elm_event=None)
        assert result is not None
        assert result.triggered_by == "cadence:self"

    @pytest.mark.asyncio
    async def test_general_speaker_id_empty(self):
        engine = make_engine()
        result = await engine.process(elm_event=None)
        assert result is not None
        assert result.speaker_id == ""

    @pytest.mark.asyncio
    async def test_general_is_fallback_false(self):
        engine = make_engine()
        result = await engine.process(elm_event=None)
        assert result is not None
        assert result.is_fallback is False

    @pytest.mark.asyncio
    async def test_general_prompt_uses_user_profile_context(self):
        client = make_mock_client()
        engine = make_engine(client=client)
        await engine.process(
            elm_event=None,
            user_profile=make_snapshot(context="1:1"),
        )
        call_kwargs = client.messages.create.call_args
        user_content = call_kwargs[1]["messages"][0]["content"]
        assert "1:1" in user_content

    @pytest.mark.asyncio
    async def test_general_prompt_mentions_context_shift(self):
        """When user has context_shifts=True, shift note included in prompt."""
        client = make_mock_client()
        engine = make_engine(client=client)
        await engine.process(
            elm_event=None,
            user_profile=make_snapshot(
                archetype="Firestarter", core_archetype="Inquisitor"
            ),
        )
        call_kwargs = client.messages.create.call_args
        user_content = call_kwargs[1]["messages"][0]["content"]
        assert "Inquisitor" in user_content


# ---------------------------------------------------------------------------
# Fallback: timeout → cached prompt
# ---------------------------------------------------------------------------

class TestFallback:
    @pytest.mark.asyncio
    async def test_first_timeout_returns_none_no_cache(self):
        engine = make_engine(client=make_timeout_client())
        result = await engine.process(elm_event=make_elm_event())
        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_after_first_success(self):
        """Success → cache; subsequent timeout → fallback with cached text."""
        engine = make_engine()
        first = await engine.process(elm_event=make_elm_event())
        assert first is not None and first.is_fallback is False

        # Now swap to a timeout client
        engine._client = make_timeout_client()
        # Reset floor so the second call isn't blocked by cadence
        engine._last_prompt_time = time.monotonic() - 999

        second = await engine.process(elm_event=make_elm_event())
        assert second is not None
        assert second.is_fallback is True
        assert second.text == first.text

    @pytest.mark.asyncio
    async def test_fallback_triggered_by_preserved(self):
        """is_fallback=True prompt still carries the correct triggered_by."""
        engine = make_engine()
        await engine.process(elm_event=make_elm_event("ego_threat"))
        engine._client = make_timeout_client()
        engine._last_prompt_time = time.monotonic() - 999

        result = await engine.process(elm_event=make_elm_event("shortcut"))
        assert result is not None
        assert result.is_fallback is True
        # triggered_by reflects the NEW event, not the cached one
        assert result.triggered_by == "elm:shortcut"

    @pytest.mark.asyncio
    async def test_generic_exception_triggers_fallback(self):
        """Any exception (not just timeout) uses the fallback path."""
        engine = make_engine()
        await engine.process(elm_event=make_elm_event())

        engine._client = make_error_client()
        engine._last_prompt_time = time.monotonic() - 999

        result = await engine.process(elm_event=make_elm_event())
        assert result is not None
        assert result.is_fallback is True

    @pytest.mark.asyncio
    async def test_generic_exception_no_cache_returns_none(self):
        engine = make_engine(client=make_error_client())
        result = await engine.process(elm_event=make_elm_event())
        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_separate_cache_per_layer(self):
        """Audience cache and self cache are independent."""
        engine = make_engine()
        # Prime audience cache
        await engine.process(elm_event=make_elm_event())
        # General cache not yet set

        engine._client = make_timeout_client()
        engine._last_prompt_time = time.monotonic() - 999

        # General (self layer) — no cache yet → None
        result = await engine.process(elm_event=None)
        assert result is None


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

class TestCacheManagement:
    @pytest.mark.asyncio
    async def test_successful_call_updates_cache(self):
        engine = make_engine(client=make_mock_client("Tip one."))
        await engine.process(elm_event=make_elm_event())
        assert "audience" in engine._cache
        assert engine._cache["audience"].text == "Tip one."

    @pytest.mark.asyncio
    async def test_fallback_does_not_overwrite_cache(self):
        """The original cached text survives a subsequent fallback call."""
        engine = make_engine(client=make_mock_client("Original tip."))
        await engine.process(elm_event=make_elm_event())
        original_text = engine._cache["audience"].text

        engine._client = make_timeout_client()
        engine._last_prompt_time = time.monotonic() - 999
        await engine.process(elm_event=make_elm_event())

        assert engine._cache["audience"].text == original_text

    @pytest.mark.asyncio
    async def test_cache_updated_on_second_success(self):
        engine = make_engine(client=make_mock_client("First tip."))
        await engine.process(elm_event=make_elm_event())

        engine._client = make_mock_client("Second tip.")
        engine._last_prompt_time = time.monotonic() - 999
        await engine.process(elm_event=make_elm_event())

        assert engine._cache["audience"].text == "Second tip."

    @pytest.mark.asyncio
    async def test_cache_keyed_by_layer(self):
        engine = make_engine()
        await engine.process(elm_event=make_elm_event())  # audience cache
        engine._last_prompt_time = time.monotonic() - 999
        await engine.process(elm_event=None)              # self cache

        assert "audience" in engine._cache
        assert "self" in engine._cache


# ---------------------------------------------------------------------------
# last_prompt_time updated correctly
# ---------------------------------------------------------------------------

class TestLastPromptTime:
    @pytest.mark.asyncio
    async def test_last_prompt_time_zero_initially(self):
        engine = make_engine()
        assert engine.last_prompt_time == 0.0

    @pytest.mark.asyncio
    async def test_last_prompt_time_updated_after_elm(self):
        engine = make_engine()
        before = time.monotonic()
        await engine.process(elm_event=make_elm_event())
        after = time.monotonic()
        assert before <= engine.last_prompt_time <= after

    @pytest.mark.asyncio
    async def test_last_prompt_time_not_updated_when_blocked(self):
        engine = make_engine(elm_floor=10.0)
        await engine.process(elm_event=make_elm_event())
        first_time = engine.last_prompt_time
        # Second call within floor — should be blocked
        await engine.process(elm_event=make_elm_event())
        assert engine.last_prompt_time == first_time

    @pytest.mark.asyncio
    async def test_last_prompt_time_not_updated_on_none_from_fallback(self):
        engine = make_engine(client=make_timeout_client())
        # First call fails (no cache) → returns None
        result = await engine.process(elm_event=make_elm_event())
        assert result is None
        assert engine.last_prompt_time == 0.0


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:
    @pytest.mark.asyncio
    async def test_reset_clears_last_prompt_time(self):
        engine = make_engine()
        await engine.process(elm_event=make_elm_event())
        assert engine.last_prompt_time > 0.0
        engine.reset()
        assert engine.last_prompt_time == 0.0

    @pytest.mark.asyncio
    async def test_reset_clears_cache(self):
        engine = make_engine()
        await engine.process(elm_event=make_elm_event())
        assert engine._cache
        engine.reset()
        assert engine._cache == {}

    @pytest.mark.asyncio
    async def test_after_reset_fallback_returns_none(self):
        """After reset the cache is gone, so fallback returns None."""
        engine = make_engine()
        await engine.process(elm_event=make_elm_event())
        engine.reset()

        engine._client = make_timeout_client()
        result = await engine.process(elm_event=make_elm_event())
        assert result is None

    @pytest.mark.asyncio
    async def test_after_reset_cadence_fires_immediately(self):
        """After reset the floor elapsed counter is gone — next call fires."""
        engine = make_engine(elm_floor=10.0)
        await engine.process(elm_event=make_elm_event())
        # Within floor — blocked
        assert await engine.process(elm_event=make_elm_event()) is None
        engine.reset()
        # After reset — should fire
        result = await engine.process(elm_event=make_elm_event())
        assert result is not None


# ---------------------------------------------------------------------------
# Haiku model parameter passed through
# ---------------------------------------------------------------------------

class TestModelPassthrough:
    @pytest.mark.asyncio
    async def test_model_passed_to_client(self):
        client = make_mock_client()
        engine = CoachingEngine(
            user_speaker="speaker_0",
            anthropic_client=client,
            model="claude-haiku-custom-test",
        )
        await engine.process(elm_event=make_elm_event())
        call_kwargs = client.messages.create.call_args
        assert call_kwargs[1]["model"] == "claude-haiku-custom-test"

    @pytest.mark.asyncio
    async def test_max_tokens_capped(self):
        client = make_mock_client()
        engine = make_engine(client=client)
        await engine.process(elm_event=make_elm_event())
        call_kwargs = client.messages.create.call_args
        assert call_kwargs[1]["max_tokens"] <= 80


# ---------------------------------------------------------------------------
# Archetype-aware coaching
# ---------------------------------------------------------------------------

class TestArchetypeAwareCoaching:
    @pytest.mark.asyncio
    async def test_elm_prompt_includes_pairing_advice(self):
        """ELM prompt should include archetype pairing insight."""
        client = make_mock_client()
        engine = make_engine(
            client=client,
            user_archetype="Firestarter",
            participants=[{"name": "Sarah", "archetype": "Architect"}],
        )
        await engine.process(elm_event=make_elm_event())
        user_content = client.messages.create.call_args[1]["messages"][0]["content"]
        assert "data and structure" in user_content  # Firestarter→Architect advice

    @pytest.mark.asyncio
    async def test_general_prompt_includes_participant_roster(self):
        """General prompt should list all participants with pairing advice."""
        client = make_mock_client()
        engine = make_engine(
            client=client,
            user_archetype="Firestarter",
            participants=[
                {"name": "Sarah Chen", "archetype": "Architect"},
                {"name": "Mike R", "archetype": "Bridge Builder"},
            ],
        )
        await engine.process(elm_event=None)
        user_content = client.messages.create.call_args[1]["messages"][0]["content"]
        assert "Sarah Chen (Architect)" in user_content
        assert "Mike R (Bridge Builder)" in user_content
        assert "Meeting participants" in user_content

    @pytest.mark.asyncio
    async def test_general_prompt_prefers_profile_archetype_over_init(self):
        """ProfileSnapshot archetype (behavioral data) takes precedence over __init__ value."""
        client = make_mock_client()
        engine = make_engine(client=client, user_archetype="Inquisitor")
        await engine.process(
            elm_event=None,
            user_profile=make_snapshot("Firestarter"),
        )
        user_content = client.messages.create.call_args[1]["messages"][0]["content"]
        assert "Firestarter" in user_content

    @pytest.mark.asyncio
    async def test_general_prompt_falls_back_to_init_when_undetermined(self):
        """When ProfileSnapshot archetype is Undetermined, fall back to __init__ value."""
        client = make_mock_client()
        engine = make_engine(client=client, user_archetype="Inquisitor")
        await engine.process(
            elm_event=None,
            user_profile=make_snapshot("Undetermined"),
        )
        user_content = client.messages.create.call_args[1]["messages"][0]["content"]
        assert "Inquisitor" in user_content

    @pytest.mark.asyncio
    async def test_no_participants_omits_roster(self):
        """When no participants provided, roster section is absent."""
        client = make_mock_client()
        engine = make_engine(client=client, user_archetype="Architect")
        await engine.process(elm_event=None)
        user_content = client.messages.create.call_args[1]["messages"][0]["content"]
        assert "Meeting participants" not in user_content

    @pytest.mark.asyncio
    async def test_unknown_archetype_fallback(self):
        """Unknown archetypes get a generic pairing message."""
        client = make_mock_client()
        engine = make_engine(client=client)  # defaults to "Unknown"
        await engine.process(elm_event=make_elm_event())
        user_content = client.messages.create.call_args[1]["messages"][0]["content"]
        assert "Listen actively" in user_content


# ---------------------------------------------------------------------------
# Flexibility-aware coaching prompts
# ---------------------------------------------------------------------------

class TestFlexibilityAwarePrompt:
    @pytest.mark.asyncio
    async def test_high_flex_note_included(self):
        """High-variance user gets flexibility note in prompt."""
        client = make_mock_client()
        engine = make_engine(client=client, user_archetype="Firestarter")
        # Create a snapshot with high variance
        snapshot = make_snapshot(archetype="Firestarter", core_archetype="Inquisitor")
        # Manually set high variance to trigger flex note
        snapshot = ProfileSnapshot(
            archetype="Firestarter",
            focus_score=60.0,
            stance_score=40.0,
            focus_variance=800.0,
            stance_variance=300.0,
            confidence=0.7,
            context="board",
            context_sessions=5,
            is_context_specific=True,
            core_archetype="Inquisitor",
            core_sessions=8,
            context_shifts=True,
        )
        engine._user_snapshot = snapshot
        prompt = await engine._general_prompt(
            make_classification(), snapshot
        )
        # The prompt should contain the flex note
        user_content = client.messages.create.call_args[1]["messages"][0]["content"]
        assert "adapts their style" in user_content

    @pytest.mark.asyncio
    async def test_no_flex_note_for_new_user(self):
        """New user with zero variance gets no flexibility note."""
        client = make_mock_client()
        engine = make_engine(client=client, user_archetype="Architect")
        snapshot = make_snapshot()  # default variance = 0.0
        prompt = await engine._general_prompt(
            make_classification(), snapshot
        )
        user_content = client.messages.create.call_args[1]["messages"][0]["content"]
        assert "adapts their style" not in user_content
        assert "same style regardless" not in user_content
