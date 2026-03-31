"""Unit tests for backend.speaker_resolver — LLM-based speaker name resolution."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.speaker_resolver import SpeakerResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_response(mappings: list[dict]) -> MagicMock:
    """Build a mock Claude response with the given mappings JSON."""
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps({"mappings": mappings}))]
    return response


def _make_resolver(
    *,
    known_names: list[str] | None = None,
    confidence_threshold: float = 0.7,
    lock_threshold: float = 0.8,
    ws_send: AsyncMock | None = None,
) -> tuple[SpeakerResolver, AsyncMock]:
    """Create a SpeakerResolver with a mocked Anthropic client."""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock()

    resolver = SpeakerResolver(
        anthropic_client=client,
        known_names=known_names or ["Alice Chen", "Bob Smith"],
        interval_s=0.05,  # fast for tests
        confidence_threshold=confidence_threshold,
        lock_threshold=lock_threshold,
        ws_send=ws_send,
    )
    return resolver, client.messages.create


# ---------------------------------------------------------------------------
# Tests — resolve() basics
# ---------------------------------------------------------------------------

class TestResolveBasics:
    def test_unknown_speaker_returns_id(self):
        resolver, _ = _make_resolver()
        assert resolver.resolve("counterpart_0") == "counterpart_0"

    def test_resolve_after_manual_set(self):
        resolver, _ = _make_resolver()
        resolver.set_confirmed_name("counterpart_0", "Alice Chen")
        assert resolver.resolve("counterpart_0") == "Alice Chen"

    def test_mappings_returns_copy(self):
        resolver, _ = _make_resolver()
        resolver.set_confirmed_name("counterpart_0", "Alice")
        m = resolver.mappings
        m["counterpart_0"] = "CHANGED"
        assert resolver.resolve("counterpart_0") == "Alice"


# ---------------------------------------------------------------------------
# Tests — LLM resolution cycle
# ---------------------------------------------------------------------------

class TestResolutionCycle:
    @pytest.mark.asyncio
    async def test_resolve_with_name_address(self):
        """'Thanks Sarah' in transcript → maps to Sarah."""
        resolver, create_mock = _make_resolver(known_names=["Sarah Lee", "Bob Smith"])

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Sarah Lee", "confidence": 0.85, "evidence": "addressed as Sarah at turn 5"},
        ])

        # Add enough utterances to trigger resolution
        for i in range(6):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")

        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Sarah Lee"

    @pytest.mark.asyncio
    async def test_resolve_with_self_introduction(self):
        """'I'm John from engineering' → maps to John."""
        resolver, create_mock = _make_resolver(known_names=["John Park", "Lisa Wong"])

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_1", "name": "John Park", "confidence": 0.9, "evidence": "self-introduced as John"},
        ])

        for i in range(6):
            resolver.add_utterance("counterpart_1", f"Utterance {i}")

        await resolver._resolve_once()
        assert resolver.resolve("counterpart_1") == "John Park"

    @pytest.mark.asyncio
    async def test_low_confidence_not_applied(self):
        """Ambiguous mapping below threshold is not applied."""
        resolver, create_mock = _make_resolver(confidence_threshold=0.7)

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.4, "evidence": "unclear"},
        ])

        for i in range(6):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")

        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "counterpart_0"  # unchanged

    @pytest.mark.asyncio
    async def test_monotonic_confidence_locks(self):
        """Once confidence ≥ lock_threshold, mapping is locked permanently."""
        resolver, create_mock = _make_resolver(lock_threshold=0.8)

        # First resolution: high confidence → locks
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.9, "evidence": "direct naming"},
        ])
        for i in range(6):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Alice Chen"

        # Second resolution: different name, even higher confidence → ignored (locked)
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Bob Smith", "confidence": 1.0, "evidence": "new evidence"},
        ])
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Alice Chen"  # still locked

    @pytest.mark.asyncio
    async def test_set_confirmed_name_locks(self):
        """User-confirmed names are locked and cannot be overridden by LLM."""
        resolver, create_mock = _make_resolver()
        resolver.set_confirmed_name("counterpart_0", "Manual Name")

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 1.0, "evidence": "strong"},
        ])
        for i in range(6):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Manual Name"


# ---------------------------------------------------------------------------
# Tests — background loop lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_no_resolution_with_few_utterances(self):
        """< 5 utterances → resolver skips the cycle."""
        resolver, create_mock = _make_resolver()

        # Add only 3 utterances
        for i in range(3):
            resolver.add_utterance("counterpart_0", f"Short {i}")

        await resolver.start()
        await asyncio.sleep(0.15)  # wait for at least one loop cycle
        await resolver.stop()

        create_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        """Start and stop without errors."""
        resolver, _ = _make_resolver()
        await resolver.start()
        assert resolver._running is True
        await resolver.stop()
        assert resolver._running is False
        assert resolver._task is None

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self):
        """Starting twice doesn't create duplicate tasks."""
        resolver, _ = _make_resolver()
        await resolver.start()
        task1 = resolver._task
        await resolver.start()  # second start
        assert resolver._task is task1  # same task
        await resolver.stop()


# ---------------------------------------------------------------------------
# Tests — WebSocket notification
# ---------------------------------------------------------------------------

class TestWSNotification:
    @pytest.mark.asyncio
    async def test_ws_send_on_identification(self):
        """Frontend receives speaker_identified when a name is resolved."""
        ws_send = AsyncMock()
        resolver, create_mock = _make_resolver(ws_send=ws_send)

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.85, "evidence": "named"},
        ])

        for i in range(6):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")
        await resolver._resolve_once()

        ws_send.assert_called_once()
        call_arg = ws_send.call_args[0][0]
        assert call_arg["type"] == "speaker_identified"
        assert call_arg["speaker_id"] == "counterpart_0"
        assert call_arg["name"] == "Alice Chen"

    @pytest.mark.asyncio
    async def test_ws_send_failure_does_not_crash(self):
        """WS send failure is caught and doesn't stop the resolver."""
        ws_send = AsyncMock(side_effect=RuntimeError("connection closed"))
        resolver, create_mock = _make_resolver(ws_send=ws_send)

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.85, "evidence": "named"},
        ])

        for i in range(6):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")

        # Should not raise
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Alice Chen"


# ---------------------------------------------------------------------------
# Tests — malformed LLM response
# ---------------------------------------------------------------------------

class TestMalformedResponse:
    @pytest.mark.asyncio
    async def test_non_json_response_handled(self):
        """Non-JSON LLM response doesn't crash."""
        resolver, create_mock = _make_resolver()
        response = MagicMock()
        response.content = [MagicMock(text="I can't determine the speakers.")]
        create_mock.return_value = response

        for i in range(6):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")

        # Should not raise
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "counterpart_0"

    @pytest.mark.asyncio
    async def test_empty_mappings_array(self):
        """Empty mappings array doesn't crash."""
        resolver, create_mock = _make_resolver()
        create_mock.return_value = _make_llm_response([])

        for i in range(6):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")

        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "counterpart_0"

    @pytest.mark.asyncio
    async def test_missing_fields_in_mapping(self):
        """Mapping with missing fields is skipped."""
        resolver, create_mock = _make_resolver()
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "", "name": "Alice", "confidence": 0.9},
            {"speaker_id": "counterpart_0", "name": "", "confidence": 0.9},
            {"confidence": 0.9},
        ])

        for i in range(6):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")

        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "counterpart_0"
