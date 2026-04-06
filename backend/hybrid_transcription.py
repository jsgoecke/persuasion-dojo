"""
Hybrid transcription orchestrator — Deepgram primary, Moonshine fallback.

Modes
─────
  "cloud"  → Deepgram only, fail hard on error
  "local"  → Moonshine only, no cloud dependency
  "auto"   → Try Deepgram (with health check), failover to Moonshine on failure.
              Once switched to Moonshine, stay local for session remainder.

Failover triggers
─────────────────
  1. Health check fails before session (auto mode) → start on Moonshine
  2. Deepgram connect() fails → switch to Moonshine
  3. Deepgram exhausts reconnects mid-session (on_error fires) → switch to Moonshine

The ring buffer in DeepgramTranscriber stores ~5s of recent audio. On mid-session
failover, we replay this buffer through Moonshine so the user doesn't lose context.
"""

from __future__ import annotations

import logging
from typing import Literal

from backend.moonshine_transcription import MoonshineTranscriber
from backend.transcriber_protocol import StatusCallback, UtteranceCallback
from backend.transcription import DeepgramTranscriber, deepgram_health_check

logger = logging.getLogger(__name__)

TranscriptionMode = Literal["cloud", "local", "auto"]


class HybridTranscriber:
    """
    Wraps Deepgram (primary) and Moonshine (fallback).
    Conforms to the Transcriber protocol.
    """

    def __init__(
        self,
        *,
        mode: TranscriptionMode = "auto",
        deepgram_api_key: str = "",
        on_utterance: UtteranceCallback,
        on_status: StatusCallback | None = None,
        diarize: bool = True,
        sample_rate: int = 16_000,
        # Test injection
        _deepgram_factory=None,
        _moonshine_factory=None,
    ) -> None:
        self._mode = mode
        self._deepgram_api_key = deepgram_api_key
        self._on_utterance = on_utterance
        self._on_status = on_status
        self._diarize = diarize
        self._sample_rate = sample_rate
        self._deepgram_factory = _deepgram_factory
        self._moonshine_factory = _moonshine_factory

        self._deepgram: DeepgramTranscriber | None = None
        self._moonshine: MoonshineTranscriber | None = None
        self._active: DeepgramTranscriber | MoonshineTranscriber | None = None
        self._active_backend: str = ""
        self._connected = False

    # ------------------------------------------------------------------
    # Public API (Transcriber protocol)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """
        Connect the appropriate transcriber based on mode.

        auto: health check Deepgram → connect Deepgram or fallback to Moonshine.
        cloud: connect Deepgram only.
        local: connect Moonshine only.
        """
        if self._connected:
            return

        if self._mode == "local":
            await self._connect_moonshine()
        elif self._mode == "cloud":
            await self._connect_deepgram()
        else:
            # auto mode: try Deepgram, fallback to Moonshine
            if not self._deepgram_api_key:
                logger.info("No Deepgram API key — using Moonshine")
                await self._connect_moonshine()
                await self._emit_status("fallback_activated", {
                    "reason": "no_api_key",
                })
            else:
                ok, reason = await deepgram_health_check(self._deepgram_api_key)
                if ok:
                    try:
                        await self._connect_deepgram()
                        return
                    except Exception as exc:
                        logger.warning(
                            "Deepgram connect failed after health check: %s", exc
                        )
                        await self._emit_status("fallback_activated", {
                            "reason": f"connect_failed: {exc}",
                        })
                        await self._connect_moonshine()
                else:
                    logger.warning("Deepgram health check failed: %s", reason)
                    await self._emit_status("fallback_activated", {
                        "reason": f"health_check: {reason}",
                    })
                    await self._connect_moonshine()

    async def send_audio(self, data: bytes) -> None:
        """Forward audio to the active transcriber."""
        if self._active is not None and self._connected:
            await self._active.send_audio(data)

    async def disconnect(self) -> None:
        """Disconnect whichever transcriber is active."""
        if not self._connected:
            return
        self._connected = False
        if self._active is not None:
            await self._active.disconnect()
        self._active = None
        self._active_backend = ""

    async def finalize(self) -> None:
        """Flush buffered audio on the active transcriber."""
        if self._active is not None:
            await self._active.finalize()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def active_backend(self) -> str:
        """'deepgram', 'moonshine', or '' if not connected."""
        return self._active_backend

    # ------------------------------------------------------------------
    # Internal: connect helpers
    # ------------------------------------------------------------------

    async def _connect_deepgram(self) -> None:
        """Create and connect a DeepgramTranscriber."""
        if self._deepgram_factory is not None:
            self._deepgram = self._deepgram_factory()
        else:
            self._deepgram = DeepgramTranscriber(
                api_key=self._deepgram_api_key,
                on_utterance=self._on_utterance,
                on_error=self._on_deepgram_error,
                on_status=self._on_status,
                sample_rate=self._sample_rate,
                diarize=self._diarize,
            )
        await self._deepgram.connect()
        self._active = self._deepgram
        self._active_backend = "deepgram"
        self._connected = True
        await self._emit_status("using_cloud", {})
        logger.info("HybridTranscriber: using Deepgram")

    async def _connect_moonshine(self) -> None:
        """Create and connect a MoonshineTranscriber."""
        if self._moonshine_factory is not None:
            self._moonshine = self._moonshine_factory()
        else:
            self._moonshine = MoonshineTranscriber(
                on_utterance=self._on_utterance,
                on_status=self._on_status,
                diarize=self._diarize,
            )
        await self._moonshine.connect()
        self._active = self._moonshine
        self._active_backend = "moonshine"
        self._connected = True
        await self._emit_status("using_local", {})
        logger.info("HybridTranscriber: using Moonshine")

    # ------------------------------------------------------------------
    # Mid-session failover
    # ------------------------------------------------------------------

    async def _on_deepgram_error(self, exc: Exception) -> None:
        """
        Called when Deepgram exhausts reconnect attempts.

        Switches to Moonshine and replays the ring buffer.
        """
        logger.warning("Deepgram exhausted — failing over to Moonshine: %s", exc)
        await self._emit_status("fallback_activated", {
            "reason": f"mid_session: {exc}",
        })

        # Capture ring buffer before disconnecting Deepgram
        ring_buffer = list(self._deepgram._ring_buffer) if self._deepgram else []

        # Disconnect Deepgram
        if self._deepgram is not None:
            try:
                self._deepgram._connected = False  # prevent double-close issues
            except Exception:
                pass

        # Connect Moonshine
        try:
            await self._connect_moonshine()
        except Exception as moon_exc:
            logger.error("Moonshine fallback also failed: %s", moon_exc)
            self._connected = False
            return

        # Replay ring buffer through Moonshine
        for chunk in ring_buffer:
            await self._moonshine.send_audio(chunk)

        logger.info(
            "HybridTranscriber: failover complete, replayed %d chunks",
            len(ring_buffer),
        )

    # ------------------------------------------------------------------
    # Status helper
    # ------------------------------------------------------------------

    async def _emit_status(self, event: str, detail: dict) -> None:
        """Fire the on_status callback if registered."""
        if self._on_status is not None:
            try:
                await self._on_status(event, detail)
            except Exception:
                pass
