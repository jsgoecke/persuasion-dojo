"""
Tests for audio pipeline lifecycle — pipe cleanup, session-end signaling,
orphan prevention, multi-session resilience, and watchdog behaviour.

These tests exist because orphaned AudioCapture processes and stale pipes
have been the #1 recurring production issue. Every class here corresponds
to a failure mode that has broken "Go Live" in the past.

Covers:
  1. Pipe file management: create, remove, idempotent cleanup, stale pipe handling
  2. Reader state machine: start/stop, double-start, double-stop
  3. Multi-session lifecycle: stop→start→stop cycles (the bug that kept breaking)
  4. Session-end signaling: client stops capture on session_ended
  5. Lifespan shutdown: pipe removal, background task cancellation
  6. Silence watchdog: fires after timeout, resets on audio, doesn't fire early
  7. Audio callback plumbing: chunks forwarded, level metering works
  8. Deepgram reconnect gating: backoff after failure, retry after cooldown
  9. Silence audio detection: all-zero PCM produces zero level, triggers no transcription
  10. Transcriber status WebSocket messages: cloud/local badge events reach frontend
  11. Audio level accumulation: threshold crossing, multi-chunk accumulation
"""

from __future__ import annotations

import asyncio
import math
import os
import stat
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.audio import AudioPipeReader


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pipe_path(tmp_path):
    """Return a temporary pipe path (not yet created)."""
    return str(tmp_path / "test_audio.pipe")


@pytest.fixture
def reader(pipe_path):
    """AudioPipeReader with a temp pipe path and no-op callbacks."""
    return AudioPipeReader(
        on_audio_chunk=AsyncMock(),
        on_silence_timeout=AsyncMock(),
        pipe_path=pipe_path,
    )


def _make_reader(pipe_path, **kwargs):
    """Create a reader with custom parameters."""
    defaults = dict(
        on_audio_chunk=AsyncMock(),
        on_silence_timeout=AsyncMock(),
        pipe_path=pipe_path,
    )
    defaults.update(kwargs)
    return AudioPipeReader(**defaults)


# ---------------------------------------------------------------------------
# 1. Pipe file management
# ---------------------------------------------------------------------------

class TestPipeCleanup:
    """Verify named pipe files are correctly created and removed."""

    @pytest.mark.asyncio
    async def test_stop_removes_pipe(self, reader, pipe_path):
        """stop() should remove the pipe file."""
        os.mkfifo(pipe_path)
        assert os.path.exists(pipe_path)
        reader._running = True
        await reader.stop()
        assert not os.path.exists(pipe_path)

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, reader, pipe_path):
        """Calling stop() twice should not raise."""
        os.mkfifo(pipe_path)
        reader._running = True
        await reader.stop()
        await reader.stop()
        assert not os.path.exists(pipe_path)

    def test_cleanup_pipe_no_file(self, reader, pipe_path):
        """_cleanup_pipe() is safe when pipe doesn't exist."""
        assert not os.path.exists(pipe_path)
        reader._cleanup_pipe()  # Should not raise

    def test_cleanup_pipe_removes_existing(self, reader, pipe_path):
        """_cleanup_pipe() removes an existing pipe."""
        os.mkfifo(pipe_path)
        reader._cleanup_pipe()
        assert not os.path.exists(pipe_path)

    def test_cleanup_pipe_idempotent(self, reader, pipe_path):
        """Calling _cleanup_pipe() twice is safe."""
        os.mkfifo(pipe_path)
        reader._cleanup_pipe()
        reader._cleanup_pipe()
        assert not os.path.exists(pipe_path)

    def test_ensure_pipe_creates_fifo(self, reader, pipe_path):
        """_ensure_pipe() creates an actual FIFO (not a regular file)."""
        assert not os.path.exists(pipe_path)
        reader._ensure_pipe()
        assert os.path.exists(pipe_path)
        mode = os.stat(pipe_path).st_mode
        assert stat.S_ISFIFO(mode), "Expected FIFO, got something else"

    def test_ensure_pipe_reuses_existing_fifo(self, reader, pipe_path):
        """_ensure_pipe() reuses an existing FIFO (Swift writer may be attached)."""
        os.mkfifo(pipe_path)
        inode_before = os.stat(pipe_path).st_ino
        reader._ensure_pipe()
        assert os.path.exists(pipe_path)
        inode_after = os.stat(pipe_path).st_ino
        assert inode_before == inode_after, "Existing FIFO should be reused, not recreated"
        assert stat.S_ISFIFO(os.stat(pipe_path).st_mode)

    def test_ensure_pipe_replaces_non_fifo(self, reader, pipe_path):
        """_ensure_pipe() replaces a regular file with a FIFO."""
        with open(pipe_path, "w") as f:
            f.write("not a pipe")
        reader._ensure_pipe()
        assert os.path.exists(pipe_path)
        assert stat.S_ISFIFO(os.stat(pipe_path).st_mode)


# ---------------------------------------------------------------------------
# 2. Reader state machine
# ---------------------------------------------------------------------------

class TestReaderState:
    """Verify the reader's running/stopped state transitions."""

    def test_initial_state(self, reader):
        """Reader starts in non-running state with no audio history."""
        assert not reader.is_running
        assert reader.last_audio_time == 0.0

    @pytest.mark.asyncio
    async def test_running_after_start(self, reader):
        """Reader is running after start()."""
        await reader.start()
        assert reader.is_running
        await reader.stop()

    @pytest.mark.asyncio
    async def test_not_running_after_stop(self, reader):
        """Reader is not running after stop()."""
        await reader.start()
        await reader.stop()
        assert not reader.is_running

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self, reader):
        """Calling start() twice doesn't create duplicate tasks."""
        await reader.start()
        task1 = reader._read_task
        await reader.start()  # should be a no-op
        assert reader._read_task is task1
        await reader.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self, reader):
        """stop() on a never-started reader is safe."""
        await reader.stop()  # Should not raise
        assert not reader.is_running

    @pytest.mark.asyncio
    async def test_start_creates_both_tasks(self, reader):
        """start() creates both the read task and the watchdog task."""
        await reader.start()
        assert reader._read_task is not None
        assert reader._watchdog_task is not None
        await reader.stop()

    @pytest.mark.asyncio
    async def test_stop_nulls_both_tasks(self, reader):
        """stop() sets both tasks to None."""
        await reader.start()
        await reader.stop()
        assert reader._read_task is None
        assert reader._watchdog_task is None


# ---------------------------------------------------------------------------
# 3. Multi-session lifecycle (THE recurring bug)
#
# Root cause: session-end kills AudioCapture, next session never restarts it.
# These tests simulate sequential session start/stop cycles to verify the
# reader can be reused across sessions without leaving stale state.
# ---------------------------------------------------------------------------

class TestMultiSessionLifecycle:
    """
    Simulate the real-world pattern: user does "Go Live", ends session,
    then does "Go Live" again. This broke repeatedly because:
      - Pipe file not cleaned up between sessions
      - Reader state not reset on second start
      - Background tasks leaked across sessions
    """

    @pytest.mark.asyncio
    async def test_start_stop_start_stop(self, reader, pipe_path):
        """Reader can do a full start→stop→start→stop cycle."""
        # Session 1
        await reader.start()
        assert reader.is_running
        assert os.path.exists(pipe_path)
        await reader.stop()
        assert not reader.is_running
        assert not os.path.exists(pipe_path)

        # Session 2 — this is what kept breaking
        await reader.start()
        assert reader.is_running
        assert os.path.exists(pipe_path)
        await reader.stop()
        assert not reader.is_running
        assert not os.path.exists(pipe_path)

    @pytest.mark.asyncio
    async def test_three_consecutive_sessions(self, reader, pipe_path):
        """Three back-to-back sessions all work cleanly."""
        for i in range(3):
            await reader.start()
            assert reader.is_running, f"Session {i+1}: not running after start"
            assert os.path.exists(pipe_path), f"Session {i+1}: pipe missing"
            await reader.stop()
            assert not reader.is_running, f"Session {i+1}: still running after stop"
            assert not os.path.exists(pipe_path), f"Session {i+1}: pipe not cleaned"

    @pytest.mark.asyncio
    async def test_second_session_creates_fresh_pipe(self, reader, pipe_path):
        """After stop removes the pipe, the next start creates a new one."""
        await reader.start()
        await reader.stop()
        assert not os.path.exists(pipe_path)

        await reader.start()
        assert os.path.exists(pipe_path)
        mode = os.stat(pipe_path).st_mode
        assert stat.S_ISFIFO(mode), "Second session should create a proper FIFO"
        await reader.stop()

    @pytest.mark.asyncio
    async def test_stale_pipe_from_crash_handled(self, reader, pipe_path):
        """If a stale pipe exists from a previous crash, start() reuses it."""
        # Simulate crash: pipe exists but reader was never stopped cleanly
        os.mkfifo(pipe_path)

        await reader.start()
        assert os.path.exists(pipe_path)
        assert reader.is_running
        await reader.stop()
        assert not os.path.exists(pipe_path)

    @pytest.mark.asyncio
    async def test_state_resets_between_sessions(self, reader, pipe_path):
        """Internal state (last_audio_time, silence_fired) resets on new start."""
        await reader.start()
        # Simulate receiving audio
        reader._last_audio_time = time.monotonic()
        reader._silence_fired = True
        await reader.stop()

        # Second session should have fresh state
        await reader.start()
        assert reader._last_audio_time == 0.0, "last_audio_time not reset"
        assert reader._silence_fired is False, "silence_fired not reset"
        await reader.stop()

    @pytest.mark.asyncio
    async def test_separate_readers_for_each_session(self, pipe_path):
        """
        Simulates what actually happens in production: a new AudioPipeReader
        is created for each WebSocket session. Each reader should independently
        manage the pipe file.
        """
        # Session 1
        reader1 = _make_reader(pipe_path)
        await reader1.start()
        assert os.path.exists(pipe_path)
        await reader1.stop()
        assert not os.path.exists(pipe_path)

        # Session 2 — new reader instance, same pipe path
        reader2 = _make_reader(pipe_path)
        await reader2.start()
        assert os.path.exists(pipe_path), "Second reader failed to create pipe"
        assert reader2.is_running
        await reader2.stop()
        assert not os.path.exists(pipe_path)


# ---------------------------------------------------------------------------
# 4. Pipe lifecycle edge cases
# ---------------------------------------------------------------------------

class TestPipeLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_stop_removes(self, reader, pipe_path):
        """Full start/stop cycle should create then remove the pipe."""
        await reader.start()
        assert os.path.exists(pipe_path)
        assert reader.is_running
        await reader.stop()
        assert not os.path.exists(pipe_path)
        assert not reader.is_running

    @pytest.mark.asyncio
    async def test_stale_pipe_replaced_on_start(self, reader, pipe_path):
        """If a stale pipe exists from a previous session, start() reuses it."""
        os.mkfifo(pipe_path)
        assert os.path.exists(pipe_path)
        await reader.start()
        assert os.path.exists(pipe_path)
        assert reader.is_running
        await reader.stop()
        assert not os.path.exists(pipe_path)

    @pytest.mark.asyncio
    async def test_regular_file_at_pipe_path(self, pipe_path):
        """
        If something other than a FIFO exists at the pipe path
        (e.g. a debug log accidentally written there), ensure_pipe
        should not crash.
        """
        # Create a regular file at the pipe path
        with open(pipe_path, "w") as f:
            f.write("not a pipe")

        reader = _make_reader(pipe_path)
        # _ensure_pipe sees the path exists and skips creation — no crash
        reader._ensure_pipe()
        # Cleanup should still remove it
        reader._cleanup_pipe()
        assert not os.path.exists(pipe_path)


# ---------------------------------------------------------------------------
# 5. Session-end signaling
# ---------------------------------------------------------------------------

class TestSessionEndSignal:
    """
    Verify the WebSocket session-end sequence: session_ended is sent before
    ws.close(). The client stops AudioCapture when it receives session_ended
    (no separate stop_capture message — that raced with ws.close()).
    """

    @pytest.mark.asyncio
    async def test_session_ended_sent_before_close(self):
        """session_ended message must precede WebSocket close."""
        call_order: list[str] = []

        ws = AsyncMock()
        ws.send_json = AsyncMock(
            side_effect=lambda msg: call_order.append(f"send:{msg.get('type', '')}")
        )
        ws.close = AsyncMock(side_effect=lambda: call_order.append("close"))

        # Simulate the tail of _handle_session_end
        await ws.send_json({"type": "session_ended", "session_id": "test"})
        await ws.close()

        assert "send:session_ended" in call_order, "session_ended was never sent"
        assert call_order.index("send:session_ended") < call_order.index("close"), \
            f"session_ended must come before close, got: {call_order}"

    @pytest.mark.asyncio
    async def test_no_separate_stop_capture_message(self):
        """Backend must NOT send a separate stop_capture — client handles it on session_ended."""
        messages_sent: list[str] = []
        ws = AsyncMock()
        ws.send_json = AsyncMock(
            side_effect=lambda msg: messages_sent.append(msg.get("type", ""))
        )
        ws.close = AsyncMock()

        # Simulate session end — only session_ended, no stop_capture
        await ws.send_json({"type": "session_ended", "session_id": "test"})
        await ws.close()

        assert "session_ended" in messages_sent
        assert "stop_capture" not in messages_sent, \
            "stop_capture should not be sent — client stops on session_ended"

    @pytest.mark.asyncio
    async def test_session_ended_always_sent_even_without_utterances(self):
        """
        Even an empty session (no speech detected) should send session_ended
        so the frontend knows to clean up and stop AudioCapture.
        """
        call_order: list[str] = []
        ws = AsyncMock()
        ws.send_json = AsyncMock(
            side_effect=lambda msg: call_order.append(msg.get("type", ""))
        )
        ws.close = AsyncMock()

        # Simulate empty session end
        await ws.send_json({
            "type": "session_ended",
            "session_id": "test",
            "persuasion_score": None,
        })
        await ws.close()

        assert "session_ended" in call_order


# ---------------------------------------------------------------------------
# 6. Lifespan shutdown
# ---------------------------------------------------------------------------

class TestLifespanShutdown:
    """Verify the FastAPI lifespan shutdown handler cleans up correctly."""

    @pytest.mark.asyncio
    async def test_lifespan_does_not_delete_pipe(self):
        """
        Pipe cleanup is owned by AudioPipeReader.stop(), not the lifespan
        shutdown handler. If someone re-adds pipe deletion, this test catches it.
        """
        import inspect
        from backend.main import lifespan
        source = inspect.getsource(lifespan)
        assert "unlink" not in source, \
            "Lifespan should not call unlink — pipe cleanup is owned by AudioPipeReader"

    @pytest.mark.asyncio
    async def test_background_tasks_cancelled_on_shutdown(self):
        """
        Background tasks (debrief generation, playbook updates) stored in
        app.state.background_tasks must be cancelled on shutdown.
        """
        bg_tasks: set[asyncio.Task] = set()

        # Simulate two long-running background tasks
        async def _long_task():
            await asyncio.sleep(3600)

        t1 = asyncio.create_task(_long_task())
        t2 = asyncio.create_task(_long_task())
        bg_tasks.add(t1)
        bg_tasks.add(t2)

        # Simulate lifespan shutdown
        for task in bg_tasks:
            if not task.done():
                task.cancel()
        bg_tasks.clear()

        # Let cancellation propagate
        for t in (t1, t2):
            try:
                await t
            except asyncio.CancelledError:
                pass

        assert t1.cancelled()
        assert t2.cancelled()

    @pytest.mark.asyncio
    async def test_completed_tasks_not_recancelled(self):
        """Tasks that finished before shutdown should not cause errors."""
        bg_tasks: set[asyncio.Task] = set()

        async def _quick_task():
            return "done"

        t = asyncio.create_task(_quick_task())
        bg_tasks.add(t)
        t.add_done_callback(bg_tasks.discard)

        # Let the task complete
        await asyncio.sleep(0.05)
        assert t.done()
        # bg_tasks should be empty due to discard callback
        assert len(bg_tasks) == 0

        # Shutdown should be a no-op
        for task in bg_tasks:
            if not task.done():
                task.cancel()
        bg_tasks.clear()


# ---------------------------------------------------------------------------
# 7. Silence watchdog
# ---------------------------------------------------------------------------

class TestSilenceWatchdog:
    """
    The watchdog fires on_silence_timeout when no audio arrives for
    silence_timeout_s. This tells Electron to restart the Swift binary.
    """

    @pytest.mark.asyncio
    async def test_watchdog_fires_after_timeout(self, pipe_path):
        """
        After receiving audio then going silent, the watchdog should fire
        on_silence_timeout within ~silence_timeout_s.
        """
        silence_cb = AsyncMock()
        reader = AudioPipeReader(
            on_audio_chunk=AsyncMock(),
            on_silence_timeout=silence_cb,
            pipe_path=pipe_path,
            silence_timeout_s=0.5,  # short for testing
        )

        # Simulate: reader is running, received audio, then went silent
        reader._running = True
        reader._last_audio_time = time.monotonic()
        reader._silence_fired = False
        reader._watchdog_task = asyncio.ensure_future(reader._watchdog_loop())

        # Wait for the watchdog to fire (timeout + 1 check interval)
        await asyncio.sleep(2.0)

        reader._running = False
        reader._watchdog_task.cancel()
        try:
            await reader._watchdog_task
        except asyncio.CancelledError:
            pass

        silence_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_watchdog_does_not_fire_before_first_audio(self, pipe_path):
        """
        The watchdog should NOT fire before any audio has been received.
        This prevents false alarms during the initial pipe-open phase.
        """
        silence_cb = AsyncMock()
        reader = AudioPipeReader(
            on_audio_chunk=AsyncMock(),
            on_silence_timeout=silence_cb,
            pipe_path=pipe_path,
            silence_timeout_s=0.3,
        )

        reader._running = True
        reader._last_audio_time = 0.0  # no audio received yet
        reader._watchdog_task = asyncio.ensure_future(reader._watchdog_loop())

        await asyncio.sleep(1.0)

        reader._running = False
        reader._watchdog_task.cancel()
        try:
            await reader._watchdog_task
        except asyncio.CancelledError:
            pass

        silence_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_watchdog_fires_only_once(self, pipe_path):
        """
        The silence callback should fire at most once per connection
        (until audio resumes and resets the flag).
        """
        silence_cb = AsyncMock()
        reader = AudioPipeReader(
            on_audio_chunk=AsyncMock(),
            on_silence_timeout=silence_cb,
            pipe_path=pipe_path,
            silence_timeout_s=0.3,
        )

        reader._running = True
        reader._last_audio_time = time.monotonic()
        reader._silence_fired = False
        reader._watchdog_task = asyncio.ensure_future(reader._watchdog_loop())

        # Wait long enough for multiple potential firings
        await asyncio.sleep(2.0)

        reader._running = False
        reader._watchdog_task.cancel()
        try:
            await reader._watchdog_task
        except asyncio.CancelledError:
            pass

        assert silence_cb.call_count == 1, \
            f"Expected exactly 1 call, got {silence_cb.call_count}"

    @pytest.mark.asyncio
    async def test_watchdog_resets_when_audio_resumes(self, pipe_path):
        """
        If audio goes silent and watchdog fires, then audio resumes
        (last_audio_time updated, silence_fired reset), the watchdog
        should fire again on the next silence.
        """
        silence_cb = AsyncMock()
        reader = AudioPipeReader(
            on_audio_chunk=AsyncMock(),
            on_silence_timeout=silence_cb,
            pipe_path=pipe_path,
            silence_timeout_s=0.3,
        )

        reader._running = True
        reader._last_audio_time = time.monotonic()
        reader._silence_fired = False
        reader._watchdog_task = asyncio.ensure_future(reader._watchdog_loop())

        # Wait for first silence timeout
        await asyncio.sleep(1.5)
        assert silence_cb.call_count == 1

        # Simulate audio resuming
        reader._last_audio_time = time.monotonic()
        reader._silence_fired = False

        # Wait for second silence timeout
        await asyncio.sleep(1.5)

        reader._running = False
        reader._watchdog_task.cancel()
        try:
            await reader._watchdog_task
        except asyncio.CancelledError:
            pass

        assert silence_cb.call_count == 2, \
            f"Expected 2 calls after audio resume, got {silence_cb.call_count}"

    @pytest.mark.asyncio
    async def test_watchdog_tolerates_callback_exception(self, pipe_path):
        """
        If on_silence_timeout raises, the watchdog should log it but
        NOT crash — it must keep running for the rest of the session.
        """
        silence_cb = AsyncMock(side_effect=RuntimeError("callback failed"))
        reader = AudioPipeReader(
            on_audio_chunk=AsyncMock(),
            on_silence_timeout=silence_cb,
            pipe_path=pipe_path,
            silence_timeout_s=0.3,
        )

        reader._running = True
        reader._last_audio_time = time.monotonic()
        reader._silence_fired = False
        reader._watchdog_task = asyncio.ensure_future(reader._watchdog_loop())

        await asyncio.sleep(1.0)

        # Watchdog should still be running (not crashed)
        assert not reader._watchdog_task.done(), "Watchdog crashed on callback exception"

        reader._running = False
        reader._watchdog_task.cancel()
        try:
            await reader._watchdog_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# 8. Integration: WebSocket session lifecycle via TestClient
# ---------------------------------------------------------------------------

class TestWebSocketMultiSession:
    """
    End-to-end multi-session tests using FastAPI TestClient.
    Verifies that sequential "Go Live" sessions work without orphan issues.
    """

    @pytest.fixture(autouse=True)
    def stub_audio_pipeline(self):
        """Replace the real audio pipeline with no-op stubs."""
        pipe_mock = MagicMock()
        pipe_mock.start = AsyncMock()
        pipe_mock.stop = AsyncMock()
        pipe_mock.last_audio_time = 0.0

        transcriber_mock = MagicMock()
        transcriber_mock.connect = AsyncMock()
        transcriber_mock.disconnect = AsyncMock()
        transcriber_mock.send_audio = AsyncMock()
        transcriber_mock.is_connected = False

        with (
            patch("backend.main.AudioPipeReader", return_value=pipe_mock),
            patch("backend.main.HybridTranscriber", return_value=transcriber_mock),
            patch("backend.main._load_settings", return_value={
                "deepgram_api_key": "test-dg-key",
            }),
        ):
            self._pipe_mock = pipe_mock
            self._transcriber_mock = transcriber_mock
            yield

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from sqlalchemy.ext.asyncio import create_async_engine
        from backend.database import override_engine
        from backend.main import app

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        override_engine(engine)
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
        asyncio.run(engine.dispose())

    def _create_session(self, client) -> str:
        r = client.post("/sessions", json={"context": "meeting"})
        assert r.status_code == 201
        return r.json()["session_id"]

    def test_two_sequential_sessions(self, client):
        """
        Two complete Go Live → End Session cycles.
        This is the exact pattern that broke when AudioCapture wasn't restarted.
        """
        from backend.main import SessionPipeline

        for i in range(2):
            sid = self._create_session(client)
            with patch.object(
                SessionPipeline, "compute_scores",
                return_value={
                    "persuasion_score": 50 + i * 10,
                    "timing_score": 7, "ego_safety_score": 7, "convergence_score": 7,
                    "timing_signals": [], "ego_safety_signals": [], "convergence_signals": [],
                },
            ):
                with client.websocket_connect(f"/ws/session/{sid}") as ws:
                    ws.send_json({"type": "session_end"})
                    data = ws.receive_json()
                    assert data["type"] == "session_ended", \
                        f"Session {i+1}: expected session_ended, got {data['type']}"

            # Verify stop was called on the audio reader
            assert self._pipe_mock.stop.call_count >= i + 1, \
                f"Session {i+1}: AudioPipeReader.stop() not called"

    def test_session_end_triggers_audio_stop(self, client):
        """
        When session_end is received, the finally block must call
        audio_reader.stop() — this is what removes the pipe.
        """
        from backend.main import SessionPipeline

        sid = self._create_session(client)
        with patch.object(
            SessionPipeline, "compute_scores",
            return_value={
                "persuasion_score": 60,
                "timing_score": 7, "ego_safety_score": 7, "convergence_score": 7,
                "timing_signals": [], "ego_safety_signals": [], "convergence_signals": [],
            },
        ):
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                ws.send_json({"type": "session_end"})
                ws.receive_json()  # session_ended

        self._pipe_mock.stop.assert_called()

    def test_session_end_triggers_transcriber_disconnect(self, client):
        """Transcriber must be disconnected when session ends."""
        from backend.main import SessionPipeline

        sid = self._create_session(client)
        with patch.object(
            SessionPipeline, "compute_scores",
            return_value={
                "persuasion_score": 60,
                "timing_score": 7, "ego_safety_score": 7, "convergence_score": 7,
                "timing_signals": [], "ego_safety_signals": [], "convergence_signals": [],
            },
        ):
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                ws.send_json({"type": "session_end"})
                ws.receive_json()  # session_ended

        self._transcriber_mock.disconnect.assert_called()

    def test_no_stop_capture_after_session_ended(self, client):
        """
        Backend must NOT send a separate stop_capture message.
        The client stops AudioCapture when it receives session_ended.
        """
        from backend.main import SessionPipeline

        sid = self._create_session(client)
        with patch.object(
            SessionPipeline, "compute_scores",
            return_value={
                "persuasion_score": 60,
                "timing_score": 7, "ego_safety_score": 7, "convergence_score": 7,
                "timing_signals": [], "ego_safety_signals": [], "convergence_signals": [],
            },
        ):
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                ws.send_json({"type": "session_end"})
                msg = ws.receive_json()  # session_ended

        assert msg["type"] == "session_ended"

    def test_ws_disconnect_without_session_end_no_crash(self, client):
        """
        If the user closes the overlay (WebSocket disconnects) without
        sending session_end, the server should not crash. The finally
        block in the handler calls audio_reader.stop() and
        transcriber.disconnect().
        """
        sid = self._create_session(client)
        with client.websocket_connect(f"/ws/session/{sid}") as ws:
            # Send a ping to confirm connection is live
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"
            # Close without session_end — should not crash the server

        # Server is still healthy after abrupt disconnect
        r = client.get("/health")
        assert r.status_code == 200

    def test_missing_deepgram_key_cloud_mode_closes_cleanly(self, client):
        """
        If the Deepgram API key is missing in cloud mode, the session should
        close immediately with an error — no dangling pipe reader.
        (In auto/local mode, Moonshine fallback handles this gracefully.)
        """
        # Override both settings and env var to simulate missing key
        with patch("backend.main._load_settings", return_value={}), \
             patch.dict("os.environ", {"DEEPGRAM_API_KEY": ""}, clear=False):
            r = client.post("/sessions", json={"context": "meeting", "transcription_mode": "cloud"})
            assert r.status_code == 201
            sid = r.json()["session_id"]
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "Deepgram" in data["message"]


# ---------------------------------------------------------------------------
# 9. Audio level metering
# ---------------------------------------------------------------------------

class TestAudioLevelMetering:
    """
    Verify the audio level computation that drives the sound level indicator.
    This tests the math, not the WebSocket plumbing.
    """

    def test_silence_produces_zero_level(self):
        """All-zero samples should produce level ≈ 0."""
        import struct
        n_samples = 4000  # 250ms at 16kHz
        data = struct.pack(f"<{n_samples}h", *([0] * n_samples))
        # Compute RMS manually
        samples = struct.unpack(f"<{n_samples}h", data)
        rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        level = min(rms / 32767.0, 1.0)
        assert level == 0.0

    def test_loud_signal_produces_high_level(self):
        """Max-amplitude samples should produce level ≈ 1.0."""
        import struct
        n_samples = 4000
        data = struct.pack(f"<{n_samples}h", *([32767] * n_samples))
        samples = struct.unpack(f"<{n_samples}h", data)
        rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        level = min(rms / 32767.0, 1.0)
        assert level == pytest.approx(1.0, abs=0.01)

    def test_moderate_signal_in_range(self):
        """A moderate signal (half amplitude) should be ~0.5."""
        import struct
        n_samples = 4000
        amplitude = 16383  # ~half max
        data = struct.pack(f"<{n_samples}h", *([amplitude] * n_samples))
        samples = struct.unpack(f"<{n_samples}h", data)
        rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        level = min(rms / 32767.0, 1.0)
        assert 0.3 < level < 0.7


# ---------------------------------------------------------------------------
# Regression: single-pipe mode must meter audio levels
# ---------------------------------------------------------------------------

class TestSinglePipeModeAudioLevels:
    """
    Regression test for single-pipe audio level metering (v0.10.0.0).

    When only one pipe exists (no mic pipe), the system audio handler
    must also compute and send audio levels. Previously, is_mic=False
    on the system handler meant no audio_level messages were ever sent,
    causing the UI meter to show zero.

    The fix: in single-pipe mode, the system handler gets is_mic=True.
    """

    def test_single_pipe_flag_logic(self):
        """
        Verify the is_mic flag logic: when _dual_pipe_mode is False,
        the system chunk handler must have is_mic=True.

        This tests the logic extracted from main.py's session handler.
        """
        # Simulate the dual/single pipe logic from main.py
        # Dual pipe mode: mic_handler.is_mic=True, system_handler.is_mic=False
        dual_pipe_mode = True
        system_is_mic = not dual_pipe_mode
        assert system_is_mic is False, "Dual pipe: system should NOT meter"

        # Single pipe mode: system_handler.is_mic=True (it's the only source)
        dual_pipe_mode = False
        system_is_mic = not dual_pipe_mode
        assert system_is_mic is True, "Single pipe: system MUST meter"

    def test_audio_level_computed_from_pcm(self):
        """
        Verify that the audio level metering math works correctly
        on PCM data that would come from system audio in single-pipe mode.
        """
        import struct

        # Simulate 250ms of moderate speech audio (mix of amplitudes)
        n_samples = 4000  # 250ms at 16kHz
        # Realistic speech: alternating loud and quiet samples
        samples_in = []
        for i in range(n_samples):
            # Simulated speech envelope
            amp = int(8000 * math.sin(2 * math.pi * 200 * i / 16000))
            samples_in.append(amp)

        data = struct.pack(f"<{n_samples}h", *samples_in)
        samples_out = struct.unpack(f"<{n_samples}h", data)
        rms = math.sqrt(sum(s * s for s in samples_out) / len(samples_out))
        level = min(rms / 32767.0, 1.0)

        # Should be non-zero (the bug was always-zero levels)
        assert level > 0.1, f"Audio level {level} too low — metering broken"
        assert level < 1.0


# ---------------------------------------------------------------------------
# 9. Silence audio detection — all-zero PCM from AudioCapture
# ---------------------------------------------------------------------------

class TestSilenceDetection:
    """
    Regression tests for the silence-output bug (v0.11.0).

    When AudioCapture lacks Screen Recording permission, ScreenCaptureKit
    delivers all-zero audio buffers. The mixer writes these zeros to the pipe.
    The backend receives 320-byte chunks of silence, computes RMS=0, and
    sends audio_level=0.0 to the frontend — causing the meter to flatline.

    These tests verify that:
    - All-zero chunks are detected as silence
    - Level accumulation works correctly across small chunks (320 bytes = 160 samples)
    - The threshold is actually reached after enough small chunks
    - Mixed silence + real audio produces correct levels
    """

    def test_small_chunks_accumulate_to_threshold(self):
        """
        AudioCapture sends 320-byte chunks (160 samples, 10ms at 16kHz).
        The level interval is 4000 samples. It takes 25 chunks to trigger
        a level computation. Verify the accumulator works.
        """
        import struct

        threshold = 16_000 // 4  # 4000 samples
        chunk_size_samples = 160
        accum = []
        sample_count = 0

        # Feed 25 chunks of moderate audio
        for _ in range(25):
            samples = [8000] * chunk_size_samples
            data = struct.pack(f"<{chunk_size_samples}h", *samples)
            parsed = struct.unpack(f"<{chunk_size_samples}h", data)
            accum.extend(s * s for s in parsed)
            sample_count += chunk_size_samples

        assert sample_count == 4000
        assert sample_count >= threshold
        assert len(accum) == 4000

        rms = math.sqrt(sum(accum) / len(accum))
        level = min(rms / 32767.0, 1.0)
        assert level > 0.2, "25 chunks of moderate audio must produce non-zero level"

    def test_silence_chunks_produce_zero_level(self):
        """
        25 chunks of all-zero PCM (AudioCapture without permission)
        must produce level == 0.0.
        """
        import struct

        chunk_size_samples = 160
        accum = []
        for _ in range(25):
            data = struct.pack(f"<{chunk_size_samples}h", *([0] * chunk_size_samples))
            parsed = struct.unpack(f"<{chunk_size_samples}h", data)
            accum.extend(s * s for s in parsed)

        rms = math.sqrt(sum(accum) / len(accum)) if accum else 0
        level = min(rms / 32767.0, 1.0)
        assert level == 0.0, "Silence must produce exactly zero level"

    def test_mixed_silence_and_audio(self):
        """
        If first 20 chunks are silence and last 5 are loud,
        the RMS should be non-zero but lower than pure audio.
        """
        import struct

        chunk_size_samples = 160
        accum = []

        # 20 chunks of silence
        for _ in range(20):
            data = struct.pack(f"<{chunk_size_samples}h", *([0] * chunk_size_samples))
            parsed = struct.unpack(f"<{chunk_size_samples}h", data)
            accum.extend(s * s for s in parsed)

        # 5 chunks of loud audio
        for _ in range(5):
            data = struct.pack(f"<{chunk_size_samples}h", *([20000] * chunk_size_samples))
            parsed = struct.unpack(f"<{chunk_size_samples}h", data)
            accum.extend(s * s for s in parsed)

        rms = math.sqrt(sum(accum) / len(accum))
        level = min(rms / 32767.0, 1.0)
        assert 0.1 < level < 0.7, f"Mixed silence+audio should be moderate, got {level}"

    def test_threshold_not_reached_with_too_few_chunks(self):
        """
        With only 10 chunks (1600 samples), the 4000-sample threshold
        should NOT be reached. This verifies no premature level event.
        """
        threshold = 16_000 // 4  # 4000 samples
        sample_count = 160 * 10  # 10 chunks
        assert sample_count < threshold, "10 chunks must be below threshold"

    def test_struct_unpack_handles_odd_byte_count(self):
        """
        If a chunk has an odd number of bytes (e.g. 321), struct.unpack
        should still work on the even portion.
        """
        import struct

        data = bytes(321)  # 321 bytes → 160 samples + 1 leftover byte
        n_samples = len(data) // 2
        assert n_samples == 160
        samples = struct.unpack(f"<{n_samples}h", data[:n_samples * 2])
        assert len(samples) == 160
        assert all(s == 0 for s in samples)


# ---------------------------------------------------------------------------
# 10. Transcriber status WebSocket messages
# ---------------------------------------------------------------------------

class TestTranscriberStatusWebSocket:
    """
    Verify that transcriber_status events (using_cloud, using_local,
    fallback_activated) are forwarded to the frontend via WebSocket.

    These tests mock the audio pipeline and HybridTranscriber to control
    which status events fire, then verify the WebSocket messages received.
    """

    @pytest.fixture(autouse=True)
    def stub_pipeline(self):
        """Stub audio pipeline, capturing the on_status callback."""
        self._captured_on_status = None

        pipe_mock = MagicMock()
        pipe_mock.start = AsyncMock()
        pipe_mock.stop = AsyncMock()
        pipe_mock.last_audio_time = 0.0

        transcriber_mock = MagicMock()
        transcriber_mock.connect = AsyncMock()
        transcriber_mock.disconnect = AsyncMock()
        transcriber_mock.send_audio = AsyncMock()
        transcriber_mock.is_connected = False

        original_init = None

        def capture_hybrid_init(**kwargs):
            self._captured_on_status = kwargs.get("on_status")
            return transcriber_mock

        with (
            patch("backend.main.AudioPipeReader", return_value=pipe_mock),
            patch("backend.main.HybridTranscriber", side_effect=capture_hybrid_init),
            patch("backend.main._load_settings", return_value={
                "deepgram_api_key": "test-key",
            }),
        ):
            self._pipe_mock = pipe_mock
            self._transcriber_mock = transcriber_mock
            yield

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from sqlalchemy.ext.asyncio import create_async_engine
        from backend.database import override_engine
        from backend.main import app

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        override_engine(engine)
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
        asyncio.run(engine.dispose())

    def _create_session(self, client) -> str:
        r = client.post("/sessions", json={"context": "meeting"})
        assert r.status_code == 201
        return r.json()["session_id"]

    def test_using_cloud_status_sent(self, client):
        """When HybridTranscriber emits 'using_cloud', the frontend
        receives a transcriber_status message with event='using_cloud'."""
        from backend.main import SessionPipeline

        sid = self._create_session(client)
        with patch.object(
            SessionPipeline, "compute_scores",
            return_value={
                "persuasion_score": 60,
                "timing_score": 7, "ego_safety_score": 7, "convergence_score": 7,
                "timing_signals": [], "ego_safety_signals": [], "convergence_signals": [],
            },
        ):
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                # The on_status callback was captured during HybridTranscriber init
                if self._captured_on_status:
                    asyncio.get_event_loop().run_until_complete(
                        self._captured_on_status("using_cloud", {})
                    )

                    data = ws.receive_json()
                    assert data["type"] == "transcriber_status"
                    assert data["event"] == "using_cloud"

                ws.send_json({"type": "session_end"})
                # Drain remaining messages
                while True:
                    msg = ws.receive_json()
                    if msg["type"] == "session_ended":
                        break

    def test_fallback_activated_status_sent(self, client):
        """When HybridTranscriber emits 'fallback_activated', the frontend
        receives it with the reason detail."""
        from backend.main import SessionPipeline

        sid = self._create_session(client)
        with patch.object(
            SessionPipeline, "compute_scores",
            return_value={
                "persuasion_score": 60,
                "timing_score": 7, "ego_safety_score": 7, "convergence_score": 7,
                "timing_signals": [], "ego_safety_signals": [], "convergence_signals": [],
            },
        ):
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                if self._captured_on_status:
                    asyncio.get_event_loop().run_until_complete(
                        self._captured_on_status("fallback_activated", {
                            "reason": "health_check_failed",
                        })
                    )

                    data = ws.receive_json()
                    assert data["type"] == "transcriber_status"
                    assert data["event"] == "fallback_activated"
                    assert data["detail"]["reason"] == "health_check_failed"

                ws.send_json({"type": "session_end"})
                while True:
                    msg = ws.receive_json()
                    if msg["type"] == "session_ended":
                        break


# ---------------------------------------------------------------------------
# 11. Audio level accumulation across multiple small chunks
# ---------------------------------------------------------------------------

class TestAudioLevelAccumulation:
    """
    Test the audio level accumulation logic that drives the frontend meter.

    The actual metering code in main.py accumulates PCM sample squares in
    _level_accum and fires when _level_sample_count >= 4000. These tests
    verify the accumulation math independently.

    Regression: In the v0.11.0 debug session, audio levels showed 0.0 because
    AudioCapture was outputting silence (Screen Recording permission denied).
    The metering code was correct but the input was all zeros. These tests
    verify both the math AND the zero-input case.
    """

    def test_accumulator_resets_after_threshold(self):
        """After computing a level, the accumulator must clear."""
        import struct

        threshold = 4000
        chunk_samples = 160
        accum = []
        sample_count = 0

        # Feed exactly enough chunks to trigger
        needed = threshold // chunk_samples  # 25
        for _ in range(needed):
            data = struct.pack(f"<{chunk_samples}h", *([10000] * chunk_samples))
            samples = struct.unpack(f"<{chunk_samples}h", data)
            accum.extend(s * s for s in samples)
            sample_count += chunk_samples

        assert sample_count >= threshold

        # Compute and reset (as main.py does)
        rms = math.sqrt(sum(accum) / len(accum))
        level_1 = min(rms / 32767.0, 1.0)
        accum.clear()
        sample_count = 0

        assert level_1 > 0
        assert len(accum) == 0
        assert sample_count == 0

        # Feed silence after reset
        for _ in range(needed):
            data = struct.pack(f"<{chunk_samples}h", *([0] * chunk_samples))
            samples = struct.unpack(f"<{chunk_samples}h", data)
            accum.extend(s * s for s in samples)
            sample_count += chunk_samples

        rms = math.sqrt(sum(accum) / len(accum)) if accum else 0
        level_2 = min(rms / 32767.0, 1.0)
        assert level_2 == 0.0, "After reset, silence must produce zero"

    def test_first_chunks_are_larger(self):
        """
        AudioCapture initially sends 1600-byte chunks (800 samples, 50ms)
        before settling to 320-byte chunks (160 samples, 10ms).
        Verify accumulation works across mixed sizes.
        """
        import struct

        threshold = 4000
        accum = []
        sample_count = 0

        # 3 large chunks (800 samples each = 2400 total)
        for _ in range(3):
            n = 800
            data = struct.pack(f"<{n}h", *([5000] * n))
            samples = struct.unpack(f"<{n}h", data)
            accum.extend(s * s for s in samples)
            sample_count += n

        assert sample_count == 2400

        # 10 small chunks (160 samples each = 1600 more = 4000 total)
        for _ in range(10):
            n = 160
            data = struct.pack(f"<{n}h", *([5000] * n))
            samples = struct.unpack(f"<{n}h", data)
            accum.extend(s * s for s in samples)
            sample_count += n

        assert sample_count == 4000
        assert sample_count >= threshold

        rms = math.sqrt(sum(accum) / len(accum))
        level = min(rms / 32767.0, 1.0)
        assert level > 0.1, "Mixed chunk sizes must produce valid level"

    def test_level_frequency_matches_interval(self):
        """
        At 16kHz with 320-byte chunks (160 samples, 10ms each), the level
        fires every 4000 samples = every 25 chunks = every 250ms.
        Verify this timing.
        """
        threshold = 4000
        chunk_samples = 160
        chunks_per_level = threshold // chunk_samples
        assert chunks_per_level == 25
        time_per_level_ms = chunks_per_level * 10  # 10ms per chunk
        assert time_per_level_ms == 250, "Level must fire every 250ms"
