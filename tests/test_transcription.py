"""
Tests for backend/transcription.py.

No network calls — the WebSocket is fully injected via _connect_fn.

Covers:
  - _speaker_from_words helper
  - _handle_results: transcript routing, is_final propagation,
    timing extraction, speaker mapping
  - _handle_message: ignores non-JSON, non-Results types
  - connect / disconnect lifecycle
  - send_audio enqueues bytes; silently drops when not connected
  - reconnect on recv error (up to max_reconnects, then on_error fires)
  - Deepgram error event is logged without crashing
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.transcription import DeepgramTranscriber, _speaker_from_words


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

def _results_msg(
    transcript: str,
    is_final: bool = True,
    start: float = 0.0,
    duration: float = 1.0,
    words: list[dict] | None = None,
) -> str:
    """Build a minimal Deepgram Results JSON string."""
    return json.dumps({
        "type": "Results",
        "is_final": is_final,
        "start": start,
        "duration": duration,
        "channel": {
            "alternatives": [
                {
                    "transcript": transcript,
                    "confidence": 0.99,
                    "words": words or [],
                }
            ]
        },
    })


def _words(speaker: int, *texts: str) -> list[dict]:
    return [
        {"word": t, "start": i * 0.5, "end": (i + 1) * 0.5,
         "confidence": 0.95, "speaker": speaker, "punctuated_word": t}
        for i, t in enumerate(texts)
    ]


class FakeWS:
    """
    Minimal fake WebSocket.

    Receives are driven by pre-queued messages; sends are captured.
    """

    def __init__(self, messages: list[str | bytes | Exception] | None = None):
        self._q: asyncio.Queue = asyncio.Queue()
        for m in (messages or []):
            self._q.put_nowait(m)
        self.sent: list[bytes] = []
        self.closed = False

    def queue(self, msg: str | bytes | Exception) -> None:
        self._q.put_nowait(msg)

    async def recv(self) -> str | bytes:
        item = await self._q.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


def make_client(
    ws_messages: list | None = None,
    *,
    max_reconnects: int = 1,
    reconnect_delay_s: float = 0.0,
) -> tuple[DeepgramTranscriber, list[tuple], FakeWS]:
    """
    Create a DeepgramTranscriber with a FakeWS and a captured utterance list.
    """
    ws = FakeWS(ws_messages)
    utterances: list[tuple] = []

    async def on_utterance(speaker_id, text, is_final, start_s, end_s):
        utterances.append((speaker_id, text, is_final, start_s, end_s))

    async def connect_fn(url, *, extra_headers=None, **_):
        return ws

    client = DeepgramTranscriber(
        api_key="test-key",
        on_utterance=on_utterance,
        max_reconnects=max_reconnects,
        reconnect_delay_s=reconnect_delay_s,
        _connect_fn=connect_fn,
    )
    return client, utterances, ws


# ---------------------------------------------------------------------------
# _speaker_from_words
# ---------------------------------------------------------------------------

class TestSpeakerFromWords:
    def test_empty_words_returns_speaker_0(self):
        assert _speaker_from_words([]) == "speaker_0"

    def test_single_speaker(self):
        assert _speaker_from_words(_words(2, "hello", "world")) == "speaker_2"

    def test_majority_speaker_wins(self):
        # speaker 0 has 1 word, speaker 1 has 2 words
        words = _words(0, "hi") + _words(1, "hello", "there")
        assert _speaker_from_words(words) == "speaker_1"

    def test_tie_goes_to_lower_speaker_id(self):
        # equal counts → lower speaker int wins (via -k in max)
        words = _words(2, "a") + _words(0, "b")
        assert _speaker_from_words(words) == "speaker_0"

    def test_words_without_speaker_key_fallback(self):
        words = [{"word": "hi", "start": 0.0, "end": 0.5, "confidence": 0.9}]
        assert _speaker_from_words(words) == "speaker_0"

    def test_speaker_0_is_formatted_correctly(self):
        assert _speaker_from_words(_words(0, "test")) == "speaker_0"

    def test_speaker_5_is_formatted_correctly(self):
        assert _speaker_from_words(_words(5, "test")) == "speaker_5"


# ---------------------------------------------------------------------------
# _handle_message — routing
# ---------------------------------------------------------------------------

class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_results_event_fires_callback(self):
        client, utterances, _ = make_client()
        msg = _results_msg("hello world")
        await client._handle_message(msg)
        assert len(utterances) == 1
        assert utterances[0][1] == "hello world"

    @pytest.mark.asyncio
    async def test_empty_transcript_skipped(self):
        client, utterances, _ = make_client()
        await client._handle_message(_results_msg(""))
        assert utterances == []

    @pytest.mark.asyncio
    async def test_whitespace_only_transcript_skipped(self):
        client, utterances, _ = make_client()
        await client._handle_message(_results_msg("   "))
        assert utterances == []

    @pytest.mark.asyncio
    async def test_non_json_ignored(self):
        client, utterances, _ = make_client()
        await client._handle_message("not json!!!")
        assert utterances == []

    @pytest.mark.asyncio
    async def test_binary_bytes_decoded(self):
        client, utterances, _ = make_client()
        msg_bytes = _results_msg("binary test").encode("utf-8")
        await client._handle_message(msg_bytes)
        assert utterances[0][1] == "binary test"

    @pytest.mark.asyncio
    async def test_metadata_event_ignored(self):
        client, utterances, _ = make_client()
        await client._handle_message(json.dumps({"type": "Metadata", "transaction_key": "abc"}))
        assert utterances == []

    @pytest.mark.asyncio
    async def test_speech_started_ignored(self):
        client, utterances, _ = make_client()
        await client._handle_message(json.dumps({"type": "SpeechStarted"}))
        assert utterances == []

    @pytest.mark.asyncio
    async def test_utterance_end_ignored(self):
        client, utterances, _ = make_client()
        await client._handle_message(json.dumps({"type": "UtteranceEnd"}))
        assert utterances == []

    @pytest.mark.asyncio
    async def test_error_event_does_not_raise(self):
        client, utterances, _ = make_client()
        await client._handle_message(
            json.dumps({"type": "Error", "message": "auth failed"})
        )
        # No exception, no utterances
        assert utterances == []

    @pytest.mark.asyncio
    async def test_unknown_type_ignored(self):
        client, utterances, _ = make_client()
        await client._handle_message(json.dumps({"type": "Surprise"}))
        assert utterances == []


# ---------------------------------------------------------------------------
# _handle_results — callback values
# ---------------------------------------------------------------------------

class TestHandleResults:
    @pytest.mark.asyncio
    async def test_is_final_true_propagated(self):
        client, utterances, _ = make_client()
        await client._handle_message(_results_msg("hello", is_final=True))
        assert utterances[0][2] is True

    @pytest.mark.asyncio
    async def test_is_final_false_propagated(self):
        client, utterances, _ = make_client()
        await client._handle_message(_results_msg("hello", is_final=False))
        assert utterances[0][2] is False

    @pytest.mark.asyncio
    async def test_start_time_propagated(self):
        client, utterances, _ = make_client()
        await client._handle_message(_results_msg("hello", start=5.25, duration=1.0))
        assert utterances[0][3] == pytest.approx(5.25)

    @pytest.mark.asyncio
    async def test_end_time_is_start_plus_duration(self):
        client, utterances, _ = make_client()
        await client._handle_message(_results_msg("hello", start=3.0, duration=2.5))
        assert utterances[0][4] == pytest.approx(5.5)

    @pytest.mark.asyncio
    async def test_speaker_extracted_from_words(self):
        client, utterances, _ = make_client()
        words = _words(3, "hello", "world")
        await client._handle_message(_results_msg("hello world", words=words))
        assert utterances[0][0] == "speaker_3"

    @pytest.mark.asyncio
    async def test_no_words_defaults_to_speaker_0(self):
        client, utterances, _ = make_client()
        await client._handle_message(_results_msg("hello", words=[]))
        assert utterances[0][0] == "speaker_0"

    @pytest.mark.asyncio
    async def test_transcript_is_stripped(self):
        client, utterances, _ = make_client()
        await client._handle_message(_results_msg("  hello world  "))
        assert utterances[0][1] == "hello world"

    @pytest.mark.asyncio
    async def test_no_alternatives_skipped(self):
        client, utterances, _ = make_client()
        msg = json.dumps({
            "type": "Results",
            "is_final": True,
            "start": 0.0,
            "duration": 1.0,
            "channel": {"alternatives": []},
        })
        await client._handle_message(msg)
        assert utterances == []


# ---------------------------------------------------------------------------
# connect / disconnect lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_connect_sets_connected(self):
        client, _, _ = make_client()
        assert not client.is_connected
        await client.connect()
        assert client.is_connected
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_clears_connected(self):
        client, _, _ = make_client()
        await client.connect()
        await client.disconnect()
        assert not client.is_connected

    @pytest.mark.asyncio
    async def test_double_connect_is_idempotent(self):
        client, _, ws = make_client()
        await client.connect()
        await client.connect()   # second call — must not crash or re-open
        assert client.is_connected
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_double_disconnect_is_safe(self):
        client, _, _ = make_client()
        await client.connect()
        await client.disconnect()
        await client.disconnect()  # must not raise

    @pytest.mark.asyncio
    async def test_disconnect_closes_ws(self):
        client, _, ws = make_client()
        await client.connect()
        await client.disconnect()
        assert ws.closed


# ---------------------------------------------------------------------------
# send_audio
# ---------------------------------------------------------------------------

class TestSendAudio:
    @pytest.mark.asyncio
    async def test_sends_bytes_to_websocket(self):
        client, _, ws = make_client()
        await client.connect()

        audio = b"\x00\x01\x02\x03"
        await client.send_audio(audio)

        # Give the send loop a chance to drain
        await asyncio.sleep(0.05)

        assert audio in ws.sent
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_empty_bytes_not_sent(self):
        client, _, ws = make_client()
        await client.connect()
        await client.send_audio(b"")
        await asyncio.sleep(0.05)
        assert ws.sent == []
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_send_when_not_connected_is_silent(self):
        client, _, ws = make_client()
        # Don't call connect()
        await client.send_audio(b"\xff\xfe")   # must not raise
        assert ws.sent == []


# ---------------------------------------------------------------------------
# recv loop — single message round-trip
# ---------------------------------------------------------------------------

class TestRecvLoop:
    @pytest.mark.asyncio
    async def test_utterance_received_end_to_end(self):
        """Full round-trip: connect → recv Results → callback fires."""
        messages = [_results_msg("end to end", is_final=True)]
        # After the real message, raise to terminate the loop cleanly
        messages.append(ConnectionError("end of stream"))

        client, utterances, _ = make_client(messages, max_reconnects=0)
        await client.connect()
        await asyncio.sleep(0.1)
        await client.disconnect()

        assert any(u[1] == "end to end" for u in utterances)

    @pytest.mark.asyncio
    async def test_multiple_utterances_in_order(self):
        messages = [
            _results_msg("first"),
            _results_msg("second"),
            _results_msg("third"),
            ConnectionError("done"),
        ]
        client, utterances, _ = make_client(messages, max_reconnects=0)
        await client.connect()
        await asyncio.sleep(0.15)
        await client.disconnect()

        texts = [u[1] for u in utterances]
        assert texts == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_interim_and_final_both_fire(self):
        messages = [
            _results_msg("interim", is_final=False),
            _results_msg("final", is_final=True),
            ConnectionError("done"),
        ]
        client, utterances, _ = make_client(messages, max_reconnects=0)
        await client.connect()
        await asyncio.sleep(0.1)
        await client.disconnect()

        assert utterances[0][2] is False   # interim
        assert utterances[1][2] is True    # final


# ---------------------------------------------------------------------------
# reconnect
# ---------------------------------------------------------------------------

class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_after_single_error(self):
        """
        Recv error on the first message → reconnect → second message delivered.
        """
        reconnect_ws_calls = []

        async def connect_fn(url, *, extra_headers=None, **_):
            call_index = len(reconnect_ws_calls)
            if call_index == 0:
                ws = FakeWS([ConnectionError("first failure")])
            else:
                ws = FakeWS([_results_msg("after reconnect"), ConnectionError("done")])
            reconnect_ws_calls.append(ws)
            return ws

        utterances = []

        async def on_utterance(speaker_id, text, is_final, start_s, end_s):
            utterances.append(text)

        client = DeepgramTranscriber(
            api_key="test",
            on_utterance=on_utterance,
            max_reconnects=3,
            reconnect_delay_s=0.0,
            _connect_fn=connect_fn,
        )

        await client.connect()
        await asyncio.sleep(0.15)
        await client.disconnect()

        assert "after reconnect" in utterances

    @pytest.mark.asyncio
    async def test_on_error_called_after_max_reconnects(self):
        """After exhausting reconnects, on_error receives the last exception."""
        errors: list[Exception] = []

        async def connect_fn(url, *, extra_headers=None, **_):
            return FakeWS([ConnectionError("always fails")])

        async def on_utterance(*args):
            pass

        async def on_error(exc: Exception):
            errors.append(exc)

        client = DeepgramTranscriber(
            api_key="test",
            on_utterance=on_utterance,
            on_error=on_error,
            max_reconnects=2,
            reconnect_delay_s=0.0,
            _connect_fn=connect_fn,
        )

        await client.connect()
        await asyncio.sleep(0.2)
        await client.disconnect()

        assert len(errors) == 1
        assert isinstance(errors[0], ConnectionError)

    @pytest.mark.asyncio
    async def test_no_on_error_callback_does_not_raise(self):
        """Exhausting reconnects without on_error set must not crash."""
        async def connect_fn(url, *, extra_headers=None, **_):
            return FakeWS([ConnectionError("always")])

        async def on_utterance(*args):
            pass

        client = DeepgramTranscriber(
            api_key="test",
            on_utterance=on_utterance,
            on_error=None,
            max_reconnects=1,
            reconnect_delay_s=0.0,
            _connect_fn=connect_fn,
        )

        await client.connect()
        await asyncio.sleep(0.15)
        await client.disconnect()  # must not raise


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------

class TestBuildUrl:
    def test_url_contains_deepgram_endpoint(self):
        client, _, _ = make_client()
        url = client._build_url()
        assert url.startswith("wss://api.deepgram.com/v1/listen")

    def test_url_includes_diarize(self):
        client, _, _ = make_client()
        assert "diarize=true" in client._build_url()

    def test_url_includes_interim_results(self):
        client, _, _ = make_client()
        assert "interim_results=true" in client._build_url()

    def test_custom_sample_rate_in_url(self):
        async def connect_fn(url, *, extra_headers=None, **_):
            return FakeWS()

        async def on_utterance(*a):
            pass

        client = DeepgramTranscriber(
            api_key="k",
            on_utterance=on_utterance,
            sample_rate=48_000,
            _connect_fn=connect_fn,
        )
        assert "sample_rate=48000" in client._build_url()
