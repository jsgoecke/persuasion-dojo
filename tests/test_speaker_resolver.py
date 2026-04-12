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
        import time
        resolver, create_mock = _make_resolver()
        create_mock.return_value = _make_llm_response([])

        _add_utterances(resolver, n=6)
        # Push start_time far back so adaptive scheduling uses the fast test interval
        resolver._start_time = time.monotonic() - 300

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


# ---------------------------------------------------------------------------
# Tests — adaptive scheduling (_current_interval)
# ---------------------------------------------------------------------------

class TestAdaptiveScheduling:
    def test_default_interval_before_any_utterance(self):
        """Before any utterance, use the configured interval."""
        resolver, _ = _make_resolver()
        assert resolver._current_interval() == 0.05  # test fast interval

    def test_intro_phase_interval(self):
        """During intro phase (< 120s), interval is 10s."""
        resolver, _ = _make_resolver()
        resolver.add_utterance("counterpart_0", "Hello")
        # _start_time is just set, elapsed ~ 0s
        assert resolver._current_interval() == 10.0

    def test_default_phase_after_intro(self):
        """After 120s with unlocked speakers, use configured interval."""
        import time
        resolver, _ = _make_resolver()
        resolver.add_utterance("counterpart_0", "Hello")
        # Simulate elapsed > 120s
        resolver._start_time = time.monotonic() - 200
        resolver._mappings["counterpart_0"] = "Alice Chen"
        assert resolver._current_interval() == resolver._interval

    def test_coast_phase_all_locked(self):
        """When all speakers are locked, coast at 60s."""
        import time
        resolver, _ = _make_resolver()
        resolver.add_utterance("counterpart_0", "Hello")
        resolver._start_time = time.monotonic() - 200
        resolver._mappings["counterpart_0"] = "Alice Chen"
        resolver._locked.add("counterpart_0")
        assert resolver._current_interval() == 60.0

    def test_coast_requires_all_locked(self):
        """If any speaker is unlocked, don't coast."""
        import time
        resolver, _ = _make_resolver()
        resolver.add_utterance("counterpart_0", "Hello")
        resolver._start_time = time.monotonic() - 200
        resolver._mappings["counterpart_0"] = "Alice Chen"
        resolver._mappings["counterpart_1"] = "Bob Smith"
        resolver._locked.add("counterpart_0")
        # counterpart_1 is not locked
        assert resolver._current_interval() == resolver._interval

    def test_coast_requires_non_empty_mappings(self):
        """Empty mappings should not trigger coast even with elapsed > 120s."""
        import time
        resolver, _ = _make_resolver()
        resolver.add_utterance("counterpart_0", "Hello")
        resolver._start_time = time.monotonic() - 200
        # No mappings yet
        assert resolver._current_interval() == resolver._interval


# ---------------------------------------------------------------------------
# Tests — resolver accuracy metrics
# ---------------------------------------------------------------------------

class TestResolverMetrics:
    def test_initial_metrics(self):
        """Fresh resolver has zero metrics."""
        resolver, _ = _make_resolver()
        m = resolver.metrics
        assert m["total_resolutions"] == 0
        assert m["user_corrections"] == 0
        assert m["time_to_first_resolution"] is None
        assert m["locked_at_end"] == 0

    @pytest.mark.asyncio
    async def test_total_resolutions_incremented(self):
        """Each successful mapping bumps total_resolutions."""
        resolver, create_mock = _make_resolver()
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.85, "evidence": "named"},
        ])
        _add_utterances(resolver)
        await resolver._resolve_once()
        assert resolver.metrics["total_resolutions"] == 1

    @pytest.mark.asyncio
    async def test_time_to_first_resolution(self):
        """time_to_first_resolution is set on the first mapping."""
        resolver, create_mock = _make_resolver()
        create_mock.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.85, "evidence": "named"},
        ])
        _add_utterances(resolver)
        await resolver._resolve_once()
        ttfr = resolver.metrics["time_to_first_resolution"]
        assert ttfr is not None
        assert isinstance(ttfr, float)
        assert ttfr >= 0.0

    def test_user_correction_tracked(self):
        """set_confirmed_name with a different name increments user_corrections."""
        resolver, _ = _make_resolver()
        resolver._mappings["counterpart_0"] = "Alice Chen"
        resolver.set_confirmed_name("counterpart_0", "Bob Smith")
        assert resolver.metrics["user_corrections"] == 1

    def test_same_name_confirmation_not_counted(self):
        """Confirming the same name is not a correction."""
        resolver, _ = _make_resolver()
        resolver._mappings["counterpart_0"] = "Alice Chen"
        resolver.set_confirmed_name("counterpart_0", "Alice Chen")
        assert resolver.metrics["user_corrections"] == 0

    def test_locked_at_end_reflects_locked_set(self):
        """locked_at_end counts the locked speakers."""
        resolver, _ = _make_resolver()
        resolver._locked.add("counterpart_0")
        resolver._locked.add("counterpart_1")
        assert resolver.metrics["locked_at_end"] == 2

    def test_confidences_property(self):
        """confidences property returns a copy of internal state."""
        resolver, _ = _make_resolver()
        resolver._confidences["counterpart_0"] = 0.85
        c = resolver.confidences
        assert c == {"counterpart_0": 0.85}
        # It's a copy, not a reference
        c["counterpart_0"] = 0.0
        assert resolver._confidences["counterpart_0"] == 0.85


# ---------------------------------------------------------------------------
# Tests — confidence-based prompt suppression (coaching engine integration)
# ---------------------------------------------------------------------------

class TestConfidenceSuppression:
    """Test that CoachingEngine suppresses low-confidence speaker names."""

    def _make_engine(self, participants=None):
        from backend.coaching_engine import CoachingEngine
        return CoachingEngine(
            user_speaker="user",
            anthropic_client=MagicMock(),
            participants=participants or [],
        )

    def test_update_speaker_name_adds_new(self):
        """update_speaker_name adds a new participant entry."""
        engine = self._make_engine()
        engine.update_speaker_name("counterpart_0", "Alice Chen", 0.85)
        assert engine._resolve_speaker_name("counterpart_0") == "Alice Chen"

    def test_update_speaker_name_updates_existing(self):
        """update_speaker_name updates an existing participant's name."""
        engine = self._make_engine([
            {"speaker_id": "counterpart_0", "name": "Unknown", "archetype": "Architect"},
        ])
        engine.update_speaker_name("counterpart_0", "Alice Chen", 0.9)
        assert engine._resolve_speaker_name("counterpart_0") == "Alice Chen"

    def test_low_confidence_suppresses_name(self):
        """Name is suppressed (returns '') when resolver_confidence < 0.7."""
        engine = self._make_engine()
        engine.update_speaker_name("counterpart_0", "Alice Chen", 0.5)
        assert engine._resolve_speaker_name("counterpart_0") == ""

    def test_threshold_boundary(self):
        """Exactly 0.7 confidence is NOT suppressed."""
        engine = self._make_engine()
        engine.update_speaker_name("counterpart_0", "Alice Chen", 0.7)
        assert engine._resolve_speaker_name("counterpart_0") == "Alice Chen"

    def test_no_resolver_confidence_not_suppressed(self):
        """Pre-seeded participants without resolver_confidence are never suppressed."""
        engine = self._make_engine([
            {"speaker_id": "counterpart_0", "name": "Alice Chen", "archetype": "Architect"},
        ])
        # No resolver_confidence key — should NOT suppress
        assert engine._resolve_speaker_name("counterpart_0") == "Alice Chen"

    def test_confirmed_name_full_confidence(self):
        """User-confirmed name at confidence 1.0 is always shown."""
        engine = self._make_engine()
        engine.update_speaker_name("counterpart_0", "Alice Chen", 1.0)
        assert engine._resolve_speaker_name("counterpart_0") == "Alice Chen"

    def test_index_based_lookup_suppressed(self):
        """Index-based lookup also checks resolver_confidence."""
        engine = self._make_engine([
            {"name": "Alice Chen", "archetype": "Architect", "resolver_confidence": 0.4},
        ])
        # counterpart_0 → index 0 → Alice Chen, but confidence is 0.4
        assert engine._resolve_speaker_name("counterpart_0") == ""


# ---------------------------------------------------------------------------
# Turn tracker boost integration
# ---------------------------------------------------------------------------

class TestTurnTrackerBoost:
    """Test turn tracker confidence boost in _resolve_once."""

    @pytest.mark.asyncio
    async def test_turn_tracker_boost_agrees(self):
        """Turn tracker boost adds +0.10 when it agrees with LLM mapping."""
        resolver, mock_create = _make_resolver(
            known_names=["Greg Wilson", "Sarah Chen"],
            confidence_threshold=0.7,
            lock_threshold=0.8,
        )
        _add_utterances(resolver, n=6, speaker="counterpart_0")
        mock_create.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Greg Wilson",
             "confidence": 0.72, "evidence": "said Hi Greg"},
        ])
        # Inject turn tracker scores that agree with LLM
        resolver.set_turn_tracker_scores({
            "counterpart_0": {"Greg Wilson": 1.0},
        })
        await resolver._resolve_once()
        # 0.72 + 0.10 = 0.82, but capped at lock_threshold - 0.01 = 0.79
        assert resolver._confidences["counterpart_0"] == pytest.approx(0.79, abs=0.01)
        assert resolver._metrics["turn_tracker_agreements"] == 1

    @pytest.mark.asyncio
    async def test_turn_tracker_disagreement_tracked(self):
        """Turn tracker disagreement is recorded in metrics."""
        resolver, mock_create = _make_resolver(
            known_names=["Greg Wilson", "Sarah Chen"],
        )
        _add_utterances(resolver, n=6)
        mock_create.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Greg Wilson",
             "confidence": 0.75, "evidence": "context"},
        ])
        # Tracker says Sarah, LLM says Greg
        resolver.set_turn_tracker_scores({
            "counterpart_0": {"Sarah Chen": 1.0},
        })
        await resolver._resolve_once()
        assert resolver._metrics["turn_tracker_disagreements"] == 1

    @pytest.mark.asyncio
    async def test_combined_voiceprint_and_turn_tracker_capped(self):
        """Voiceprint + turn tracker boosts combined cannot exceed lock threshold."""
        resolver, mock_create = _make_resolver(
            known_names=["Greg Wilson"],
            confidence_threshold=0.7,
            lock_threshold=0.8,
        )
        _add_utterances(resolver, n=6, speaker="counterpart_0")
        mock_create.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Greg Wilson",
             "confidence": 0.72, "evidence": "context"},
        ])
        # Both boosts agree
        resolver.set_voiceprint_match("counterpart_0", "Greg Wilson", 0.85)
        resolver.set_turn_tracker_scores({
            "counterpart_0": {"Greg Wilson": 1.0},
        })
        await resolver._resolve_once()
        # 0.72 + 0.15 (voiceprint) + 0.10 (turn tracker) = 0.97,
        # but capped at 0.79
        assert resolver._confidences["counterpart_0"] == pytest.approx(0.79, abs=0.01)
        assert "counterpart_0" not in resolver._locked

    @pytest.mark.asyncio
    async def test_turn_tracker_no_scores_no_effect(self):
        """When turn tracker has no scores, confidence is unchanged (except cap)."""
        resolver, mock_create = _make_resolver(
            known_names=["Greg Wilson"],
            confidence_threshold=0.7,
            lock_threshold=0.8,
        )
        _add_utterances(resolver, n=6, speaker="counterpart_0")
        mock_create.return_value = _make_llm_response([
            {"speaker_id": "counterpart_0", "name": "Greg Wilson",
             "confidence": 0.75, "evidence": "context"},
        ])
        # No turn tracker scores
        await resolver._resolve_once()
        # 0.75, capped at 0.79 — no boost, but cap applies
        assert resolver._confidences["counterpart_0"] == pytest.approx(0.75, abs=0.01)
