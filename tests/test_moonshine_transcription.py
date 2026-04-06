"""
Tests for backend/moonshine_transcription.py.

No real model loading — all tests use FakeMoonshine mock via _transcriber_factory.

Covers:
  - Transcriber protocol satisfaction
  - connect / disconnect lifecycle
  - PCM int16 → float32 conversion
  - Transcript event → UtteranceCallback mapping
  - Interim (is_final=False) and final (is_final=True) utterances
  - Speaker ID assignment (diarize=True/False)
  - Model cache reuse
  - Empty/invalid audio handling
  - Disconnect flushes pending text
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from backend.moonshine_transcription import MoonshineTranscriber, _MODEL_CACHE
from backend.transcriber_protocol import Transcriber


# ---------------------------------------------------------------------------
# Fake Moonshine objects
# ---------------------------------------------------------------------------

@dataclass
class FakeWord:
    text: str


@dataclass
class FakeTranscriptLine:
    words: list[FakeWord] = field(default_factory=list)
    is_new: bool = False
    is_updated: bool = False
    has_text_changed: bool = False
    has_speaker_id: bool = False
    speaker_id: str = ""
    speaker_index: int = 0


@dataclass
class FakeTranscriptEvent:
    line: FakeTranscriptLine = field(default_factory=FakeTranscriptLine)
    stream_handle: int = 0


class FakeMoonshineTranscriber:
    """Minimal fake for moonshine_voice.transcriber.Transcriber."""

    def __init__(self) -> None:
        self._listeners: list[Callable] = []
        self._started = False
        self._audio_chunks: list[list[float]] = []

    def add_listener(self, listener: Callable) -> None:
        self._listeners.append(listener)

    def remove_all_listeners(self) -> None:
        self._listeners.clear()

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def add_audio(self, audio_data: list[float], sample_rate: int = 16000) -> None:
        self._audio_chunks.append(audio_data)

    def update_transcription(self, flags: int = 0) -> None:
        pass

    def fire_event(self, event: FakeTranscriptEvent) -> None:
        """Test helper: simulate a transcript event from the model."""
        for listener in self._listeners:
            listener(event)


def _make_client(
    *,
    diarize: bool = False,
    on_status=None,
) -> tuple[MoonshineTranscriber, list[tuple], FakeMoonshineTranscriber]:
    """Create a MoonshineTranscriber with a FakeMoonshine and captured utterances."""
    fake = FakeMoonshineTranscriber()
    utterances: list[tuple] = []

    async def on_utterance(speaker_id, text, is_final, start_s, end_s):
        utterances.append((speaker_id, text, is_final, start_s, end_s))

    client = MoonshineTranscriber(
        on_utterance=on_utterance,
        on_status=on_status,
        diarize=diarize,
        _transcriber_factory=lambda: fake,
    )
    return client, utterances, fake


def _pcm_bytes(samples: list[int]) -> bytes:
    """Convert a list of int16 values to PCM 16-bit LE bytes."""
    return struct.pack(f"<{len(samples)}h", *samples)


# ---------------------------------------------------------------------------
# Protocol satisfaction
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_satisfies_transcriber_protocol(self):
        client, _, _ = _make_client()
        assert isinstance(client, Transcriber)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_connect_sets_connected(self):
        client, _, fake = _make_client()
        assert not client.is_connected
        await client.connect()
        assert client.is_connected
        assert fake._started
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_clears_connected(self):
        client, _, _ = _make_client()
        await client.connect()
        await client.disconnect()
        assert not client.is_connected

    @pytest.mark.asyncio
    async def test_double_connect_is_idempotent(self):
        client, _, _ = _make_client()
        await client.connect()
        await client.connect()  # should not raise
        assert client.is_connected
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_double_disconnect_is_safe(self):
        client, _, _ = _make_client()
        await client.connect()
        await client.disconnect()
        await client.disconnect()  # should not raise

    @pytest.mark.asyncio
    async def test_connect_fires_status_event(self):
        events: list[tuple] = []

        async def on_status(event, detail):
            events.append((event, detail))

        client, _, _ = _make_client(on_status=on_status)
        await client.connect()
        assert any(e[0] == "connected" for e in events)
        await client.disconnect()


# ---------------------------------------------------------------------------
# Audio input
# ---------------------------------------------------------------------------

class TestSendAudio:
    @pytest.mark.asyncio
    async def test_pcm_int16_converted_to_float32(self):
        client, _, fake = _make_client()
        await client.connect()

        # Send a known PCM chunk
        pcm = _pcm_bytes([0, 16384, -16384, 32767])
        await client.send_audio(pcm)

        assert len(fake._audio_chunks) == 1
        samples = fake._audio_chunks[0]
        assert len(samples) == 4
        assert abs(samples[0] - 0.0) < 0.001
        assert abs(samples[1] - 0.5) < 0.001
        assert abs(samples[2] - (-0.5)) < 0.001
        assert abs(samples[3] - 1.0) < 0.001
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_empty_bytes_ignored(self):
        client, _, fake = _make_client()
        await client.connect()
        await client.send_audio(b"")
        assert len(fake._audio_chunks) == 0
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_send_before_connect_is_silent(self):
        client, _, fake = _make_client()
        await client.send_audio(_pcm_bytes([100, 200]))
        assert len(fake._audio_chunks) == 0

    @pytest.mark.asyncio
    async def test_odd_byte_count_handled(self):
        """Odd byte count (not multiple of 2) should not crash."""
        client, _, fake = _make_client()
        await client.connect()
        await client.send_audio(b"\x01\x02\x03")  # 3 bytes = 1 sample + 1 leftover
        assert len(fake._audio_chunks) == 1
        assert len(fake._audio_chunks[0]) == 1
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_single_byte_ignored(self):
        """Single byte can't form a sample."""
        client, _, fake = _make_client()
        await client.connect()
        await client.send_audio(b"\x01")
        assert len(fake._audio_chunks) == 0
        await client.disconnect()


# ---------------------------------------------------------------------------
# Transcript events → UtteranceCallback
# ---------------------------------------------------------------------------

class TestTranscriptEvents:
    @pytest.mark.asyncio
    async def test_new_line_fires_interim(self):
        client, utterances, fake = _make_client()
        await client.connect()

        event = FakeTranscriptEvent(
            line=FakeTranscriptLine(
                words=[FakeWord("hello"), FakeWord("world")],
                is_new=True,
            )
        )
        fake.fire_event(event)
        await asyncio.sleep(0.05)

        assert len(utterances) == 1
        assert utterances[0][1] == "hello world"
        assert utterances[0][2] is False  # is_final
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_updated_line_fires_interim(self):
        client, utterances, fake = _make_client()
        await client.connect()

        event = FakeTranscriptEvent(
            line=FakeTranscriptLine(
                words=[FakeWord("hello"), FakeWord("there")],
                is_updated=True,
                has_text_changed=True,
            )
        )
        fake.fire_event(event)
        await asyncio.sleep(0.05)

        assert len(utterances) == 1
        assert utterances[0][2] is False  # interim
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_completed_line_fires_final(self):
        client, utterances, fake = _make_client()
        await client.connect()

        # A line that is not new and not updated-with-changes = completed
        event = FakeTranscriptEvent(
            line=FakeTranscriptLine(
                words=[FakeWord("final"), FakeWord("result")],
                is_new=False,
                is_updated=False,
                has_text_changed=False,
            )
        )
        fake.fire_event(event)
        await asyncio.sleep(0.05)

        assert len(utterances) == 1
        assert utterances[0][1] == "final result"
        assert utterances[0][2] is True  # is_final
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self):
        client, utterances, fake = _make_client()
        await client.connect()

        event = FakeTranscriptEvent(
            line=FakeTranscriptLine(words=[], is_new=True)
        )
        fake.fire_event(event)
        await asyncio.sleep(0.05)

        assert len(utterances) == 0
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_timestamps_are_positive(self):
        client, utterances, fake = _make_client()
        await client.connect()
        await asyncio.sleep(0.01)  # Let some time pass

        event = FakeTranscriptEvent(
            line=FakeTranscriptLine(
                words=[FakeWord("test")],
                is_new=True,
            )
        )
        fake.fire_event(event)
        await asyncio.sleep(0.05)

        assert len(utterances) == 1
        start_s = utterances[0][3]
        end_s = utterances[0][4]
        assert start_s >= 0
        assert end_s >= start_s
        await client.disconnect()


# ---------------------------------------------------------------------------
# Speaker diarization
# ---------------------------------------------------------------------------

class TestDiarization:
    @pytest.mark.asyncio
    async def test_diarize_false_always_speaker_0(self):
        client, utterances, fake = _make_client(diarize=False)
        await client.connect()

        event = FakeTranscriptEvent(
            line=FakeTranscriptLine(
                words=[FakeWord("hello")],
                is_new=True,
                has_speaker_id=True,
                speaker_index=3,
            )
        )
        fake.fire_event(event)
        await asyncio.sleep(0.05)

        assert utterances[0][0] == "speaker_0"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_diarize_true_uses_speaker_index(self):
        client, utterances, fake = _make_client(diarize=True)
        await client.connect()

        event = FakeTranscriptEvent(
            line=FakeTranscriptLine(
                words=[FakeWord("hello")],
                is_new=True,
                has_speaker_id=True,
                speaker_index=2,
            )
        )
        fake.fire_event(event)
        await asyncio.sleep(0.05)

        assert utterances[0][0] == "speaker_2"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_diarize_true_no_speaker_id_falls_back(self):
        client, utterances, fake = _make_client(diarize=True)
        await client.connect()

        event = FakeTranscriptEvent(
            line=FakeTranscriptLine(
                words=[FakeWord("hello")],
                is_new=True,
                has_speaker_id=False,
            )
        )
        fake.fire_event(event)
        await asyncio.sleep(0.05)

        assert utterances[0][0] == "speaker_0"
        await client.disconnect()


# ---------------------------------------------------------------------------
# Disconnect flushes pending text
# ---------------------------------------------------------------------------

class TestDisconnectFlush:
    @pytest.mark.asyncio
    async def test_disconnect_flushes_pending_text(self):
        client, utterances, fake = _make_client()
        await client.connect()

        # Fire a "new line" event (interim, not final)
        event = FakeTranscriptEvent(
            line=FakeTranscriptLine(
                words=[FakeWord("pending"), FakeWord("text")],
                is_new=True,
            )
        )
        fake.fire_event(event)
        await asyncio.sleep(0.05)

        # Should have one interim utterance
        assert len(utterances) == 1
        assert utterances[0][2] is False

        # Disconnect should flush as final
        await client.disconnect()
        await asyncio.sleep(0.05)

        assert len(utterances) == 2
        assert utterances[1][1] == "pending text"
        assert utterances[1][2] is True  # flushed as final


# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------

class TestModelCache:
    def test_cache_starts_empty_or_populated(self):
        """Model cache is a module-level dict."""
        assert isinstance(_MODEL_CACHE, dict)

    @pytest.mark.asyncio
    async def test_factory_injection_bypasses_cache(self):
        """_transcriber_factory skips the real model loading path."""
        client, _, fake = _make_client()
        await client.connect()
        # No real model was loaded — fake was used
        assert client._transcriber is fake
        await client.disconnect()


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------

class TestFinalize:
    @pytest.mark.asyncio
    async def test_finalize_calls_update_transcription(self):
        client, _, fake = _make_client()
        await client.connect()
        fake.update_transcription = MagicMock()
        await client.finalize()
        fake.update_transcription.assert_called_once()
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_finalize_before_connect_is_safe(self):
        client, _, _ = _make_client()
        await client.finalize()  # should not raise
