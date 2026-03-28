"""
Tests for backend/sparring.py.

No real API calls — the Anthropic client is fully mocked.

Covers:
  - SparringSession initialisation defaults
  - send(): yields user echo, streaming opponent chunks, final opponent, coaching tip
  - Coaching tip suppressed when user text is too short (<5 words)
  - History grows correctly across multiple turns
  - max_turns enforced: no more turns after limit reached
  - end() stops the session
  - _get_coaching_tip: returns stripped text from Haiku response
  - _ARCHETYPE_DESCRIPTIONS coverage: all four types have entries
  - SparringTurn fields: role, text, is_final, turn_number, coaching_tip
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.sparring import (
    SparringSession,
    SparringTurn,
    _ARCHETYPE_DESCRIPTIONS,
    _MIN_WORDS_FOR_COACHING,
)


# ---------------------------------------------------------------------------
# Fake Anthropic client
# ---------------------------------------------------------------------------

class FakeTextStream:
    """Async iterator that yields preset string chunks."""

    def __init__(self, chunks: list[str]):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


@dataclass
class FakeStreamCtx:
    """Fake context manager returned by client.messages.stream()."""
    chunks: list[str]

    @asynccontextmanager
    async def __call__(self, *args, **kwargs):
        yield self

    @property
    def text_stream(self) -> FakeTextStream:
        return FakeTextStream(self.chunks)


def make_fake_client(
    opponent_chunks: list[str] | None = None,
    coaching_text: str = "Ask a clarifying question.",
) -> MagicMock:
    """
    Build a fake AsyncAnthropic client.

    client.messages.stream()  → yields opponent_chunks
    client.messages.create()  → returns coaching_text
    """
    chunks = opponent_chunks or ["I ", "disagree ", "completely."]

    client = MagicMock()

    # Stream context manager
    fake_stream_ctx = FakeStreamCtx(chunks)

    @asynccontextmanager
    async def fake_stream(**kwargs):
        yield fake_stream_ctx

    client.messages.stream = fake_stream

    # Non-streaming create (coaching)
    fake_content = MagicMock()
    fake_content.text = coaching_text
    fake_response = MagicMock()
    fake_response.content = [fake_content]
    client.messages.create = AsyncMock(return_value=fake_response)

    return client


def make_session(
    *,
    opponent_chunks: list[str] | None = None,
    coaching_text: str = "Ask a clarifying question.",
    max_turns: int = 10,
) -> SparringSession:
    client = make_fake_client(
        opponent_chunks=opponent_chunks,
        coaching_text=coaching_text,
    )
    session = SparringSession(
        user_archetype="Inquisitor",
        opponent_archetype="Firestarter",
        scenario="Pitch a new product roadmap",
        max_turns=max_turns,
        anthropic_client=client,
    )
    return session


async def collect_turns(session: SparringSession, text: str) -> list[SparringTurn]:
    turns = []
    async for turn in await session.send(text):
        turns.append(turn)
    return turns


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestInit:
    def test_default_max_turns(self):
        session = make_session()
        assert session.max_turns == 10

    def test_turn_count_starts_at_zero(self):
        session = make_session()
        assert session.turn_count == 0

    def test_not_ended_initially(self):
        session = make_session()
        assert not session.is_ended

    def test_history_empty_initially(self):
        session = make_session()
        assert session.history_snapshot() == []

    def test_custom_max_turns(self):
        session = make_session(max_turns=3)
        assert session.max_turns == 3


# ---------------------------------------------------------------------------
# Single turn — turn types and order
# ---------------------------------------------------------------------------

class TestSingleTurn:
    @pytest.mark.asyncio
    async def test_first_turn_is_user_echo(self):
        session = make_session()
        turns = await collect_turns(session, "What is your evidence for that claim?")
        assert turns[0].role == "user"
        assert turns[0].text == "What is your evidence for that claim?"

    @pytest.mark.asyncio
    async def test_streaming_opponent_chunks_present(self):
        session = make_session(opponent_chunks=["Hello ", "world."])
        turns = await collect_turns(session, "I need data to move forward.")
        opponent_stream = [t for t in turns if t.role == "opponent" and not t.is_final]
        assert len(opponent_stream) == 2
        assert opponent_stream[0].text == "Hello "
        assert opponent_stream[1].text == "world."

    @pytest.mark.asyncio
    async def test_final_opponent_turn_present(self):
        session = make_session(opponent_chunks=["Hello ", "world."])
        turns = await collect_turns(session, "I need data to move forward.")
        final_turns = [t for t in turns if t.role == "opponent" and t.is_final]
        assert len(final_turns) == 1
        assert final_turns[0].text == "Hello world."

    @pytest.mark.asyncio
    async def test_coaching_turn_present_for_long_message(self):
        session = make_session(coaching_text="Use a specific data point here.")
        long_msg = "I need more evidence before I can accept this claim"
        turns = await collect_turns(session, long_msg)
        coaching = [t for t in turns if t.role == "coaching"]
        assert len(coaching) == 1
        assert coaching[0].text == "Use a specific data point here."

    @pytest.mark.asyncio
    async def test_coaching_tip_field_on_coaching_turn(self):
        session = make_session(coaching_text="Anchor your point in numbers.")
        turns = await collect_turns(session, "I need more evidence before accepting this")
        coaching = [t for t in turns if t.role == "coaching"][0]
        assert coaching.coaching_tip == "Anchor your point in numbers."

    @pytest.mark.asyncio
    async def test_turn_order_user_stream_final_coaching(self):
        session = make_session(opponent_chunks=["X"])
        turns = await collect_turns(session, "I want evidence for that statement please")
        roles = [t.role for t in turns]
        # user → at least one opponent stream → final opponent → coaching
        assert roles[0] == "user"
        assert "opponent" in roles
        # Final opponent (is_final=True) comes before coaching
        final_idx = next(
            i for i, t in enumerate(turns) if t.role == "opponent" and t.is_final
        )
        coaching_indices = [i for i, t in enumerate(turns) if t.role == "coaching"]
        if coaching_indices:
            assert coaching_indices[0] > final_idx

    @pytest.mark.asyncio
    async def test_turn_number_is_zero_for_first_turn(self):
        session = make_session()
        turns = await collect_turns(session, "Show me the data right now please")
        for t in turns:
            assert t.turn_number == 0

    @pytest.mark.asyncio
    async def test_turn_count_incremented_after_send(self):
        session = make_session()
        await collect_turns(session, "Show me the data right now please")
        assert session.turn_count == 1


# ---------------------------------------------------------------------------
# Coaching suppression
# ---------------------------------------------------------------------------

class TestCoachingSuppression:
    @pytest.mark.asyncio
    async def test_no_coaching_for_short_message(self):
        session = make_session()
        # Fewer than _MIN_WORDS_FOR_COACHING words
        short = " ".join(["word"] * (_MIN_WORDS_FOR_COACHING - 1))
        turns = await collect_turns(session, short)
        coaching = [t for t in turns if t.role == "coaching"]
        assert coaching == []

    @pytest.mark.asyncio
    async def test_coaching_fires_at_threshold(self):
        session = make_session(coaching_text="Good tip here please use it.")
        at_threshold = " ".join(["word"] * _MIN_WORDS_FOR_COACHING)
        turns = await collect_turns(session, at_threshold)
        coaching = [t for t in turns if t.role == "coaching"]
        assert len(coaching) == 1

    @pytest.mark.asyncio
    async def test_single_word_no_coaching(self):
        session = make_session()
        turns = await collect_turns(session, "Yes")
        coaching = [t for t in turns if t.role == "coaching"]
        assert coaching == []


# ---------------------------------------------------------------------------
# History management
# ---------------------------------------------------------------------------

class TestHistory:
    @pytest.mark.asyncio
    async def test_history_has_user_and_assistant_after_one_turn(self):
        session = make_session(opponent_chunks=["Response."])
        await collect_turns(session, "I need evidence before moving forward here")
        history = session.history_snapshot()
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_user_text_in_history(self):
        session = make_session()
        msg = "I need evidence before moving forward here"
        await collect_turns(session, msg)
        assert session.history_snapshot()[0]["content"] == msg

    @pytest.mark.asyncio
    async def test_assistant_text_in_history_is_full_response(self):
        session = make_session(opponent_chunks=["Hello ", "world."])
        await collect_turns(session, "I need evidence before moving forward here")
        assert session.history_snapshot()[1]["content"] == "Hello world."

    @pytest.mark.asyncio
    async def test_history_grows_across_two_turns(self):
        session = make_session()
        await collect_turns(session, "I need evidence before moving forward here")
        await collect_turns(session, "That is not sufficient evidence for my needs")
        assert len(session.history_snapshot()) == 4

    @pytest.mark.asyncio
    async def test_history_snapshot_is_a_copy(self):
        session = make_session()
        snapshot = session.history_snapshot()
        snapshot.append({"role": "injected", "content": "hack"})
        assert len(session.history_snapshot()) == 0  # original unchanged


# ---------------------------------------------------------------------------
# max_turns enforcement
# ---------------------------------------------------------------------------

class TestMaxTurns:
    @pytest.mark.asyncio
    async def test_session_ends_after_max_turns(self):
        session = make_session(max_turns=2)
        await collect_turns(session, "I need evidence before moving forward here")
        await collect_turns(session, "That still does not answer my question properly")
        assert session.is_ended

    @pytest.mark.asyncio
    async def test_no_turns_after_max_reached(self):
        session = make_session(max_turns=1)
        await collect_turns(session, "I need evidence before we can proceed forward")
        turns = await collect_turns(session, "Another message after limit was reached")
        assert turns == []

    @pytest.mark.asyncio
    async def test_turn_count_does_not_exceed_max(self):
        session = make_session(max_turns=2)
        for _ in range(5):
            await collect_turns(session, "I need more evidence for your claim")
        assert session.turn_count == 2

    @pytest.mark.asyncio
    async def test_session_not_ended_before_max(self):
        session = make_session(max_turns=3)
        await collect_turns(session, "I need evidence before moving forward here")
        assert not session.is_ended


# ---------------------------------------------------------------------------
# end()
# ---------------------------------------------------------------------------

class TestEnd:
    @pytest.mark.asyncio
    async def test_end_stops_session(self):
        session = make_session()
        session.end()
        turns = await collect_turns(session, "I need evidence before we can proceed here")
        assert turns == []

    def test_end_sets_is_ended(self):
        session = make_session()
        session.end()
        assert session.is_ended

    @pytest.mark.asyncio
    async def test_end_mid_session_respected_on_next_turn(self):
        session = make_session(max_turns=5)
        await collect_turns(session, "I need evidence before moving forward here")
        session.end()
        turns = await collect_turns(session, "Another turn that should be ignored now")
        assert turns == []


# ---------------------------------------------------------------------------
# Archetype descriptions
# ---------------------------------------------------------------------------

class TestArchetypeDescriptions:
    def test_all_four_archetypes_present(self):
        for archetype in ("Architect", "Firestarter", "Inquisitor", "Bridge Builder"):
            assert archetype in _ARCHETYPE_DESCRIPTIONS

    def test_descriptions_are_non_empty_strings(self):
        for k, v in _ARCHETYPE_DESCRIPTIONS.items():
            assert isinstance(v, str) and len(v) > 10, f"Empty description for {k}"


# ---------------------------------------------------------------------------
# SparringTurn defaults
# ---------------------------------------------------------------------------

class TestSparringTurn:
    def test_defaults(self):
        t = SparringTurn(role="user", text="hello", turn_number=0)
        assert t.is_final is True
        assert t.coaching_tip == ""

    def test_streaming_turn_not_final(self):
        t = SparringTurn(role="opponent", text="chunk", turn_number=1, is_final=False)
        assert not t.is_final
