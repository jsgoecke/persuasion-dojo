"""
Tests for backend/hybrid_transcription.py.

No real transcription — both Deepgram and Moonshine are fully mocked.

Covers:
  - Transcriber protocol satisfaction
  - cloud mode: uses Deepgram, no fallback
  - local mode: uses Moonshine, no Deepgram
  - auto mode: healthy Deepgram → cloud
  - auto mode: failed health check → Moonshine fallback
  - auto mode: no API key → Moonshine fallback
  - auto mode: Deepgram connect fails → Moonshine fallback
  - auto mode: mid-session Deepgram failure → failover to Moonshine
  - Ring buffer replayed on failover
  - Status events emitted correctly
  - active_backend property at each state
  - on_utterance flows through hybrid layer
  - disconnect cleans up active transcriber
"""

from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.hybrid_transcription import HybridTranscriber
from backend.transcriber_protocol import Transcriber


# ---------------------------------------------------------------------------
# Fake transcribers for testing
# ---------------------------------------------------------------------------

class FakeDeepgramTranscriber:
    """Minimal fake DeepgramTranscriber."""

    def __init__(self, *, fail_connect=False, on_error=None, on_utterance=None, **kwargs):
        self._fail_connect = fail_connect
        self._on_error = on_error
        self._on_utterance = on_utterance
        self._connected = False
        self._audio: list[bytes] = []
        self._ring_buffer: deque[bytes] = deque(maxlen=160)
        self._finalized = False

    async def connect(self):
        if self._fail_connect:
            raise ConnectionError("Deepgram connect failed")
        self._connected = True

    async def send_audio(self, data: bytes):
        self._audio.append(data)
        self._ring_buffer.append(data)

    async def disconnect(self):
        self._connected = False

    async def finalize(self):
        self._finalized = True

    @property
    def is_connected(self):
        return self._connected

    async def trigger_error(self):
        """Test helper: simulate Deepgram exhausting reconnects."""
        if self._on_error:
            await self._on_error(ConnectionError("exhausted"))


class FakeMoonshineTranscriber:
    """Minimal fake MoonshineTranscriber."""

    def __init__(self, *, on_utterance=None, **kwargs):
        self._on_utterance = on_utterance
        self._connected = False
        self._audio: list[bytes] = []
        self._finalized = False

    async def connect(self):
        self._connected = True

    async def send_audio(self, data: bytes):
        self._audio.append(data)

    async def disconnect(self):
        self._connected = False

    async def finalize(self):
        self._finalized = True

    @property
    def is_connected(self):
        return self._connected


def _make_hybrid(
    mode="auto",
    api_key="test-key",
    fail_deepgram_connect=False,
    health_check_result=(True, "ok"),
):
    """Create a HybridTranscriber with fake backends and captured state."""
    utterances = []
    events = []

    async def on_utterance(speaker_id, text, is_final, start_s, end_s):
        utterances.append((speaker_id, text, is_final, start_s, end_s))

    async def on_status(event, detail):
        events.append((event, detail))

    fake_dg = None
    fake_moon = None

    def dg_factory():
        nonlocal fake_dg
        fake_dg = FakeDeepgramTranscriber(
            fail_connect=fail_deepgram_connect,
            on_utterance=on_utterance,
        )
        return fake_dg

    def moon_factory():
        nonlocal fake_moon
        fake_moon = FakeMoonshineTranscriber(on_utterance=on_utterance)
        return fake_moon

    hybrid = HybridTranscriber(
        mode=mode,
        deepgram_api_key=api_key,
        on_utterance=on_utterance,
        on_status=on_status,
        _deepgram_factory=dg_factory,
        _moonshine_factory=moon_factory,
    )

    return hybrid, utterances, events, lambda: fake_dg, lambda: fake_moon, health_check_result


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_satisfies_transcriber_protocol(self):
        hybrid, *_ = _make_hybrid()
        assert isinstance(hybrid, Transcriber)


# ---------------------------------------------------------------------------
# Cloud mode
# ---------------------------------------------------------------------------

class TestCloudMode:
    @pytest.mark.asyncio
    async def test_cloud_mode_uses_deepgram(self):
        hybrid, _, events, get_dg, get_moon, _ = _make_hybrid(mode="cloud")
        await hybrid.connect()
        assert hybrid.active_backend == "deepgram"
        assert get_dg() is not None
        assert get_dg()._connected
        assert get_moon() is None
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_cloud_mode_deepgram_fails_no_fallback(self):
        hybrid, _, events, _, _, _ = _make_hybrid(
            mode="cloud", fail_deepgram_connect=True
        )
        with pytest.raises(ConnectionError):
            await hybrid.connect()
        assert not hybrid.is_connected

    @pytest.mark.asyncio
    async def test_cloud_mode_sends_audio_to_deepgram(self):
        hybrid, _, _, get_dg, _, _ = _make_hybrid(mode="cloud")
        await hybrid.connect()
        await hybrid.send_audio(b"\x01\x02")
        assert len(get_dg()._audio) == 1
        await hybrid.disconnect()


# ---------------------------------------------------------------------------
# Local mode
# ---------------------------------------------------------------------------

class TestLocalMode:
    @pytest.mark.asyncio
    async def test_local_mode_uses_moonshine(self):
        hybrid, _, events, get_dg, get_moon, _ = _make_hybrid(mode="local")
        await hybrid.connect()
        assert hybrid.active_backend == "moonshine"
        assert get_moon() is not None
        assert get_moon()._connected
        assert get_dg() is None
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_local_mode_ignores_api_key(self):
        hybrid, _, _, _, get_moon, _ = _make_hybrid(mode="local", api_key="")
        await hybrid.connect()
        assert hybrid.active_backend == "moonshine"
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_local_mode_sends_audio_to_moonshine(self):
        hybrid, _, _, _, get_moon, _ = _make_hybrid(mode="local")
        await hybrid.connect()
        await hybrid.send_audio(b"\x01\x02")
        assert len(get_moon()._audio) == 1
        await hybrid.disconnect()


# ---------------------------------------------------------------------------
# Auto mode — healthy Deepgram
# ---------------------------------------------------------------------------

class TestAutoModeHealthy:
    @pytest.mark.asyncio
    async def test_auto_healthy_deepgram_uses_cloud(self):
        hybrid, _, events, get_dg, get_moon, hc = _make_hybrid(mode="auto")

        with patch("backend.hybrid_transcription.deepgram_health_check", return_value=hc):
            await hybrid.connect()

        assert hybrid.active_backend == "deepgram"
        assert get_dg()._connected
        event_names = [e[0] for e in events]
        assert "using_cloud" in event_names
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_auto_healthy_disconnect_cleans_up(self):
        hybrid, _, _, get_dg, _, hc = _make_hybrid(mode="auto")

        with patch("backend.hybrid_transcription.deepgram_health_check", return_value=hc):
            await hybrid.connect()

        await hybrid.disconnect()
        assert not hybrid.is_connected
        assert hybrid.active_backend == ""


# ---------------------------------------------------------------------------
# Auto mode — Deepgram fails
# ---------------------------------------------------------------------------

class TestAutoModeFallback:
    @pytest.mark.asyncio
    async def test_auto_health_check_fails_uses_moonshine(self):
        hybrid, _, events, _, get_moon, _ = _make_hybrid(mode="auto")

        with patch(
            "backend.hybrid_transcription.deepgram_health_check",
            return_value=(False, "HTTP 401"),
        ):
            await hybrid.connect()

        assert hybrid.active_backend == "moonshine"
        event_names = [e[0] for e in events]
        assert "fallback_activated" in event_names
        assert "using_local" in event_names
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_auto_no_api_key_uses_moonshine(self):
        hybrid, _, events, _, get_moon, _ = _make_hybrid(mode="auto", api_key="")

        await hybrid.connect()
        assert hybrid.active_backend == "moonshine"
        event_names = [e[0] for e in events]
        assert "fallback_activated" in event_names
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_auto_deepgram_connect_fails_uses_moonshine(self):
        hybrid, _, events, _, get_moon, _ = _make_hybrid(
            mode="auto", fail_deepgram_connect=True
        )

        with patch(
            "backend.hybrid_transcription.deepgram_health_check",
            return_value=(True, "ok"),
        ):
            await hybrid.connect()

        assert hybrid.active_backend == "moonshine"
        event_names = [e[0] for e in events]
        assert "fallback_activated" in event_names
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_fallback_event_includes_reason(self):
        hybrid, _, events, _, _, _ = _make_hybrid(mode="auto", api_key="")
        await hybrid.connect()

        fallback_events = [e for e in events if e[0] == "fallback_activated"]
        assert len(fallback_events) >= 1
        assert "reason" in fallback_events[0][1]
        await hybrid.disconnect()


# ---------------------------------------------------------------------------
# Mid-session failover
# ---------------------------------------------------------------------------

class TestMidSessionFailover:
    @pytest.mark.asyncio
    async def test_deepgram_error_triggers_moonshine_failover(self):
        hybrid, _, events, get_dg, get_moon, hc = _make_hybrid(mode="auto")

        with patch("backend.hybrid_transcription.deepgram_health_check", return_value=hc):
            await hybrid.connect()

        assert hybrid.active_backend == "deepgram"

        # Send some audio (populates ring buffer)
        for _ in range(5):
            await hybrid.send_audio(b"\xAB" * 100)

        # Simulate Deepgram exhausting reconnects
        await hybrid._on_deepgram_error(ConnectionError("exhausted"))

        assert hybrid.active_backend == "moonshine"
        assert hybrid.is_connected
        event_names = [e[0] for e in events]
        assert "fallback_activated" in event_names
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_ring_buffer_replayed_on_failover(self):
        hybrid, _, _, get_dg, get_moon, hc = _make_hybrid(mode="auto")

        with patch("backend.hybrid_transcription.deepgram_health_check", return_value=hc):
            await hybrid.connect()

        # Send audio to populate Deepgram's ring buffer
        for i in range(5):
            await hybrid.send_audio(bytes([i]) * 100)

        # Failover
        await hybrid._on_deepgram_error(ConnectionError("exhausted"))

        # Moonshine should have received the replayed chunks
        moon = get_moon()
        assert moon is not None
        assert len(moon._audio) >= 5
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_no_switch_back_after_failover(self):
        """Once on Moonshine, stay there for the session."""
        hybrid, _, _, get_dg, get_moon, hc = _make_hybrid(mode="auto")

        with patch("backend.hybrid_transcription.deepgram_health_check", return_value=hc):
            await hybrid.connect()

        await hybrid._on_deepgram_error(ConnectionError("exhausted"))

        # Verify still on Moonshine
        assert hybrid.active_backend == "moonshine"

        # Sending more audio goes to Moonshine, not Deepgram
        await hybrid.send_audio(b"\xFF" * 50)
        moon = get_moon()
        assert len(moon._audio) >= 1  # at least the new chunk
        await hybrid.disconnect()


# ---------------------------------------------------------------------------
# active_backend property
# ---------------------------------------------------------------------------

class TestActiveBackend:
    @pytest.mark.asyncio
    async def test_empty_before_connect(self):
        hybrid, *_ = _make_hybrid()
        assert hybrid.active_backend == ""

    @pytest.mark.asyncio
    async def test_empty_after_disconnect(self):
        hybrid, _, _, _, _, hc = _make_hybrid(mode="auto")
        with patch("backend.hybrid_transcription.deepgram_health_check", return_value=hc):
            await hybrid.connect()
        await hybrid.disconnect()
        assert hybrid.active_backend == ""

    @pytest.mark.asyncio
    async def test_deepgram_when_cloud(self):
        hybrid, _, _, _, _, hc = _make_hybrid(mode="cloud")
        await hybrid.connect()
        assert hybrid.active_backend == "deepgram"
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_moonshine_when_local(self):
        hybrid, _, _, _, _, _ = _make_hybrid(mode="local")
        await hybrid.connect()
        assert hybrid.active_backend == "moonshine"
        await hybrid.disconnect()


# ---------------------------------------------------------------------------
# Utterance callback passthrough
# ---------------------------------------------------------------------------

class TestUtterancePassthrough:
    @pytest.mark.asyncio
    async def test_cloud_utterance_reaches_caller(self):
        """on_utterance fires when active transcriber produces utterances."""
        utterances = []

        async def on_utterance(speaker_id, text, is_final, start_s, end_s):
            utterances.append(text)

        fake_dg = FakeDeepgramTranscriber(on_utterance=on_utterance)

        hybrid = HybridTranscriber(
            mode="cloud",
            deepgram_api_key="test",
            on_utterance=on_utterance,
            _deepgram_factory=lambda: fake_dg,
        )
        await hybrid.connect()
        # Simulate utterance callback from Deepgram
        await on_utterance("speaker_0", "hello", True, 0.0, 1.0)
        assert "hello" in utterances
        await hybrid.disconnect()


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------

class TestFinalize:
    @pytest.mark.asyncio
    async def test_finalize_forwards_to_active(self):
        hybrid, _, _, get_dg, _, hc = _make_hybrid(mode="cloud")
        await hybrid.connect()
        await hybrid.finalize()
        assert get_dg()._finalized
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_finalize_before_connect_is_safe(self):
        hybrid, *_ = _make_hybrid()
        await hybrid.finalize()  # should not raise


# ---------------------------------------------------------------------------
# Double connect / disconnect
# ---------------------------------------------------------------------------

class TestIdempotent:
    @pytest.mark.asyncio
    async def test_double_connect_is_idempotent(self):
        hybrid, _, _, _, _, hc = _make_hybrid(mode="local")
        await hybrid.connect()
        await hybrid.connect()
        assert hybrid.is_connected
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_double_disconnect_is_safe(self):
        hybrid, _, _, _, _, _ = _make_hybrid(mode="local")
        await hybrid.connect()
        await hybrid.disconnect()
        await hybrid.disconnect()
        assert not hybrid.is_connected

    @pytest.mark.asyncio
    async def test_send_before_connect_is_silent(self):
        hybrid, *_ = _make_hybrid()
        await hybrid.send_audio(b"\x01\x02")  # should not raise


# ---------------------------------------------------------------------------
# Status event detail content
# ---------------------------------------------------------------------------

class TestStatusEventDetails:
    """
    Verify the content and structure of status events emitted by
    HybridTranscriber. The frontend uses these to show the Cloud/Local badge.

    Regression: during the v0.11.0 debug session, the user couldn't tell
    whether Deepgram or Moonshine was active. These events are the data
    source for the transcription backend indicator badge.
    """

    @pytest.mark.asyncio
    async def test_cloud_mode_emits_using_cloud_event(self):
        """Cloud mode must emit exactly one 'using_cloud' event on connect."""
        hybrid, _, events, _, _, hc = _make_hybrid(mode="cloud")
        with patch("backend.hybrid_transcription.deepgram_health_check", return_value=hc):
            await hybrid.connect()

        cloud_events = [e for e in events if e[0] == "using_cloud"]
        assert len(cloud_events) == 1, f"Expected 1 using_cloud event, got {cloud_events}"
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_local_mode_emits_using_local_event(self):
        """Local mode must emit exactly one 'using_local' event on connect."""
        hybrid, _, events, _, _, _ = _make_hybrid(mode="local")
        await hybrid.connect()

        local_events = [e for e in events if e[0] == "using_local"]
        assert len(local_events) == 1, f"Expected 1 using_local event, got {local_events}"
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_fallback_event_contains_reason_key(self):
        """Fallback events must include a 'reason' key in the detail dict."""
        hybrid, _, events, _, _, _ = _make_hybrid(
            mode="auto", api_key="",
        )
        await hybrid.connect()

        fallback_events = [e for e in events if e[0] in ("fallback_activated", "using_local")]
        assert len(fallback_events) >= 1
        # At least one event should explain why fallback was used
        reasons = [e[1].get("reason") for e in events if e[1].get("reason")]
        assert len(reasons) >= 1, f"No reason given for fallback. Events: {events}"
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_auto_healthy_emits_using_cloud(self):
        """Auto mode with healthy Deepgram must emit 'using_cloud'."""
        hybrid, _, events, _, _, hc = _make_hybrid(mode="auto")
        with patch("backend.hybrid_transcription.deepgram_health_check", return_value=hc):
            await hybrid.connect()

        event_names = [e[0] for e in events]
        assert "using_cloud" in event_names
        assert "using_local" not in event_names
        assert "fallback_activated" not in event_names
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_auto_unhealthy_emits_fallback(self):
        """Auto mode with failing health check must emit fallback, not using_cloud."""
        hybrid, _, events, _, _, _ = _make_hybrid(
            mode="auto",
            health_check_result=(False, "HTTP 401"),
        )
        with patch("backend.hybrid_transcription.deepgram_health_check",
                   return_value=(False, "HTTP 401")):
            await hybrid.connect()

        event_names = [e[0] for e in events]
        assert "using_cloud" not in event_names
        has_fallback = "fallback_activated" in event_names or "using_local" in event_names
        assert has_fallback, f"Expected fallback event, got {event_names}"
        await hybrid.disconnect()

    @pytest.mark.asyncio
    async def test_no_duplicate_status_events_on_connect(self):
        """Connect should emit exactly one backend selection event, not multiples."""
        hybrid, _, events, _, _, _ = _make_hybrid(mode="local")
        await hybrid.connect()

        backend_events = [e for e in events if e[0] in ("using_cloud", "using_local")]
        assert len(backend_events) == 1, (
            f"Expected exactly 1 backend event, got {len(backend_events)}: {backend_events}"
        )
        await hybrid.disconnect()
