"""TCP audio reader — Swift capture client (via AudioTcpServer) → PCM callback.

Architecture
────────────
  Swift binary (ScreenCaptureKit + POSIX socket)
         │ TCP 127.0.0.1:$AUDIO_TCP_PORT  (2-byte handshake, then raw PCM)
         ▼
  AudioTcpServer                (backend/audio_tcp_server.py)
         │ asyncio.Queue[bytes] (per stream tag)
         ▼
  AudioTcpReader                (this module)
         │
         ├── on_audio_chunk(bytes)  ──► HybridTranscriber.send_audio()
         │
         └── on_silence_timeout()  ──► Electron restart signal
                                        (TCP disconnected / Swift crashed)

Silence detection
─────────────────
- Fires ``on_silence_timeout`` when no non-empty chunk arrives for
  ``silence_timeout_s`` (default 5.0 s) after the first chunk.
- The timeout fires at most once per ``start()``. It resets when audio
  resumes.

Thread safety
─────────────
All public methods are async and safe to call from the event loop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from backend.audio_tcp_server import AudioTcpServer, STREAM_TAG_SYSTEM

logger = logging.getLogger(__name__)

_DEFAULT_SILENCE_TIMEOUT_S = 5.0

AudioCallback = Callable[[bytes], Awaitable[None]]
SilenceCallback = Callable[[], Awaitable[None]]


class AudioTcpReader:
    """Async reader that drains one AudioTcpServer stream into a callback."""

    def __init__(
        self,
        *,
        server: AudioTcpServer,
        stream_tag: int = STREAM_TAG_SYSTEM,
        on_audio_chunk: AudioCallback,
        on_silence_timeout: SilenceCallback | None = None,
        silence_timeout_s: float = _DEFAULT_SILENCE_TIMEOUT_S,
    ) -> None:
        self._server = server
        self._stream_tag = stream_tag
        self._on_audio_chunk = on_audio_chunk
        self._on_silence_timeout = on_silence_timeout
        self._silence_timeout = silence_timeout_s

        self._running = False
        self._queue: asyncio.Queue[bytes] | None = None
        self._read_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None

        self._last_audio_time: float = 0.0
        self._silence_fired: bool = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_audio_time(self) -> float:
        return self._last_audio_time

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._last_audio_time = 0.0
        self._silence_fired = False
        self._queue = self._server.register(self._stream_tag)
        self._read_task = asyncio.ensure_future(self._read_loop())
        self._watchdog_task = asyncio.ensure_future(self._watchdog_loop())
        logger.info("AudioTcpReader started (tag=%d)", self._stream_tag)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in (self._read_task, self._watchdog_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._read_task = None
        self._watchdog_task = None
        self._queue = None
        self._server.unregister(self._stream_tag)
        logger.info("AudioTcpReader stopped (tag=%d)", self._stream_tag)

    async def _read_loop(self) -> None:
        assert self._queue is not None
        try:
            while self._running:
                chunk = await self._queue.get()
                if not chunk:
                    continue
                self._last_audio_time = time.monotonic()
                self._silence_fired = False
                try:
                    await self._on_audio_chunk(chunk)
                except Exception:
                    logger.exception("AudioTcpReader: on_audio_chunk raised")
        except asyncio.CancelledError:
            return

    async def _watchdog_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._silence_timeout / 4)
                if self._last_audio_time == 0.0:
                    continue
                if self._silence_fired:
                    continue
                elapsed = time.monotonic() - self._last_audio_time
                if elapsed >= self._silence_timeout and self._on_silence_timeout:
                    self._silence_fired = True
                    try:
                        await self._on_silence_timeout()
                    except Exception:
                        logger.exception(
                            "AudioTcpReader: on_silence_timeout raised"
                        )
        except asyncio.CancelledError:
            return
