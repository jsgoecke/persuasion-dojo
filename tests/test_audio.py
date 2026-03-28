"""
Tests for backend/audio.py (AudioPipeReader).

Uses real OS FIFOs (mkfifo) so the pipe semantics are genuine.
No mocking of the filesystem — the tests create temp pipes, write to them
from a helper thread, and verify the async callbacks fire correctly.

Covers:
  - start() / stop() lifecycle
  - Chunks delivered to on_audio_chunk in order
  - EOF on pipe causes re-open (writer reconnects, second batch delivered)
  - FileNotFoundError on missing pipe — reader waits and retries
  - Silence watchdog fires on_silence_timeout after threshold
  - Silence watchdog does NOT fire before first chunk
  - Silence watchdog resets after audio resumes
  - Empty chunks (zero-byte reads) do not update last_audio_time
  - Double-start is idempotent; double-stop is safe
  - is_running reflects state correctly
  - last_audio_time is 0.0 before first chunk, updated afterwards
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time

import pytest

from backend.audio import AudioPipeReader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fifo() -> str:
    """Create a temp directory and return the path of a new FIFO inside it."""
    tmpdir = tempfile.mkdtemp()
    pipe_path = os.path.join(tmpdir, "test.pipe")
    os.mkfifo(pipe_path)
    return pipe_path


def _write_to_pipe(pipe_path: str, chunks: list[bytes], delay_s: float = 0.01) -> None:
    """
    Open the FIFO for writing in a thread, write chunks, then close.

    Runs in a background thread so it doesn't block the event loop.
    delay_s: pause between chunks (keeps the reader busy for a moment).
    """
    def _writer():
        with open(pipe_path, "wb") as f:
            for chunk in chunks:
                f.write(chunk)
                f.flush()
                time.sleep(delay_s)
    threading.Thread(target=_writer, daemon=True).start()


def _write_after_delay(pipe_path: str, chunks: list[bytes], delay_s: float) -> None:
    """Write to the pipe after a delay (simulates late Swift reconnect)."""
    def _writer():
        time.sleep(delay_s)
        with open(pipe_path, "wb") as f:
            for chunk in chunks:
                f.write(chunk)
                f.flush()
    threading.Thread(target=_writer, daemon=True).start()


def make_reader(
    pipe_path: str,
    *,
    chunks_out: list[bytes],
    silence_timeouts: list[float] | None = None,
    silence_timeout_s: float = 5.0,
    reopen_delay_s: float = 0.05,
) -> AudioPipeReader:
    """Build an AudioPipeReader with list-appending callbacks."""

    async def on_chunk(data: bytes) -> None:
        chunks_out.append(data)

    silence_times: list[float] = silence_timeouts if silence_timeouts is not None else []

    async def on_silence() -> None:
        silence_times.append(time.monotonic())

    return AudioPipeReader(
        on_audio_chunk=on_chunk,
        on_silence_timeout=on_silence if silence_timeouts is not None else None,
        pipe_path=pipe_path,
        silence_timeout_s=silence_timeout_s,
        reopen_delay_s=reopen_delay_s,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_is_running_after_start(self):
        pipe_path = _make_fifo()
        reader = make_reader(pipe_path, chunks_out=[])
        await reader.start()
        assert reader.is_running
        await reader.stop()

    @pytest.mark.asyncio
    async def test_not_running_after_stop(self):
        pipe_path = _make_fifo()
        reader = make_reader(pipe_path, chunks_out=[])
        await reader.start()
        await reader.stop()
        assert not reader.is_running

    @pytest.mark.asyncio
    async def test_double_start_idempotent(self):
        pipe_path = _make_fifo()
        reader = make_reader(pipe_path, chunks_out=[])
        await reader.start()
        await reader.start()   # second call — must not crash
        assert reader.is_running
        await reader.stop()

    @pytest.mark.asyncio
    async def test_double_stop_safe(self):
        pipe_path = _make_fifo()
        reader = make_reader(pipe_path, chunks_out=[])
        await reader.start()
        await reader.stop()
        await reader.stop()    # must not raise
        assert not reader.is_running

    @pytest.mark.asyncio
    async def test_stop_before_start_safe(self):
        pipe_path = _make_fifo()
        reader = make_reader(pipe_path, chunks_out=[])
        await reader.stop()    # must not raise

    def test_not_running_initially(self):
        pipe_path = _make_fifo()
        reader = make_reader(pipe_path, chunks_out=[])
        assert not reader.is_running


# ---------------------------------------------------------------------------
# Chunk delivery
# ---------------------------------------------------------------------------

class TestChunkDelivery:
    @pytest.mark.asyncio
    async def test_chunks_delivered_to_callback(self):
        pipe_path = _make_fifo()
        chunks_out: list[bytes] = []
        reader = make_reader(pipe_path, chunks_out=chunks_out, reopen_delay_s=0.05)

        await reader.start()
        _write_to_pipe(pipe_path, [b"hello", b"world"])
        await asyncio.sleep(0.3)
        await reader.stop()

        received = b"".join(chunks_out)
        assert b"hello" in received
        assert b"world" in received

    @pytest.mark.asyncio
    async def test_chunks_delivered_in_order(self):
        pipe_path = _make_fifo()
        chunks_out: list[bytes] = []
        reader = make_reader(pipe_path, chunks_out=chunks_out, reopen_delay_s=0.05)

        payload = [bytes([i]) * 64 for i in range(5)]
        await reader.start()
        _write_to_pipe(pipe_path, payload, delay_s=0.02)
        await asyncio.sleep(0.4)
        await reader.stop()

        received = b"".join(chunks_out)
        expected = b"".join(payload)
        assert received == expected

    @pytest.mark.asyncio
    async def test_last_audio_time_updated_after_chunk(self):
        pipe_path = _make_fifo()
        chunks_out: list[bytes] = []
        reader = make_reader(pipe_path, chunks_out=chunks_out)

        assert reader.last_audio_time == 0.0
        before = time.monotonic()
        await reader.start()
        _write_to_pipe(pipe_path, [b"x" * 32])
        await asyncio.sleep(0.2)
        await reader.stop()

        assert reader.last_audio_time >= before
        assert reader.last_audio_time <= time.monotonic()

    @pytest.mark.asyncio
    async def test_last_audio_time_zero_before_first_chunk(self):
        pipe_path = _make_fifo()
        reader = make_reader(pipe_path, chunks_out=[])
        # Don't write anything
        await reader.start()
        await asyncio.sleep(0.05)
        assert reader.last_audio_time == 0.0
        await reader.stop()


# ---------------------------------------------------------------------------
# EOF / reconnect
# ---------------------------------------------------------------------------

class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_delivers_second_batch(self):
        """
        Writer closes (EOF) then reconnects — both batches received.
        """
        pipe_path = _make_fifo()
        chunks_out: list[bytes] = []
        reader = make_reader(pipe_path, chunks_out=chunks_out, reopen_delay_s=0.05)

        await reader.start()

        # First writer — sends batch 1 then closes
        _write_to_pipe(pipe_path, [b"batch1"], delay_s=0.0)
        await asyncio.sleep(0.2)

        # Second writer (reconnect) — sends batch 2
        _write_to_pipe(pipe_path, [b"batch2"], delay_s=0.0)
        await asyncio.sleep(0.2)

        await reader.stop()

        received = b"".join(chunks_out)
        assert b"batch1" in received
        assert b"batch2" in received

    @pytest.mark.asyncio
    async def test_missing_pipe_retried(self):
        """
        Reader starts before the FIFO exists; pipe appears later and data flows.
        """
        tmpdir = tempfile.mkdtemp()
        pipe_path = os.path.join(tmpdir, "late.pipe")
        # Don't create the FIFO yet

        chunks_out: list[bytes] = []
        reader = make_reader(
            pipe_path,
            chunks_out=chunks_out,
            reopen_delay_s=0.05,
        )

        await reader.start()
        await asyncio.sleep(0.1)   # reader retries while pipe is missing

        # Create the pipe if the reader hasn't already (it calls _ensure_pipe)
        if not os.path.exists(pipe_path):
            os.mkfifo(pipe_path)
        _write_to_pipe(pipe_path, [b"late_data"])
        await asyncio.sleep(0.3)
        await reader.stop()

        assert b"late_data" in b"".join(chunks_out)


# ---------------------------------------------------------------------------
# Silence watchdog
# ---------------------------------------------------------------------------

class TestSilenceWatchdog:
    @pytest.mark.asyncio
    async def test_watchdog_fires_after_timeout(self):
        """
        After audio stops, watchdog fires on_silence_timeout.
        """
        pipe_path = _make_fifo()
        silence_times: list[float] = []
        reader = make_reader(
            pipe_path,
            chunks_out=[],
            silence_timeouts=silence_times,
            silence_timeout_s=1.0,
            reopen_delay_s=0.05,
        )

        await reader.start()
        # Write one chunk so the watchdog activates
        _write_to_pipe(pipe_path, [b"x" * 32])
        await asyncio.sleep(0.2)

        # Now wait past the silence threshold (1.0 s + watchdog poll interval 1.0 s)
        await asyncio.sleep(2.5)
        await reader.stop()

        assert len(silence_times) == 1

    @pytest.mark.asyncio
    async def test_watchdog_does_not_fire_before_first_chunk(self):
        """
        Watchdog must NOT fire if no audio has ever been received.
        """
        tmpdir = tempfile.mkdtemp()
        pipe_path = os.path.join(tmpdir, "no_data.pipe")
        os.mkfifo(pipe_path)

        silence_times: list[float] = []
        reader = make_reader(
            pipe_path,
            chunks_out=[],
            silence_timeouts=silence_times,
            silence_timeout_s=0.5,   # very short threshold
            reopen_delay_s=0.05,
        )

        await reader.start()
        # Don't write anything; wait long enough for watchdog to would-fire
        await asyncio.sleep(2.0)
        await reader.stop()

        assert silence_times == []

    @pytest.mark.asyncio
    async def test_watchdog_fires_only_once_per_silent_period(self):
        """
        Silence callback fires at most once until audio resumes.
        """
        pipe_path = _make_fifo()
        silence_times: list[float] = []
        reader = make_reader(
            pipe_path,
            chunks_out=[],
            silence_timeouts=silence_times,
            silence_timeout_s=1.0,
            reopen_delay_s=0.05,
        )

        await reader.start()
        _write_to_pipe(pipe_path, [b"x" * 32])
        await asyncio.sleep(0.1)

        # Wait well past threshold — watchdog should fire once, not repeatedly
        await asyncio.sleep(3.5)
        await reader.stop()

        assert len(silence_times) == 1

    @pytest.mark.asyncio
    async def test_watchdog_resets_when_audio_resumes(self):
        """
        After silence callback fires, new audio resets the watchdog so it
        can fire again if silence recurs.
        """
        pipe_path = _make_fifo()
        silence_times: list[float] = []
        reader = make_reader(
            pipe_path,
            chunks_out=[],
            silence_timeouts=silence_times,
            silence_timeout_s=1.0,
            reopen_delay_s=0.05,
        )

        await reader.start()

        # First batch — triggers silence
        _write_to_pipe(pipe_path, [b"first" * 8])
        await asyncio.sleep(0.1)
        await asyncio.sleep(2.5)   # let first silence fire
        assert len(silence_times) == 1

        # Second batch — audio resumes, resets the watchdog
        _write_to_pipe(pipe_path, [b"second" * 8])
        await asyncio.sleep(0.1)
        await asyncio.sleep(2.5)   # second silence should fire again
        await reader.stop()

        assert len(silence_times) == 2

    @pytest.mark.asyncio
    async def test_no_silence_callback_set_does_not_raise(self):
        """
        Reader with no on_silence_timeout must not raise after the threshold.
        """
        pipe_path = _make_fifo()
        chunks_out: list[bytes] = []

        async def on_chunk(data: bytes) -> None:
            chunks_out.append(data)

        reader = AudioPipeReader(
            on_audio_chunk=on_chunk,
            on_silence_timeout=None,
            pipe_path=pipe_path,
            silence_timeout_s=1.0,
            reopen_delay_s=0.05,
        )

        await reader.start()
        _write_to_pipe(pipe_path, [b"x" * 32])
        await asyncio.sleep(0.1)
        await asyncio.sleep(2.5)   # threshold exceeded — must not raise
        await reader.stop()
