"""
Deepgram streaming transcription client.

Architecture
────────────
                    bytes
  audio.py ──────────────────► DeepgramTranscriber
                                      │
                           WebSocket (wss://api.deepgram.com)
                                      │
                              JSON transcript events
                                      │
                              on_utterance callback
                                      │
                   ┌──────────────────▼──────────────────┐
                   │  {speaker_id, text, is_final,        │
                   │   start_s, end_s}                    │
                   └──────────────────────────────────────┘
                                      │
                              SessionPipeline (main.py)

Deepgram wire format (simplified)
──────────────────────────────────
Inbound (audio bytes) → binary frames on the WebSocket
Outbound (JSON)       → {"type": "Results", "is_final": bool,
                          "channel": {"alternatives": [{"transcript": str,
                                                        "words": [...]}]},
                          "start": float, "duration": float}

Speaker diarization
───────────────────
When diarize=True, each word carries a "speaker" integer.
We map the integer to a stable string ID "speaker_N".

Reconnect behaviour
───────────────────
On any connection error or unexpected close, the client sleeps for
reconnect_delay_s (default 1.0 s) and re-opens the WebSocket, resending
the configure message.  Up to max_reconnects attempts are made; after that
on_error (if provided) is called and the loop exits.

Usage
─────
    async def handle(speaker_id, text, is_final, start_s, end_s):
        ...

    client = DeepgramTranscriber(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        on_utterance=handle,
    )
    await client.connect()
    # feed audio
    await client.send_audio(pcm_bytes)
    # ...
    await client.disconnect()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

UtteranceCallback = Callable[
    [str, str, bool, float, float],   # speaker_id, text, is_final, start_s, end_s
    Awaitable[None],
]

ErrorCallback = Callable[[Exception], Awaitable[None]]

# ---------------------------------------------------------------------------
# Deepgram endpoint
# ---------------------------------------------------------------------------

_DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"

# Query-string params sent on every connection
_DEFAULT_PARAMS: dict[str, str] = {
    "encoding": "linear16",
    "sample_rate": "16000",
    "channels": "1",
    "language": "en-US",
    "model": "nova-2",
    "diarize": "true",
    "punctuate": "true",
    "smart_format": "true",
    "interim_results": "true",
    "utterance_end_ms": "1000",
    "endpointing": "200",
    "vad_events": "true",
    "no_delay": "true",
}

# ---------------------------------------------------------------------------
# DeepgramTranscriber
# ---------------------------------------------------------------------------


class DeepgramTranscriber:
    """
    Async Deepgram streaming client.

    Parameters
    ----------
    api_key:
        Deepgram API key. Defaults to DEEPGRAM_API_KEY env var.
    on_utterance:
        Async callback invoked for every recognised utterance (final or interim).
        Signature: ``async def cb(speaker_id, text, is_final, start_s, end_s)``
    on_error:
        Optional async callback invoked when the client exhausts reconnect
        attempts.  Receives the last Exception.
    sample_rate:
        PCM sample rate in Hz (default 16 000).
    diarize:
        Enable Deepgram speaker diarization (default True). Set to False for
        single-speaker streams (e.g., microphone-only) to reduce cost.
    reconnect_delay_s:
        Seconds to wait between reconnect attempts (default 1.0).
    max_reconnects:
        Maximum consecutive reconnect attempts before giving up (default 5).
    _connect_fn:
        Injectable WebSocket factory for testing.
        Signature: ``async def factory(url, extra_headers) -> websocket``.
        Defaults to ``websockets.connect``.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        on_utterance: UtteranceCallback,
        on_error: ErrorCallback | None = None,
        sample_rate: int = 16_000,
        diarize: bool = True,
        reconnect_delay_s: float = 1.0,
        max_reconnects: int = 5,
        _connect_fn: Callable | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("DEEPGRAM_API_KEY", "")
        self._on_utterance = on_utterance
        self._on_error = on_error
        self._sample_rate = sample_rate
        self._diarize = diarize
        self._reconnect_delay = reconnect_delay_s
        self._max_reconnects = max_reconnects
        self._connect_fn = _connect_fn

        self._ws = None          # active websocket connection
        self._connected = False
        self._send_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        self._send_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket connection and start the send/receive loops."""
        if self._connected:
            return
        await self._open_connection()
        self._connected = True
        self._recv_task = asyncio.ensure_future(self._recv_loop())
        self._send_task = asyncio.ensure_future(self._send_loop())
        self._keepalive_task = asyncio.ensure_future(self._keepalive_loop())

    async def send_audio(self, data: bytes) -> None:
        """
        Enqueue a chunk of raw PCM audio for delivery to Deepgram.

        Thread-safe via asyncio.Queue.  Silently dropped if not connected.
        """
        if self._connected and data:
            await self._send_queue.put(data)

    async def disconnect(self) -> None:
        """
        Gracefully close the WebSocket and cancel background tasks.

        Sends a CloseStream message to flush Deepgram's server-side buffer
        before closing the connection. Safe to call multiple times.
        """
        if not self._connected:
            return
        self._connected = False

        # Cancel KeepAlive first
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except (asyncio.CancelledError, Exception):
                pass
            self._keepalive_task = None

        # Send CloseStream to flush Deepgram's server-side audio buffer
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                logger.info("Deepgram: sent CloseStream")
                # Give Deepgram a moment to flush any final results
                await asyncio.sleep(0.5)
            except Exception:
                pass

        # Signal the send loop to drain and stop
        await self._send_queue.put(None)

        if self._send_task and not self._send_task.done():
            try:
                await asyncio.wait_for(self._send_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._send_task.cancel()

        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass

        await self._close_ws()

    async def finalize(self) -> None:
        """Send a Finalize message to flush Deepgram's server-side audio buffer.

        Use this when you want to force Deepgram to process all buffered audio
        and return results immediately, without closing the connection.
        """
        if self._ws is not None and self._connected:
            try:
                await self._ws.send(json.dumps({"type": "Finalize"}))
                logger.debug("Deepgram: sent Finalize")
            except Exception as exc:
                logger.warning("Deepgram Finalize send error: %s", exc)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _build_url(self) -> str:
        params = dict(_DEFAULT_PARAMS)
        params["sample_rate"] = str(self._sample_rate)
        params["diarize"] = "true" if self._diarize else "false"
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{_DEEPGRAM_URL}?{qs}"

    async def _open_connection(self) -> None:
        url = self._build_url()
        headers = {"Authorization": f"Token {self._api_key}"}

        connect_fn = self._connect_fn
        if connect_fn is None:
            import websockets
            connect_fn = websockets.connect

        self._ws = await connect_fn(url, additional_headers=headers)
        logger.info("Deepgram WebSocket connected")

    async def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _keepalive_loop(self) -> None:
        """Send KeepAlive messages every 5 seconds to prevent Deepgram timeout."""
        while self._connected:
            await asyncio.sleep(5.0)
            if not self._connected or self._ws is None:
                break
            try:
                await self._ws.send(json.dumps({"type": "KeepAlive"}))
                logger.debug("Deepgram: sent KeepAlive")
            except Exception as exc:
                logger.warning("Deepgram KeepAlive send error: %s", exc)
                break

    async def _send_loop(self) -> None:
        """Drain the send queue and forward audio bytes to Deepgram.

        No rate limiting — the AudioMixer already outputs at exactly 1.0x
        realtime (32 KB/s). Adding a rate limiter here causes queue backup
        and massive latency.
        """
        while True:
            chunk = await self._send_queue.get()
            if chunk is None:
                break
            if self._ws is not None:
                # If queue is backing up, skip old audio to stay real-time
                if self._send_queue.qsize() > 50:
                    skipped = 0
                    while self._send_queue.qsize() > 5:
                        try:
                            self._send_queue.get_nowait()
                            skipped += 1
                        except asyncio.QueueEmpty:
                            break
                    if skipped:
                        logger.warning("Deepgram: skipped %d stale chunks to stay real-time", skipped)
                try:
                    await self._ws.send(chunk)
                except Exception as exc:
                    logger.warning("Deepgram send error: %s", exc)

    async def _recv_loop(self) -> None:
        """
        Receive JSON events from Deepgram and invoke on_utterance.

        Handles unexpected disconnect with exponential-backoff reconnect.
        """
        consecutive_failures = 0

        while self._connected:
            try:
                raw = await self._ws.recv()
            except Exception as exc:
                if not self._connected:
                    break  # intentional disconnect
                logger.warning("Deepgram recv error: %s", exc)
                consecutive_failures += 1
                if consecutive_failures > self._max_reconnects:
                    logger.error(
                        "Exhausted %d reconnect attempts — giving up",
                        self._max_reconnects,
                    )
                    if self._on_error:
                        await self._on_error(exc)
                    self._connected = False
                    break

                await asyncio.sleep(self._reconnect_delay)
                try:
                    await self._close_ws()
                    await self._open_connection()
                    logger.info("Deepgram reconnected (attempt %d)", consecutive_failures)
                except Exception as reconnect_exc:
                    logger.warning("Reconnect failed: %s", reconnect_exc)
                continue

            # Successful recv — reset failure streak
            consecutive_failures = 0
            await self._handle_message(raw)

    async def _handle_message(self, raw: str | bytes) -> None:
        """Parse one JSON message from Deepgram and fire the callback."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Non-JSON message from Deepgram: %r", raw[:120])
            return

        msg_type = msg.get("type", "")

        if msg_type == "Results":
            await self._handle_results(msg)
        elif msg_type in ("Metadata", "SpeechStarted", "UtteranceEnd"):
            logger.debug("Deepgram %s event", msg_type)
        elif msg_type == "Error":
            logger.error("Deepgram error event: %s", msg.get("message"))
        else:
            logger.debug("Unhandled Deepgram message type: %r", msg_type)

    async def _handle_results(self, msg: dict) -> None:
        """
        Extract speaker / text / timing from a Deepgram Results event.

        Deepgram results structure:
          {
            "type": "Results",
            "is_final": bool,
            "start": float,           # seconds from session start
            "duration": float,        # seconds
            "channel": {
              "alternatives": [
                {
                  "transcript": str,
                  "confidence": float,
                  "words": [
                    {"word": str, "start": float, "end": float,
                     "confidence": float, "speaker": int, "punctuated_word": str}
                  ]
                }
              ]
            }
          }
        """
        is_final: bool = msg.get("is_final", False)
        start_s: float = float(msg.get("start", 0.0))
        duration: float = float(msg.get("duration", 0.0))
        end_s: float = start_s + duration

        channel = msg.get("channel", {})
        alternatives = channel.get("alternatives", [])
        if not alternatives:
            return

        best = alternatives[0]
        transcript: str = best.get("transcript", "").strip()
        if not transcript:
            return

        # Determine speaker from diarized words (majority-vote among first alt words)
        speaker_id = _speaker_from_words(best.get("words", []))

        await self._on_utterance(speaker_id, transcript, is_final, start_s, end_s)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _speaker_from_words(words: list[dict]) -> str:
    """
    Determine the dominant speaker for an utterance.

    Deepgram assigns a ``speaker`` integer to each word when diarize=True.
    We return the speaker with the most words; ties go to the first speaker.
    Falls back to ``"speaker_0"`` when no speaker data is present.
    """
    if not words:
        return "speaker_0"

    counts: dict[int, int] = {}
    for w in words:
        s = w.get("speaker")
        if s is not None:
            counts[s] = counts.get(s, 0) + 1

    if not counts:
        return "speaker_0"

    dominant = max(counts, key=lambda k: (counts[k], -k))
    return f"speaker_{dominant}"
