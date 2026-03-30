"""
Named-pipe audio reader — Swift ScreenCaptureKit → Python PCM stream.

Architecture
────────────
  Swift binary (ScreenCaptureKit)
         │
         │  raw PCM (16-bit LE, mono, 16 kHz)
         ▼
  /tmp/persuasion_audio.pipe  (OS named pipe / FIFO)
         │
         │  reads in executor thread (non-blocking event loop)
         ▼
  AudioPipeReader
         │
         ├── on_audio_chunk(bytes)  ──► DeepgramTranscriber.send_audio()
         │
         └── on_silence_timeout()  ──► Electron restart signal
                                       (pipe dead / Swift binary crashed)

Named-pipe behaviour
────────────────────
- A FIFO blocks on open() until both a reader and a writer are present.
- When the Swift binary stops writing (crash, restart), the read end receives
  EOF. AudioPipeReader re-opens the FIFO, which blocks until Swift reconnects.
- Each re-open attempt is separated by reopen_delay_s (default 0.5 s) to
  avoid a busy-loop if the pipe file is not yet recreated.

Silence detection
─────────────────
- A background task fires on_silence_timeout() when no audio has arrived for
  silence_timeout_s (default 5.0 s) after the first chunk.
- "Silence" means the pipe is open but delivering only zero-byte reads, OR the
  pipe is closed waiting to reopen. Either way, the Electron overlay should
  restart the Swift binary.
- The timeout fires at most once per connection open. It resets when audio
  resumes (pipe reconnects and delivers real bytes).

Thread safety
─────────────
All public methods are async and safe to call from the event loop.
Blocking I/O (open + read) runs in asyncio's default thread-pool executor.

Usage
─────
    reader = AudioPipeReader(
        on_audio_chunk=transcriber.send_audio,
        on_silence_timeout=handle_silence,
    )
    await reader.start()
    # … runs until stopped
    await reader.stop()
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_PIPE_PATH = "/tmp/persuasion_audio.pipe"
_DEFAULT_CHUNK_SIZE = 1600          # bytes (50 ms of 16-bit mono 16 kHz PCM)
_DEFAULT_SILENCE_TIMEOUT_S = 5.0   # seconds
_DEFAULT_REOPEN_DELAY_S = 0.5      # seconds between FIFO re-open attempts

AudioCallback = Callable[[bytes], Awaitable[None]]
SilenceCallback = Callable[[], Awaitable[None]]


# ---------------------------------------------------------------------------
# AudioPipeReader
# ---------------------------------------------------------------------------

class AudioPipeReader:
    """
    Async reader for a POSIX named pipe carrying raw PCM audio.

    Parameters
    ----------
    on_audio_chunk:
        Async callback invoked with each raw PCM chunk.
    on_silence_timeout:
        Optional async callback invoked when no audio is received for
        ``silence_timeout_s`` seconds after the first chunk.
    pipe_path:
        Filesystem path of the named pipe (default ``/tmp/persuasion_audio.pipe``).
    chunk_size:
        Bytes to read per iteration (default 4096).
    silence_timeout_s:
        Seconds of silence before ``on_silence_timeout`` fires (default 5.0).
    reopen_delay_s:
        Seconds to wait between FIFO re-open attempts on EOF (default 0.5).
    """

    def __init__(
        self,
        *,
        on_audio_chunk: AudioCallback,
        on_silence_timeout: SilenceCallback | None = None,
        pipe_path: str = _DEFAULT_PIPE_PATH,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        silence_timeout_s: float = _DEFAULT_SILENCE_TIMEOUT_S,
        reopen_delay_s: float = _DEFAULT_REOPEN_DELAY_S,
    ) -> None:
        self._on_audio_chunk = on_audio_chunk
        self._on_silence_timeout = on_silence_timeout
        self._pipe_path = pipe_path
        self._chunk_size = chunk_size
        self._silence_timeout = silence_timeout_s
        self._reopen_delay = reopen_delay_s

        self._running = False
        self._read_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None

        # Monotonic timestamp of the last received non-empty chunk.
        # 0.0 means "no chunk received yet" (watchdog does not fire before
        # the first chunk arrives).
        self._last_audio_time: float = 0.0
        # True after on_silence_timeout has fired for the current session
        # (prevents repeated firing while pipe is still dead).
        self._silence_fired: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Begin reading from the pipe. Returns immediately; runs in background."""
        if self._running:
            return
        self._running = True
        self._last_audio_time = 0.0
        self._silence_fired = False
        self._ensure_pipe()
        self._read_task = asyncio.ensure_future(self._read_loop())
        self._watchdog_task = asyncio.ensure_future(self._watchdog_loop())
        logger.info("AudioPipeReader started (%s)", self._pipe_path)

    async def stop(self) -> None:
        """Stop reading, cancel background tasks, and clean up the pipe file.

        Safe to call multiple times.
        """
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

        # Remove the pipe file so stale AudioCapture writers get a broken pipe
        # signal and the next session starts with a fresh FIFO.
        self._cleanup_pipe()
        logger.info("AudioPipeReader stopped")

    def _cleanup_pipe(self) -> None:
        """Remove the named pipe file if it exists."""
        try:
            if os.path.exists(self._pipe_path):
                os.unlink(self._pipe_path)
                logger.info("AudioPipeReader: removed pipe %s", self._pipe_path)
        except OSError as exc:
            logger.warning("AudioPipeReader: could not remove pipe — %s", exc)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_audio_time(self) -> float:
        """Monotonic timestamp of last received chunk (0.0 = none yet)."""
        return self._last_audio_time

    # ------------------------------------------------------------------
    # Read loop
    # ------------------------------------------------------------------

    def _ensure_pipe(self) -> None:
        """Create the named pipe if it doesn't already exist."""
        if not os.path.exists(self._pipe_path):
            try:
                os.mkfifo(self._pipe_path)
                logger.info("AudioPipeReader: created FIFO at %s", self._pipe_path)
            except OSError as exc:
                logger.warning("AudioPipeReader: could not create FIFO — %s", exc)

    async def _read_loop(self) -> None:
        """
        Continuously open the FIFO, read chunks, and call on_audio_chunk.

        Opens the FIFO with O_NONBLOCK to prevent deadlocking when the writer
        hasn't connected yet. Polls until data arrives, then switches to
        blocking reads for efficiency.

        On EOF (writer closed), waits reopen_delay_s and re-opens.
        """
        loop = asyncio.get_event_loop()
        consecutive_open_failures = 0

        while self._running:
            # Ensure the FIFO exists before trying to open it.
            self._ensure_pipe()

            # Open the FIFO non-blocking so we never deadlock waiting for a writer.
            try:
                fd = os.open(self._pipe_path, os.O_RDONLY | os.O_NONBLOCK)
                consecutive_open_failures = 0
            except (FileNotFoundError, OSError) as exc:
                consecutive_open_failures += 1
                if self._running:
                    delay = min(self._reopen_delay * (2 ** (consecutive_open_failures - 1)), 5.0)
                    if consecutive_open_failures <= 3:
                        logger.warning(
                            "AudioPipeReader: cannot open %s — %s (retry in %.1fs)",
                            self._pipe_path, exc, delay,
                        )
                    elif consecutive_open_failures == 4:
                        logger.warning(
                            "AudioPipeReader: still waiting for audio source — suppressing further warnings",
                        )
                    await asyncio.sleep(delay)
                continue

            logger.info("AudioPipeReader: FIFO opened (non-blocking)")

            # Poll until writer connects and data starts flowing, then switch
            # to blocking reads for efficiency.
            writer_connected = False
            try:
                while self._running:
                    if not writer_connected:
                        # Non-blocking read: EAGAIN = no writer yet, empty = EOF
                        try:
                            chunk = os.read(fd, self._chunk_size)
                        except BlockingIOError:
                            # No writer connected yet — wait and retry
                            await asyncio.sleep(0.1)
                            continue

                        if not chunk:
                            # EOF before writer connected — FIFO was opened then
                            # immediately saw no writer. Retry.
                            logger.debug("AudioPipeReader: FIFO EOF (no writer), retrying")
                            break

                        # Writer connected — switch to blocking and start streaming.
                        writer_connected = True
                        os.set_blocking(fd, True)
                        logger.info("AudioPipeReader: writer connected, streaming")
                        self._last_audio_time = time.monotonic()
                        self._silence_fired = False
                        await self._on_audio_chunk(chunk)
                    else:
                        # Blocking read in executor to avoid starving the event loop
                        chunk = await loop.run_in_executor(
                            None, lambda: os.read(fd, self._chunk_size)
                        )
                        if not chunk:
                            logger.debug("AudioPipeReader: EOF on pipe, will reopen")
                            break
                        self._last_audio_time = time.monotonic()
                        self._silence_fired = False
                        await self._on_audio_chunk(chunk)
            except OSError as exc:
                if self._running:
                    logger.warning("AudioPipeReader: read error — %s", exc)
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

            if self._running:
                await asyncio.sleep(self._reopen_delay)

    # ------------------------------------------------------------------
    # Silence watchdog
    # ------------------------------------------------------------------

    async def _watchdog_loop(self) -> None:
        """
        Check every second whether audio has been silent for too long.

        The watchdog only fires after the first chunk has been received
        (self._last_audio_time > 0). This prevents spurious timeouts while
        the pipe is being opened for the very first time.
        """
        while self._running:
            await asyncio.sleep(1.0)

            if not self._running:
                break

            if self._last_audio_time == 0.0:
                continue   # no audio received yet — nothing to watch

            elapsed = time.monotonic() - self._last_audio_time
            if elapsed >= self._silence_timeout and not self._silence_fired:
                self._silence_fired = True
                logger.warning(
                    "AudioPipeReader: %.1f s of silence — firing timeout callback",
                    elapsed,
                )
                if self._on_silence_timeout:
                    try:
                        await self._on_silence_timeout()
                    except Exception as exc:
                        logger.error("on_silence_timeout raised: %s", exc)
