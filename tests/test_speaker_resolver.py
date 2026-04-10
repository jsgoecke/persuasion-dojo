"""Unit tests for backend.speaker_resolver — LLM-based speaker name resolution."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.speaker_resolver import FUZZY_MATCH_THRESHOLD, SpeakerResolver


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
    on_mapping_updated: AsyncMock | None = None,
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
        on_mapping_updated=on_mapping_updated,
    )
    return resolver, client.messages.create


def _add_utterances(resolver: SpeakerResolver, n: int = 6, speaker: str = "counterpart_0") -> None:
    """Add N utterances to the resolver."""
    for i in range(n):
        resolver.add_utterance(speaker, f"Utterance {i}")


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

        _add_utterances(resolver)
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Sarah Lee"

    @pytest.mark.asyncio
    async def test_resolve_with_self_introduction(self):
        """'I'm John from engineering' → maps to John."""
        resolver, create_mock = _make_resolver(known_names=["John Park", "Lisa Wong"])

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_1", "name": "John Park", "confidence": 0.9, "evidence": "self-introduced as John"},
        ])

        _add_utterances(resolver, speaker="counterpart_1")
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_1") == "John Park"

    @pytest.mark.asyncio
    async def test_low_confidence_not_applied(self):
        """Ambiguous mapping below threshold is not applied."""
        resolver, create_mock = _make_resolver(confidence_threshold=0.7)

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.4, "evidence": "unclear"},
        ])

        _add_utterances(resolver)
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "counterpart_0"  # unchanged

    @pytest.mark.asyncio
    async def test_monotonic_confidence_locks(self):
        """Once confidence >= lock_threshold, mapping is locked permanently."""
        resolver, create_mock = _make_resolver(lock_threshold=0.8)

        # First resolution: high confidence → locks
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.9, "evidence": "direct naming"},
        ])
        _add_utterances(resolver)
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
        _add_utterances(resolver)
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Manual Name"


# ---------------------------------------------------------------------------
# Tests — context window (first 20 + last 80)
# ---------------------------------------------------------------------------

class TestContextWindow:
    @pytest.mark.asyncio
    async def test_short_transcript_uses_all(self):
        """With < 100 utterances, all are included in context."""
        resolver, create_mock = _make_resolver()
        create_mock.return_value = _make_llm_response([])

        for i in range(50):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")

        await resolver._resolve_once()

        # Check the prompt contains all 50 utterances
        call_args = create_mock.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "Utterance 0" in prompt
        assert "Utterance 49" in prompt

    @pytest.mark.asyncio
    async def test_long_transcript_first_20_last_80(self):
        """With > 100 utterances, first 20 + last 80 are sent to LLM."""
        resolver, create_mock = _make_resolver()
        create_mock.return_value = _make_llm_response([])

        for i in range(200):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")

        await resolver._resolve_once()

        call_args = create_mock.call_args
        prompt = call_args[1]["messages"][0]["content"]
        # First 20 (indices 0-19) should be present
        assert "Utterance 0" in prompt
        assert "Utterance 19" in prompt
        # Middle should be absent (index 50 is between first 20 and last 80)
        assert "Utterance 50" not in prompt
        # Last 80 (indices 120-199) should be present
        assert "Utterance 120" in prompt
        assert "Utterance 199" in prompt


# ---------------------------------------------------------------------------
# Tests — fuzzy name matching
# ---------------------------------------------------------------------------

class TestFuzzyNameMatching:
    def test_exact_match_returns_canonical(self):
        resolver, _ = _make_resolver(known_names=["Sarah Chen"])
        assert resolver._fuzzy_match_name("Sarah Chen") == "Sarah Chen"

    def test_close_match_returns_best(self):
        """'Sarah Chen' fuzzy-matches 'Sarah L Chen' above 0.85 (ratio=0.909)."""
        resolver, _ = _make_resolver(known_names=["Sarah L Chen", "Bob Smith"])
        result = resolver._fuzzy_match_name("Sarah Chen")
        assert result == "Sarah L Chen"

    def test_below_threshold_returns_none(self):
        resolver, _ = _make_resolver(known_names=["Sarah Chen"])
        assert resolver._fuzzy_match_name("Completely Different") is None

    def test_adversarial_similar_names(self):
        """'Rob Smith' is close to 'Bob Smith' (0.89). Above threshold."""
        resolver, _ = _make_resolver(known_names=["Bob Smith", "Alice Chen"])
        result = resolver._fuzzy_match_name("Rob Smith")
        # This is a known behavior: very similar names match above 0.85
        assert result == "Bob Smith"

    def test_empty_known_names_returns_none(self):
        resolver, _ = _make_resolver(known_names=[])
        assert resolver._fuzzy_match_name("Alice") is None

    @pytest.mark.asyncio
    async def test_fuzzy_match_used_in_resolution(self):
        """LLM returns 'Sarah Chen' but calendar has 'Sarah L Chen' — fuzzy match resolves."""
        resolver, create_mock = _make_resolver(known_names=["Sarah L Chen"])
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Sarah Chen", "confidence": 0.85, "evidence": "addressed"},
        ])
        _add_utterances(resolver)
        await resolver._resolve_once()
        # Should resolve to the canonical name, not the LLM's partial name
        assert resolver.resolve("counterpart_0") == "Sarah L Chen"

    @pytest.mark.asyncio
    async def test_no_match_rejects_name(self):
        """LLM returns a name not in the attendee list — rejected."""
        resolver, create_mock = _make_resolver(known_names=["Alice Chen"])
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Totally Unknown", "confidence": 0.9, "evidence": "strong"},
        ])
        _add_utterances(resolver)
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "counterpart_0"


# ---------------------------------------------------------------------------
# Tests — confidence decay with flip-flop guard
# ---------------------------------------------------------------------------

class TestConfidenceDecay:
    @pytest.mark.asyncio
    async def test_same_name_allows_lower_confidence(self):
        """Same name at lower confidence (within 0.9 factor) is accepted."""
        resolver, create_mock = _make_resolver()

        # First: Alice at 0.78 (above threshold 0.7, below lock threshold 0.8)
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.78, "evidence": "likely"},
        ])
        _add_utterances(resolver)
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Alice Chen"

        # Second: Alice at 0.72 (> 0.78 * 0.9 = 0.702), accepted
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.72, "evidence": "same"},
        ])
        resolver.add_utterance("counterpart_0", "new utterance")  # trigger new data
        await resolver._resolve_once()
        assert resolver._confidences["counterpart_0"] == 0.72

    @pytest.mark.asyncio
    async def test_same_name_rejects_too_low_confidence(self):
        """Same name at much lower confidence (below 0.9 factor) is rejected."""
        resolver, create_mock = _make_resolver()

        # First: Alice at 0.72
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.72, "evidence": "likely"},
        ])
        _add_utterances(resolver)
        await resolver._resolve_once()

        # Second: Alice at 0.50 (< 0.72 * 0.9 = 0.648), rejected
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.50, "evidence": "weak"},
        ])
        resolver.add_utterance("counterpart_0", "new utterance")
        await resolver._resolve_once()
        assert resolver._confidences["counterpart_0"] == 0.72  # unchanged

    @pytest.mark.asyncio
    async def test_different_name_requires_higher_confidence(self):
        """Different name must strictly beat existing confidence (no decay)."""
        resolver, create_mock = _make_resolver()

        # First: Alice at 0.72
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.72, "evidence": "likely"},
        ])
        _add_utterances(resolver)
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Alice Chen"

        # Second: Bob at 0.71 (NOT strictly greater), rejected
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Bob Smith", "confidence": 0.71, "evidence": "maybe"},
        ])
        resolver.add_utterance("counterpart_0", "new utterance")
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Alice Chen"  # unchanged

    @pytest.mark.asyncio
    async def test_different_name_accepted_if_strictly_higher(self):
        """Different name with strictly higher confidence overrides."""
        resolver, create_mock = _make_resolver()

        # First: Alice at 0.72
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.72, "evidence": "likely"},
        ])
        _add_utterances(resolver)
        await resolver._resolve_once()

        # Second: Bob at 0.75 (strictly greater), accepted
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Bob Smith", "confidence": 0.75, "evidence": "corrected"},
        ])
        resolver.add_utterance("counterpart_0", "new utterance")
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Bob Smith"

    @pytest.mark.asyncio
    async def test_decay_does_not_affect_locked(self):
        """Locked mappings cannot be overridden regardless of confidence."""
        resolver, create_mock = _make_resolver(lock_threshold=0.8)

        # Lock Alice at 0.85
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.85, "evidence": "strong"},
        ])
        _add_utterances(resolver)
        await resolver._resolve_once()
        assert "counterpart_0" in resolver._locked

        # Try Bob at 0.95 — still locked
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Bob Smith", "confidence": 0.95, "evidence": "very strong"},
        ])
        resolver.add_utterance("counterpart_0", "new utterance")
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Alice Chen"

    @pytest.mark.asyncio
    async def test_flip_flop_prevented(self):
        """Prevents Alice→Bob→Alice oscillation across cycles."""
        resolver, create_mock = _make_resolver()

        # Cycle 1: Alice at 0.72
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.72, "evidence": "likely"},
        ])
        _add_utterances(resolver)
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Alice Chen"

        # Cycle 2: Bob at 0.71 — rejected (not strictly greater)
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Bob Smith", "confidence": 0.71, "evidence": "maybe"},
        ])
        resolver.add_utterance("counterpart_0", "new")
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Alice Chen"  # Alice holds

        # Cycle 3: Alice at 0.60 — accepted (same name, within decay)
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.66, "evidence": "still"},
        ])
        resolver.add_utterance("counterpart_0", "another")
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "Alice Chen"  # still Alice


# ---------------------------------------------------------------------------
# Tests — DB pre-seed
# ---------------------------------------------------------------------------

class TestDBPreSeed:
    @pytest.mark.asyncio
    async def test_load_returns_participant_names(self):
        """Loads names from participant DB."""
        from unittest.mock import patch
        from datetime import datetime, timedelta

        mock_participant = MagicMock()
        mock_participant.name = "Sarah Chen"
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("Sarah Chen",), ("Bob Smith",)]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        names = await SpeakerResolver.load_known_names_from_db(mock_factory, "user-1")
        assert "Sarah Chen" in names
        assert "Bob Smith" in names

    @pytest.mark.asyncio
    async def test_load_handles_db_failure(self):
        """DB failure returns empty list, doesn't crash."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("DB down"))
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        names = await SpeakerResolver.load_known_names_from_db(mock_factory, "user-1")
        assert names == []

    @pytest.mark.asyncio
    async def test_load_filters_none_names(self):
        """None names are filtered out."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("Alice",), (None,), ("Bob",)]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        names = await SpeakerResolver.load_known_names_from_db(mock_factory, "user-1")
        assert len(names) == 2
        assert None not in names


# ---------------------------------------------------------------------------
# Tests — persistence callback
# ---------------------------------------------------------------------------

class TestPersistenceCallback:
    @pytest.mark.asyncio
    async def test_callback_fires_on_mapping_update(self):
        """Persistence callback is called when a mapping changes."""
        on_updated = AsyncMock()
        resolver, create_mock = _make_resolver(on_mapping_updated=on_updated)

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.85, "evidence": "named"},
        ])

        _add_utterances(resolver)
        await resolver._resolve_once()

        on_updated.assert_called_once_with("counterpart_0", "Alice Chen", 0.85)

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_crash(self):
        """Persistence callback failure doesn't stop resolution."""
        on_updated = AsyncMock(side_effect=RuntimeError("DB error"))
        resolver, create_mock = _make_resolver(on_mapping_updated=on_updated)

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.85, "evidence": "named"},
        ])

        _add_utterances(resolver)
        await resolver._resolve_once()  # should not raise
        assert resolver.resolve("counterpart_0") == "Alice Chen"


# ---------------------------------------------------------------------------
# Tests — skip if no new utterances
# ---------------------------------------------------------------------------

class TestSkipOptimization:
    @pytest.mark.asyncio
    async def test_skip_if_no_new_utterances(self):
        """No new utterances since last cycle → LLM not called."""
        resolver, create_mock = _make_resolver()
        create_mock.return_value = _make_llm_response([])

        _add_utterances(resolver, n=6)

        # First cycle: LLM is called
        await resolver.start()
        await asyncio.sleep(0.15)
        assert create_mock.call_count >= 1
        first_count = create_mock.call_count

        # Wait another cycle without adding utterances
        await asyncio.sleep(0.15)
        await resolver.stop()

        # Should NOT have been called again (no new utterances)
        assert create_mock.call_count == first_count


# ---------------------------------------------------------------------------
# Tests — error guards
# ---------------------------------------------------------------------------

class TestErrorGuards:
    @pytest.mark.asyncio
    async def test_empty_content_response_handled(self):
        """Empty content array in LLM response doesn't crash."""
        resolver, create_mock = _make_resolver()
        response = MagicMock()
        response.content = []  # empty
        create_mock.return_value = response

        _add_utterances(resolver)
        await resolver._resolve_once()  # should not raise
        assert resolver.resolve("counterpart_0") == "counterpart_0"

    @pytest.mark.asyncio
    async def test_name_validation_without_known_names(self):
        """Without roster, implausible names are rejected."""
        resolver, create_mock = _make_resolver(known_names=[])

        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "speaker_0", "confidence": 0.85, "evidence": "self-id"},
        ])

        _add_utterances(resolver)
        with patch("backend.identity.is_plausible_speaker_name", return_value=False):
            await resolver._resolve_once()

        # speaker_0 is a generic ID, should be rejected
        assert resolver.resolve("counterpart_0") == "counterpart_0"

    def test_none_in_known_names_filtered(self):
        """None values in known_names are filtered at init."""
        resolver = SpeakerResolver(
            anthropic_client=MagicMock(),
            known_names=["Alice", None, "", "Bob"],  # type: ignore[list-item]
        )
        assert resolver._known_names == ["Alice", "Bob"]

    def test_fuzzy_threshold_constant(self):
        """FUZZY_MATCH_THRESHOLD is 0.85."""
        assert FUZZY_MATCH_THRESHOLD == 0.85


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

        _add_utterances(resolver)
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

        _add_utterances(resolver)

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

        _add_utterances(resolver)

        # Should not raise
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "counterpart_0"

    @pytest.mark.asyncio
    async def test_empty_mappings_array(self):
        """Empty mappings array doesn't crash."""
        resolver, create_mock = _make_resolver()
        create_mock.return_value = _make_llm_response([])

        _add_utterances(resolver)
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

        _add_utterances(resolver)
        await resolver._resolve_once()
        assert resolver.resolve("counterpart_0") == "counterpart_0"
