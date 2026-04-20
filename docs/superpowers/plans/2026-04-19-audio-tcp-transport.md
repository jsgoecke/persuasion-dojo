# Audio TCP Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the named-pipe (FIFO) audio transport between the Swift ScreenCaptureKit binary and the Python backend with a loopback TCP transport so the host Swift binary can stream audio into a Docker-containerized backend.

**Architecture:** Backend is the TCP server (`AudioTcpServer`) that listens on `127.0.0.1:$AUDIO_TCP_PORT` (default `9090`). Swift opens two TCP connections (system + mic); the first 2 bytes of each connection are a handshake (`0xAD` magic + stream tag `0x01`/`0x02`). After the handshake, each connection carries raw 16-kHz mono Int16LE PCM — identical bytes to the old FIFOs. FIFO code is deleted.

**Tech Stack:** Python 3.12 + `asyncio.start_server`, Swift (POSIX sockets via Darwin), Electron (TypeScript), pytest, SwiftPM XCTest, Vitest.

**Spec:** `docs/superpowers/specs/2026-04-19-audio-tcp-transport-design.md`

---

## Task map

- Task 1 — `AudioTcpServer` skeleton: start/stop, bind, accept loop
- Task 2 — Handshake validation (magic + stream tag)
- Task 3 — Reader registration + byte routing
- Task 4 — Pending-connection parking + drain-on-register + park timeout
- Task 5 — Duplicate stream-tag rejection
- Task 6 — `AudioTcpReader` replacing `AudioPipeReader` in `backend/audio.py`
- Task 7 — Port existing audio-lifecycle tests to the TCP transport
- Task 8 — FastAPI `lifespan` wiring + `AUDIO_TCP_PORT` env var
- Task 9 — Swap `AudioPipeReader` → `AudioTcpReader` in `backend/main.py`
- Task 10 — End-to-end integration test
- Task 11 — SwiftPM test target scaffolding
- Task 12 — Swift `TcpStreamWriter` (TDD)
- Task 13 — Swift `main.swift` rewrite; delete `PipeWriter.swift`
- Task 14 — Electron spawn env forwarding + Vitest unit test
- Task 15 — Docker compose + `.env.example` + `CLAUDE.md`

Each task ends with a commit. No task spans more than a handful of files.

---

## Task 1: `AudioTcpServer` skeleton — start/stop + accept loop

**Files:**
- Create: `backend/audio_tcp_server.py`
- Create: `tests/test_audio_tcp_server.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_audio_tcp_server.py`:

```python
"""Tests for AudioTcpServer — listener lifecycle, handshake, routing, parking."""
from __future__ import annotations

import asyncio
import socket

import pytest

from backend.audio_tcp_server import AudioTcpServer


def _pick_port() -> int:
    """Bind a throwaway socket to get a free loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_server_starts_and_stops_cleanly() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    assert server.is_running is True

    # Port is now bound; connecting should succeed
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.close()
    await writer.wait_closed()

    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_stop_is_idempotent() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    await server.stop()
    await server.stop()  # must not raise


@pytest.mark.asyncio
async def test_server_rebinds_after_stop() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    await server.stop()

    server2 = AudioTcpServer(host="127.0.0.1", port=port)
    await server2.start()
    assert server2.is_running is True
    await server2.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audio_tcp_server.py -v`
Expected: `ModuleNotFoundError: No module named 'backend.audio_tcp_server'`

- [ ] **Step 3: Write the minimal implementation**

Create `backend/audio_tcp_server.py`:

```python
"""TCP audio transport server — loopback listener for Swift capture clients.

Swift opens two connections per session (system + mic). Each connection sends
a 2-byte handshake (0xAD magic + 0x01/0x02 stream tag), then streams raw
16-kHz mono Int16LE PCM. The server routes payload bytes to the
``asyncio.Queue`` registered by the session's ``AudioTcpReader``.

This module owns only the listener and per-connection demultiplexing. Silence
watchdog and session lifecycle live in ``AudioTcpReader`` (``backend/audio.py``).
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 9090


class AudioTcpServer:
    """Loopback TCP listener for Swift ScreenCaptureKit audio streams."""

    def __init__(self, *, host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> None:
        self._host = host
        self._port = port
        self._server: asyncio.base_events.Server | None = None

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client, host=self._host, port=self._port
        )
        logger.info("AudioTcpServer listening on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        logger.info("AudioTcpServer stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Handshake + routing added in Task 2 / Task 3.
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_audio_tcp_server.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/audio_tcp_server.py tests/test_audio_tcp_server.py
git commit -m "$(cat <<'EOF'
feat: scaffold AudioTcpServer listener lifecycle

Adds start/stop/is_running surface on a loopback asyncio TCP listener.
Handshake validation, reader registration, and parking arrive in subsequent
tasks. Tests cover clean start/stop, idempotent stop, and rebind after stop.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Handshake validation

**Files:**
- Modify: `backend/audio_tcp_server.py`
- Modify: `tests/test_audio_tcp_server.py`

Protocol: 2 bytes per connection, exactly once, before any payload.
`byte[0] == 0xAD` (magic), `byte[1] ∈ {0x01, 0x02}` (stream tag).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audio_tcp_server.py`:

```python
# ── Handshake validation ────────────────────────────────────────────────────

MAGIC = 0xAD
TAG_SYSTEM = 0x01
TAG_MIC = 0x02


async def _connect(port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", port)


@pytest.mark.asyncio
async def test_handshake_wrong_magic_closes_connection() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        r, w = await _connect(port)
        w.write(bytes([0x00, TAG_SYSTEM]))
        await w.drain()
        # Server must close — EOF within 1s
        data = await asyncio.wait_for(r.read(1), timeout=1.0)
        assert data == b""
        w.close()
        await w.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_handshake_unknown_stream_tag_closes_connection() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        r, w = await _connect(port)
        w.write(bytes([MAGIC, 0x99]))
        await w.drain()
        data = await asyncio.wait_for(r.read(1), timeout=1.0)
        assert data == b""
        w.close()
        await w.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_handshake_truncated_closes_connection() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        r, w = await _connect(port)
        w.write(bytes([MAGIC]))  # only 1 of 2 bytes, then close
        await w.drain()
        w.close()
        await w.wait_closed()
        # Server must notice EOF and unwind — no assertion on read side,
        # the test just needs to complete without hanging.
    finally:
        await server.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audio_tcp_server.py -v -k handshake`
Expected: `test_handshake_wrong_magic_closes_connection` and `test_handshake_unknown_stream_tag_closes_connection` FAIL (server accepts everything today). Truncated may pass by coincidence.

- [ ] **Step 3: Implement handshake validation**

Replace `_handle_client` and add constants in `backend/audio_tcp_server.py`:

```python
# Wire protocol constants
HANDSHAKE_MAGIC = 0xAD
STREAM_TAG_SYSTEM = 0x01
STREAM_TAG_MIC = 0x02
_VALID_STREAM_TAGS = frozenset({STREAM_TAG_SYSTEM, STREAM_TAG_MIC})


async def _read_handshake(reader: asyncio.StreamReader) -> int | None:
    """Read 2-byte handshake; return stream tag on success, None on invalid/EOF."""
    try:
        header = await reader.readexactly(2)
    except asyncio.IncompleteReadError:
        return None
    if header[0] != HANDSHAKE_MAGIC:
        return None
    tag = header[1]
    if tag not in _VALID_STREAM_TAGS:
        return None
    return tag
```

Update `_handle_client` in the same file:

```python
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        tag = await _read_handshake(reader)
        if tag is None:
            logger.warning("AudioTcpServer: rejected handshake from %s", peer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return
        # Routing added in Task 3; for now, close.
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_audio_tcp_server.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/audio_tcp_server.py tests/test_audio_tcp_server.py
git commit -m "$(cat <<'EOF'
feat: validate 2-byte handshake on AudioTcpServer connections

Connections with the wrong magic byte, unknown stream tag, or truncated
handshake are closed immediately. Valid handshakes pass through to the
per-connection handler (routing lands in the next commit).

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Reader registration + byte routing

A `register(stream_tag)` returns an `asyncio.Queue[bytes]` that receives every payload chunk from the matching connection. `unregister(stream_tag)` removes the reader and (if the client is still connected) closes its socket.

**Files:**
- Modify: `backend/audio_tcp_server.py`
- Modify: `tests/test_audio_tcp_server.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audio_tcp_server.py`:

```python
# ── Reader registration + byte routing ─────────────────────────────────────


@pytest.mark.asyncio
async def test_payload_bytes_route_to_registered_queue_system() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        queue = server.register(TAG_SYSTEM)

        r, w = await _connect(port)
        w.write(bytes([MAGIC, TAG_SYSTEM]))
        w.write(b"\x01\x02\x03\x04")
        await w.drain()

        chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert chunk == b"\x01\x02\x03\x04"

        w.close()
        await w.wait_closed()
    finally:
        server.unregister(TAG_SYSTEM)
        await server.stop()


@pytest.mark.asyncio
async def test_payload_bytes_route_to_correct_tag_only() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        sys_q = server.register(TAG_SYSTEM)
        mic_q = server.register(TAG_MIC)

        r1, w1 = await _connect(port)
        w1.write(bytes([MAGIC, TAG_SYSTEM]))
        w1.write(b"SYS-")
        await w1.drain()

        r2, w2 = await _connect(port)
        w2.write(bytes([MAGIC, TAG_MIC]))
        w2.write(b"MIC-")
        await w2.drain()

        assert await asyncio.wait_for(sys_q.get(), timeout=1.0) == b"SYS-"
        assert await asyncio.wait_for(mic_q.get(), timeout=1.0) == b"MIC-"
        assert sys_q.empty()
        assert mic_q.empty()

        for w in (w1, w2):
            w.close()
            await w.wait_closed()
    finally:
        server.unregister(TAG_SYSTEM)
        server.unregister(TAG_MIC)
        await server.stop()


@pytest.mark.asyncio
async def test_unregister_closes_active_socket() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        server.register(TAG_SYSTEM)
        r, w = await _connect(port)
        w.write(bytes([MAGIC, TAG_SYSTEM]))
        w.write(b"\x00\x00")
        await w.drain()
        await asyncio.sleep(0.05)

        server.unregister(TAG_SYSTEM)

        data = await asyncio.wait_for(r.read(1), timeout=1.0)
        assert data == b""
        w.close()
        await w.wait_closed()
    finally:
        await server.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audio_tcp_server.py -v -k "route or unregister"`
Expected: `AttributeError: 'AudioTcpServer' object has no attribute 'register'` on all three.

- [ ] **Step 3: Implement registration + routing**

Update `backend/audio_tcp_server.py`. Add constants and a small `_Connection` record near `HANDSHAKE_MAGIC`:

```python
_READ_CHUNK_BYTES = 4096


class _Connection:
    """A handshake-validated TCP connection with its assigned stream tag."""

    __slots__ = ("tag", "writer")

    def __init__(self, tag: int, writer: asyncio.StreamWriter) -> None:
        self.tag = tag
        self.writer = writer
```

Replace the `AudioTcpServer` body with:

```python
class AudioTcpServer:
    """Loopback TCP listener for Swift ScreenCaptureKit audio streams."""

    def __init__(self, *, host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> None:
        self._host = host
        self._port = port
        self._server: asyncio.base_events.Server | None = None
        self._queues: dict[int, asyncio.Queue[bytes]] = {}
        self._active: dict[int, _Connection] = {}

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client, host=self._host, port=self._port
        )
        logger.info("AudioTcpServer listening on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        logger.info("AudioTcpServer stopped")

    def register(self, stream_tag: int) -> asyncio.Queue[bytes]:
        """Register a consumer for ``stream_tag`` and return its chunk queue."""
        if stream_tag not in _VALID_STREAM_TAGS:
            raise ValueError(f"invalid stream tag: {stream_tag!r}")
        if stream_tag in self._queues:
            raise RuntimeError(f"stream tag {stream_tag} already registered")
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._queues[stream_tag] = queue
        return queue

    def unregister(self, stream_tag: int) -> None:
        """Drop the consumer for ``stream_tag`` and close any active socket."""
        self._queues.pop(stream_tag, None)
        conn = self._active.pop(stream_tag, None)
        if conn is not None:
            try:
                conn.writer.close()
            except Exception:
                pass

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        tag = await _read_handshake(reader)
        if tag is None:
            logger.warning("AudioTcpServer: rejected handshake from %s", peer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return

        queue = self._queues.get(tag)
        if queue is None:
            # Task 4 adds parking; for now drop unregistered connections.
            logger.info("AudioTcpServer: no reader for tag=%d, closing", tag)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return

        conn = _Connection(tag=tag, writer=writer)
        self._active[tag] = conn
        try:
            while True:
                data = await reader.read(_READ_CHUNK_BYTES)
                if not data:
                    return
                await queue.put(data)
        except (ConnectionResetError, asyncio.CancelledError):
            return
        finally:
            if self._active.get(tag) is conn:
                self._active.pop(tag, None)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_audio_tcp_server.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/audio_tcp_server.py tests/test_audio_tcp_server.py
git commit -m "$(cat <<'EOF'
feat: route AudioTcpServer payload bytes to registered per-tag queues

register(tag) returns an asyncio.Queue that receives every payload chunk
from the corresponding stream; unregister(tag) drops the queue and closes
the active socket. No cross-talk between system and mic tags.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Pending-connection parking + drain-on-register + park timeout

If a client connects before any reader is registered for its tag, the server parks the connection for up to 30 s, buffering bytes. When a reader registers, buffered bytes drain to the new queue and live bytes follow. If the park timer expires with no reader, the connection is closed.

**Files:**
- Modify: `backend/audio_tcp_server.py`
- Modify: `tests/test_audio_tcp_server.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audio_tcp_server.py`:

```python
# ── Pending connection parking ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_connection_drains_to_reader_after_register() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port, park_timeout_s=5.0)
    await server.start()
    try:
        r, w = await _connect(port)
        w.write(bytes([MAGIC, TAG_SYSTEM]))
        w.write(b"PRE-REG-BYTES")
        await w.drain()
        await asyncio.sleep(0.1)  # let the server park the connection

        queue = server.register(TAG_SYSTEM)

        # Parked bytes must drain as the first message
        first = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert first == b"PRE-REG-BYTES"

        # And new bytes flow live
        w.write(b"LIVE")
        await w.drain()
        second = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert second == b"LIVE"

        w.close()
        await w.wait_closed()
    finally:
        server.unregister(TAG_SYSTEM)
        await server.stop()


@pytest.mark.asyncio
async def test_pending_connection_closes_after_park_timeout() -> None:
    port = _pick_port()
    # Short timeout for test
    server = AudioTcpServer(host="127.0.0.1", port=port, park_timeout_s=0.2)
    await server.start()
    try:
        r, w = await _connect(port)
        w.write(bytes([MAGIC, TAG_SYSTEM]))
        await w.drain()

        # No register() — wait past the timeout
        data = await asyncio.wait_for(r.read(1), timeout=1.0)
        assert data == b""
        w.close()
        await w.wait_closed()
    finally:
        await server.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audio_tcp_server.py -v -k "pending"`
Expected: `test_pending_connection_drains_to_reader_after_register` fails (no park — server closes immediately). `test_pending_connection_closes_after_park_timeout` may time out.

- [ ] **Step 3: Implement parking**

Update `backend/audio_tcp_server.py`. Add the `park_timeout_s` constructor arg + a `_Pending` helper, and rework `_handle_client` to park instead of drop when no reader is registered.

Add near the `_Connection` class:

```python
_DEFAULT_PARK_TIMEOUT_S = 30.0


class _Pending:
    """Buffers bytes from a handshake-validated connection awaiting a reader."""

    __slots__ = ("tag", "writer", "buffer", "attached_event", "queue")

    def __init__(self, tag: int, writer: asyncio.StreamWriter) -> None:
        self.tag = tag
        self.writer = writer
        self.buffer: list[bytes] = []
        self.attached_event = asyncio.Event()
        self.queue: asyncio.Queue[bytes] | None = None
```

Update `AudioTcpServer.__init__`:

```python
    def __init__(
        self,
        *,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        park_timeout_s: float = _DEFAULT_PARK_TIMEOUT_S,
    ) -> None:
        self._host = host
        self._port = port
        self._park_timeout_s = park_timeout_s
        self._server: asyncio.base_events.Server | None = None
        self._queues: dict[int, asyncio.Queue[bytes]] = {}
        self._active: dict[int, _Connection] = {}
        self._pending: dict[int, _Pending] = {}
```

Update `register` to attach pending connections on the fly:

```python
    def register(self, stream_tag: int) -> asyncio.Queue[bytes]:
        if stream_tag not in _VALID_STREAM_TAGS:
            raise ValueError(f"invalid stream tag: {stream_tag!r}")
        if stream_tag in self._queues:
            raise RuntimeError(f"stream tag {stream_tag} already registered")
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._queues[stream_tag] = queue

        pending = self._pending.pop(stream_tag, None)
        if pending is not None:
            # Drain buffered bytes first, preserving order
            for chunk in pending.buffer:
                queue.put_nowait(chunk)
            pending.queue = queue
            pending.attached_event.set()
        return queue
```

Replace `_handle_client`:

```python
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        tag = await _read_handshake(reader)
        if tag is None:
            logger.warning("AudioTcpServer: rejected handshake from %s", peer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return

        queue = self._queues.get(tag)
        if queue is None:
            # Park until a reader registers, the client closes, or timeout.
            if tag in self._pending:
                logger.info(
                    "AudioTcpServer: tag=%d already parked; closing newer conn", tag
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return
            pending = _Pending(tag=tag, writer=writer)
            self._pending[tag] = pending
            try:
                queue = await self._park_until_attached(reader, pending)
            finally:
                self._pending.pop(tag, None)
            if queue is None:
                # Timeout or EOF while parked
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return

        conn = _Connection(tag=tag, writer=writer)
        self._active[tag] = conn
        try:
            while True:
                data = await reader.read(_READ_CHUNK_BYTES)
                if not data:
                    return
                await queue.put(data)
        except (ConnectionResetError, asyncio.CancelledError):
            return
        finally:
            if self._active.get(tag) is conn:
                self._active.pop(tag, None)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _park_until_attached(
        self, reader: asyncio.StreamReader, pending: _Pending
    ) -> asyncio.Queue[bytes] | None:
        """Buffer bytes until a reader attaches or the park timeout elapses."""
        async def _buffer_reader() -> None:
            while not pending.attached_event.is_set():
                try:
                    data = await reader.read(_READ_CHUNK_BYTES)
                except (ConnectionResetError, asyncio.CancelledError):
                    return
                if not data:
                    return
                pending.buffer.append(data)

        buffer_task = asyncio.create_task(_buffer_reader())
        attach_task = asyncio.create_task(pending.attached_event.wait())
        try:
            done, _ = await asyncio.wait(
                {buffer_task, attach_task},
                timeout=self._park_timeout_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not attach_task.done():
                attach_task.cancel()
            if not buffer_task.done():
                buffer_task.cancel()
        if pending.attached_event.is_set():
            return pending.queue
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_audio_tcp_server.py -v`
Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/audio_tcp_server.py tests/test_audio_tcp_server.py
git commit -m "$(cat <<'EOF'
feat: park AudioTcpServer connections until a reader registers

Connections arriving before register(tag) are buffered for up to
park_timeout_s (default 30s). On register, buffered bytes drain to the
new queue and live bytes follow. On timeout, the parked connection is
closed.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Duplicate stream-tag rejection

If a client connects with `TAG_SYSTEM` while an active `TAG_SYSTEM` connection already exists, close the newer connection. (Active-vs-pending case was already covered in Task 4.)

**Files:**
- Modify: `backend/audio_tcp_server.py`
- Modify: `tests/test_audio_tcp_server.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audio_tcp_server.py`:

```python
# ── Duplicate stream-tag rejection ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_tag_while_active_closes_newer_connection() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        queue = server.register(TAG_SYSTEM)

        # First connection attaches as active
        r1, w1 = await _connect(port)
        w1.write(bytes([MAGIC, TAG_SYSTEM]))
        w1.write(b"FIRST")
        await w1.drain()
        assert await asyncio.wait_for(queue.get(), timeout=1.0) == b"FIRST"

        # Second connection with same tag must be rejected
        r2, w2 = await _connect(port)
        w2.write(bytes([MAGIC, TAG_SYSTEM]))
        w2.write(b"SHOULD-NOT-ARRIVE")
        await w2.drain()
        data = await asyncio.wait_for(r2.read(1), timeout=1.0)
        assert data == b""

        # First connection still delivers bytes to the queue
        w1.write(b"STILL-HERE")
        await w1.drain()
        assert await asyncio.wait_for(queue.get(), timeout=1.0) == b"STILL-HERE"

        for w in (w1, w2):
            w.close()
            await w.wait_closed()
    finally:
        server.unregister(TAG_SYSTEM)
        await server.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audio_tcp_server.py -v -k duplicate`
Expected: FAIL — the server currently overwrites `self._active[tag]` with the second connection.

- [ ] **Step 3: Implement duplicate rejection**

In `_handle_client`, just after the `queue = self._queues.get(tag)` lookup and before `conn = _Connection(...)`, add a duplicate check. Replace the block starting at `conn = _Connection(tag=tag, writer=writer)` with:

```python
        if tag in self._active:
            logger.info(
                "AudioTcpServer: duplicate tag=%d while active, closing newer conn", tag
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return

        conn = _Connection(tag=tag, writer=writer)
        self._active[tag] = conn
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_audio_tcp_server.py -v`
Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/audio_tcp_server.py tests/test_audio_tcp_server.py
git commit -m "$(cat <<'EOF'
feat: reject duplicate stream-tag connections on AudioTcpServer

If a second client connects with a tag already served by an active
connection, close the newer one. Prevents double-attach races (e.g. during
Electron hot-reload).

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `AudioTcpReader` replaces `AudioPipeReader`

Rewrite `backend/audio.py` so it houses a new `AudioTcpReader` with the same public surface as the old `AudioPipeReader`. The reader registers with a shared `AudioTcpServer`, pulls bytes from its queue, invokes `on_audio_chunk`, and fires `on_silence_timeout` if no non-empty chunk arrives within `silence_timeout_s`.

**Files:**
- Modify: `backend/audio.py` (full rewrite of module body)
- Modify: `tests/test_audio.py` (create; focused unit tests for `AudioTcpReader` in isolation — server-integration tests follow in Task 7)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audio.py`:

```python
"""Unit tests for AudioTcpReader — lifecycle, callback plumbing, watchdog."""
from __future__ import annotations

import asyncio

import pytest

from backend.audio import AudioTcpReader
from backend.audio_tcp_server import STREAM_TAG_SYSTEM


class _FakeServer:
    """Minimal AudioTcpServer stand-in — hands back a real asyncio.Queue."""

    def __init__(self) -> None:
        self.queues: dict[int, asyncio.Queue[bytes]] = {}
        self.unregistered: list[int] = []

    def register(self, tag: int) -> asyncio.Queue[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue()
        self.queues[tag] = q
        return q

    def unregister(self, tag: int) -> None:
        self.unregistered.append(tag)


@pytest.mark.asyncio
async def test_chunks_flow_to_callback() -> None:
    server = _FakeServer()
    received: list[bytes] = []

    async def on_chunk(data: bytes) -> None:
        received.append(data)

    reader = AudioTcpReader(
        server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
    )
    await reader.start()
    await server.queues[STREAM_TAG_SYSTEM].put(b"hello")
    await asyncio.sleep(0.05)
    assert received == [b"hello"]
    await reader.stop()


@pytest.mark.asyncio
async def test_stop_is_idempotent() -> None:
    server = _FakeServer()

    async def on_chunk(_: bytes) -> None: ...

    reader = AudioTcpReader(
        server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
    )
    await reader.start()
    await reader.stop()
    await reader.stop()
    assert server.unregistered.count(STREAM_TAG_SYSTEM) >= 1


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    server = _FakeServer()

    async def on_chunk(_: bytes) -> None: ...

    reader = AudioTcpReader(
        server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
    )
    await reader.start()
    first_queue = server.queues[STREAM_TAG_SYSTEM]
    await reader.start()  # must not re-register
    assert server.queues[STREAM_TAG_SYSTEM] is first_queue
    await reader.stop()


@pytest.mark.asyncio
async def test_silence_watchdog_fires_after_timeout() -> None:
    server = _FakeServer()
    fired = asyncio.Event()

    async def on_chunk(_: bytes) -> None: ...

    async def on_silence() -> None:
        fired.set()

    reader = AudioTcpReader(
        server=server,
        stream_tag=STREAM_TAG_SYSTEM,
        on_audio_chunk=on_chunk,
        on_silence_timeout=on_silence,
        silence_timeout_s=0.2,
    )
    await reader.start()
    # Send one chunk to arm the watchdog (it does not fire before first chunk)
    await server.queues[STREAM_TAG_SYSTEM].put(b"\x00\x00")
    await asyncio.wait_for(fired.wait(), timeout=1.0)
    await reader.stop()


@pytest.mark.asyncio
async def test_silence_watchdog_does_not_fire_before_first_chunk() -> None:
    server = _FakeServer()
    fired = False

    async def on_chunk(_: bytes) -> None: ...

    async def on_silence() -> None:
        nonlocal fired
        fired = True

    reader = AudioTcpReader(
        server=server,
        stream_tag=STREAM_TAG_SYSTEM,
        on_audio_chunk=on_chunk,
        on_silence_timeout=on_silence,
        silence_timeout_s=0.1,
    )
    await reader.start()
    await asyncio.sleep(0.3)
    assert fired is False
    await reader.stop()


@pytest.mark.asyncio
async def test_silence_watchdog_resets_on_new_chunk() -> None:
    server = _FakeServer()
    fire_count = 0

    async def on_chunk(_: bytes) -> None: ...

    async def on_silence() -> None:
        nonlocal fire_count
        fire_count += 1

    reader = AudioTcpReader(
        server=server,
        stream_tag=STREAM_TAG_SYSTEM,
        on_audio_chunk=on_chunk,
        on_silence_timeout=on_silence,
        silence_timeout_s=0.2,
    )
    await reader.start()
    q = server.queues[STREAM_TAG_SYSTEM]
    await q.put(b"\x01\x01")
    await asyncio.sleep(0.1)  # still within timeout
    await q.put(b"\x02\x02")  # resets timer
    await asyncio.sleep(0.1)  # still within (new) timeout
    assert fire_count == 0
    # Now let it fire
    await asyncio.sleep(0.25)
    assert fire_count == 1
    await reader.stop()


@pytest.mark.asyncio
async def test_last_audio_time_tracks_most_recent_chunk() -> None:
    server = _FakeServer()

    async def on_chunk(_: bytes) -> None: ...

    reader = AudioTcpReader(
        server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
    )
    await reader.start()
    assert reader.last_audio_time == 0.0
    await server.queues[STREAM_TAG_SYSTEM].put(b"\x00")
    await asyncio.sleep(0.05)
    assert reader.last_audio_time > 0.0
    await reader.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audio.py -v`
Expected: `ImportError: cannot import name 'AudioTcpReader' from 'backend.audio'`.

- [ ] **Step 3: Rewrite `backend/audio.py`**

Replace the entire contents of `backend/audio.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_audio.py tests/test_audio_tcp_server.py -v`
Expected: all `test_audio.py` tests PASS, all `test_audio_tcp_server.py` tests PASS.

Note: existing `tests/test_audio_lifecycle.py` imports `AudioPipeReader` and will fail. That file is rewritten in Task 7.

- [ ] **Step 5: Commit**

```bash
git add backend/audio.py tests/test_audio.py
git commit -m "$(cat <<'EOF'
feat: replace AudioPipeReader with AudioTcpReader

Drains bytes from an AudioTcpServer queue (one per stream tag), invokes
on_audio_chunk, and fires on_silence_timeout if no non-empty chunk
arrives within silence_timeout_s. Public surface mirrors the old
AudioPipeReader; FIFO plumbing is gone.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Port `test_audio_lifecycle.py` to the TCP transport

`tests/test_audio_lifecycle.py` was tied to FIFO semantics (pipe path, `mkfifo`, stale-pipe handling). The behaviors worth preserving are: reader state machine, multi-session stop→start→stop cycles, silence watchdog, audio callback plumbing. Everything tied to pipe files is deleted.

**Files:**
- Modify: `tests/test_audio_lifecycle.py` (full rewrite — keep the filename so CI wiring stays)

- [ ] **Step 1: Rewrite the test file as failing tests against the new API**

Replace the entire contents of `tests/test_audio_lifecycle.py`:

```python
"""
Audio pipeline lifecycle tests (TCP transport).

Covers the behaviours that have historically broken "Go Live":
  1. Reader state machine: start/stop, double-start, double-stop
  2. Multi-session lifecycle: stop → start → stop cycles
  3. Silence watchdog: fires after timeout, resets on audio, doesn't fire early
  4. Audio callback plumbing: chunks forwarded in order
  5. Session-end signaling: stopping the reader unregisters from the server
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from backend.audio import AudioTcpReader
from backend.audio_tcp_server import (
    AudioTcpServer,
    HANDSHAKE_MAGIC,
    STREAM_TAG_MIC,
    STREAM_TAG_SYSTEM,
)


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Reader state machine ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reader_double_start_is_safe() -> None:
    server = AudioTcpServer(host="127.0.0.1", port=_pick_port())
    await server.start()
    try:
        async def on_chunk(_: bytes) -> None: ...

        reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
        )
        await reader.start()
        await reader.start()  # must not raise
        assert reader.is_running is True
        await reader.stop()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_reader_double_stop_is_safe() -> None:
    server = AudioTcpServer(host="127.0.0.1", port=_pick_port())
    await server.start()
    try:
        async def on_chunk(_: bytes) -> None: ...

        reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
        )
        await reader.start()
        await reader.stop()
        await reader.stop()  # must not raise
        assert reader.is_running is False
    finally:
        await server.stop()


# ── Multi-session lifecycle ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_start_stop_cycle_works() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        received: list[bytes] = []

        async def on_chunk(data: bytes) -> None:
            received.append(data)

        for _ in range(3):
            reader = AudioTcpReader(
                server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
            )
            await reader.start()

            # Client connects, streams one chunk, disconnects
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))
            w.write(b"CHUNK")
            await w.drain()
            await asyncio.sleep(0.1)
            w.close()
            await w.wait_closed()

            await reader.stop()

        assert received.count(b"CHUNK") == 3
    finally:
        await server.stop()


# ── Silence watchdog ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watchdog_fires_when_client_disconnects() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        fired = asyncio.Event()

        async def on_chunk(_: bytes) -> None: ...
        async def on_silence() -> None: fired.set()

        reader = AudioTcpReader(
            server=server,
            stream_tag=STREAM_TAG_SYSTEM,
            on_audio_chunk=on_chunk,
            on_silence_timeout=on_silence,
            silence_timeout_s=0.25,
        )
        await reader.start()

        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))
        w.write(b"\x00\x00")
        await w.drain()
        await asyncio.sleep(0.05)
        w.close()
        await w.wait_closed()

        await asyncio.wait_for(fired.wait(), timeout=1.5)
        await reader.stop()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_watchdog_does_not_fire_with_continuous_audio() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        fired = False

        async def on_chunk(_: bytes) -> None: ...
        async def on_silence() -> None:
            nonlocal fired
            fired = True

        reader = AudioTcpReader(
            server=server,
            stream_tag=STREAM_TAG_SYSTEM,
            on_audio_chunk=on_chunk,
            on_silence_timeout=on_silence,
            silence_timeout_s=0.3,
        )
        await reader.start()

        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))
        await w.drain()
        # Send a chunk every 100ms for 500ms (never exceeds 300ms silence)
        for _ in range(5):
            w.write(b"\x00\x00")
            await w.drain()
            await asyncio.sleep(0.1)
        assert fired is False
        w.close()
        await w.wait_closed()
        await reader.stop()
    finally:
        await server.stop()


# ── Audio callback plumbing ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chunks_forwarded_in_order() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        received: list[bytes] = []

        async def on_chunk(data: bytes) -> None:
            received.append(data)

        reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_MIC, on_audio_chunk=on_chunk,
        )
        await reader.start()

        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_MIC]))
        for i in range(5):
            w.write(bytes([i]) * 16)
            await w.drain()
            await asyncio.sleep(0.02)

        await asyncio.sleep(0.1)
        flat = b"".join(received)
        for i in range(5):
            assert bytes([i]) * 16 in flat
        # First 16 bytes must be the first-sent chunk
        assert flat[:16] == bytes([0]) * 16

        w.close()
        await w.wait_closed()
        await reader.stop()
    finally:
        await server.stop()


# ── Session-end signaling ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_unregisters_from_server() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        async def on_chunk(_: bytes) -> None: ...

        reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
        )
        await reader.start()
        await reader.stop()

        # A new reader on the same tag must now register without RuntimeError
        reader2 = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
        )
        await reader2.start()
        await reader2.stop()
    finally:
        await server.stop()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_audio.py tests/test_audio_lifecycle.py tests/test_audio_tcp_server.py -v`
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_audio_lifecycle.py
git commit -m "$(cat <<'EOF'
test: port audio lifecycle tests to the TCP transport

Replaces FIFO-specific assertions (pipe path, mkfifo, stale pipe) with
TCP equivalents. State machine, multi-session cycles, silence watchdog,
callback ordering, and session-end signaling are all preserved.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: FastAPI `lifespan` wiring + `AUDIO_TCP_PORT` env var

The `AudioTcpServer` is a process-scoped resource. Start it in the FastAPI `lifespan` context, stop it at shutdown, expose it via `app.state.audio_tcp_server`, and read `AUDIO_TCP_PORT` from the environment (default 9090).

**Files:**
- Modify: `backend/main.py`
- Modify: `tests/test_main.py` (append new tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py` (if a more appropriate file exists in the repo, place them alongside existing `lifespan` tests; otherwise add to `test_main.py`):

```python
# ── AudioTcpServer lifespan integration ─────────────────────────────────────

import os

import pytest
from httpx import AsyncClient

from backend.audio_tcp_server import AudioTcpServer


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_audio_tcp_server(monkeypatch) -> None:
    # Bind to an ephemeral port to avoid collisions
    monkeypatch.setenv("AUDIO_TCP_PORT", "0")
    from backend.main import app

    async with AsyncClient(app=app, base_url="http://test") as client:
        # app.state is populated after lifespan startup
        server: AudioTcpServer = app.state.audio_tcp_server  # type: ignore[attr-defined]
        assert isinstance(server, AudioTcpServer)
        assert server.is_running is True

    # After the `async with` exits, lifespan shutdown has run
    assert server.is_running is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -v -k "lifespan_starts_and_stops_audio_tcp_server"`
Expected: `AttributeError: 'State' object has no attribute 'audio_tcp_server'`.

- [ ] **Step 3: Wire the server into `lifespan`**

Edit `backend/main.py`.

Add import near the existing audio import block:

```python
from backend.audio_tcp_server import AudioTcpServer
```

Add a constant near other defaults (`_DEFAULT_MIC_PIPE_PATH` area — that constant is removed in Task 9, but keep the ordering logical for now):

```python
_DEFAULT_AUDIO_TCP_PORT = 9090
```

Modify `lifespan` at `backend/main.py:550`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the database and audio TCP server; clean up on shutdown."""
    await init_db()
    async with get_db_session() as db:
        await _get_or_create_user(db)

    port = int(os.environ.get("AUDIO_TCP_PORT", _DEFAULT_AUDIO_TCP_PORT))
    audio_tcp_server = AudioTcpServer(host="127.0.0.1", port=port)
    await audio_tcp_server.start()
    app.state.audio_tcp_server = audio_tcp_server

    _background_tasks: set[asyncio.Task] = set()
    app.state.background_tasks = _background_tasks
    yield
    # ── Shutdown cleanup ──
    for task in list(_background_tasks):
        if not task.done():
            task.cancel()
    _background_tasks.clear()
    await audio_tcp_server.stop()
```

Delete the now-stale comment at `backend/main.py:560`:
```python
    # Pipe cleanup is owned by AudioPipeReader.stop() — do not duplicate here.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main.py -v -k lifespan`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_main.py
git commit -m "$(cat <<'EOF'
feat: start/stop AudioTcpServer in FastAPI lifespan

Server binds 127.0.0.1:$AUDIO_TCP_PORT (default 9090) on app startup and
stops on shutdown. Exposed via app.state.audio_tcp_server for the
WebSocket session handler (next commit).

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Swap `AudioPipeReader` → `AudioTcpReader` in `backend/main.py`

Remove the single-pipe fallback (TCP has no analog — Swift always opens both streams). Swap the two `AudioPipeReader` instantiations for `AudioTcpReader` bound to the shared server.

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Remove FIFO imports and constants**

Delete these lines from `backend/main.py`:

- Line `from backend.audio import AudioPipeReader` → replace with `from backend.audio import AudioTcpReader`
- Line `_DEFAULT_MIC_PIPE_PATH = "/tmp/persuasion_mic.pipe"` (around line 131) → delete
- Anywhere that reads/references `_DEFAULT_MIC_PIPE_PATH` — cleaned up below

- [ ] **Step 2: Update the WebSocket session handler**

At `backend/main.py:1828` (the block that decides `_dual_pipe_mode`), replace the whole dual/single-pipe construction with a TCP-based one.

Find and replace this block:

```python
    _dual_pipe_mode = os.path.exists(_DEFAULT_MIC_PIPE_PATH)

    _on_mic_chunk = _make_audio_chunk_handler(mic_transcriber, "mic", is_mic=True)
    # In single-pipe mode, system handler also meters audio levels
    _on_system_chunk = _make_audio_chunk_handler(
        system_transcriber, "system", is_mic=not _dual_pipe_mode,
    )

    # Dual readers: mic pipe (no silence watchdog) + system pipe (with watchdog)
    system_reader = AudioPipeReader(
        on_audio_chunk=_on_system_chunk,
        on_silence_timeout=_on_silence,
    )
    mic_reader: AudioPipeReader | None = None

    if _dual_pipe_mode:
        mic_reader = AudioPipeReader(
            pipe_path=_DEFAULT_MIC_PIPE_PATH,
            on_audio_chunk=_on_mic_chunk,
            on_silence_timeout=None,  # Mic silence is normal (user muted)
        )

    try:
        if mic_reader:
            # Start both readers concurrently
            await asyncio.gather(
                system_reader.start(),
                mic_reader.start(),
            )
        else:
            # Fallback: single-pipe mode (old Swift binary)
            logger.warning(
                "Mic pipe not found at %s — using single-pipe mode. "
                "Update AudioCapture binary for accurate speaker identification.",
                _DEFAULT_MIC_PIPE_PATH,
            )
            await system_reader.start()
    except Exception as exc:
        await ws.send_json({
            "type": "error",
            "message": f"Audio pipeline failed: {exc}",
        })
        await ws.close()
        _session_manager.remove(session_id)
        return
```

with:

```python
    from backend.audio_tcp_server import STREAM_TAG_MIC, STREAM_TAG_SYSTEM

    _on_mic_chunk = _make_audio_chunk_handler(mic_transcriber, "mic", is_mic=True)
    _on_system_chunk = _make_audio_chunk_handler(
        system_transcriber, "system", is_mic=False,
    )

    audio_tcp_server = app.state.audio_tcp_server
    system_reader = AudioTcpReader(
        server=audio_tcp_server,
        stream_tag=STREAM_TAG_SYSTEM,
        on_audio_chunk=_on_system_chunk,
        on_silence_timeout=_on_silence,
    )
    mic_reader = AudioTcpReader(
        server=audio_tcp_server,
        stream_tag=STREAM_TAG_MIC,
        on_audio_chunk=_on_mic_chunk,
        on_silence_timeout=None,  # Mic silence is normal (user muted)
    )

    try:
        await asyncio.gather(system_reader.start(), mic_reader.start())
    except Exception as exc:
        await ws.send_json({
            "type": "error",
            "message": f"Audio pipeline failed: {exc}",
        })
        await ws.close()
        _session_manager.remove(session_id)
        return
```

Leave the existing `try/finally` that calls `system_reader.stop()` / `mic_reader.stop()` untouched — the public surface is the same.

Also remove the comment block near `backend/main.py:1594`:
```python
    # ── Audio pipeline (dual-pipe: mic + system) ──────────────────────
```
and replace with:
```python
    # ── Audio pipeline (TCP: system tag 0x01, mic tag 0x02) ───────────
```

- [ ] **Step 3: Update the docstring on `websocket_session`**

At `backend/main.py:1344`, replace:
```
    Audio pipeline
    ──────────────
    On connect we start:
      AudioPipeReader  →  HybridTranscriber  →  on_utterance → _handle_utterance
    Both are stopped when the session ends or the connection closes.

    If AudioPipeReader's silence watchdog fires (Swift binary stopped writing),
    we send {"type": "swift_restart_needed"} so the Electron renderer can ask
    the main process to restart the capture binary.
```
with:
```
    Audio pipeline
    ──────────────
    On connect we register two AudioTcpReader instances (system + mic) with
    the process-scoped AudioTcpServer. Each reader drains its per-tag queue:
      AudioTcpReader  →  HybridTranscriber  →  on_utterance → _handle_utterance
    Both are stopped (and unregistered) when the session ends or the
    connection closes.

    If AudioTcpReader's silence watchdog fires (Swift client disconnected),
    we send {"type": "swift_restart_needed"} so the Electron renderer can
    ask the main process to restart the capture binary.
```

- [ ] **Step 4: Run the full backend suite**

Run: `pytest -m "not integration and not eval" --ignore=tests/test_pipeline_e2e.py --timeout=30 -q`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "$(cat <<'EOF'
feat: wire WebSocket session to AudioTcpReader

Swap both AudioPipeReader instances for AudioTcpReader bound to the
shared AudioTcpServer. Single-pipe fallback is gone — Swift always
opens both streams in the new transport.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: End-to-end integration test

Drive the full Python stack: start `AudioTcpServer` on an ephemeral port, start an `AudioTcpReader`, connect a raw TCP client, send handshake + PCM, assert the callback fired with the right bytes.

**Files:**
- Create: `tests/test_audio_tcp_integration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_audio_tcp_integration.py`:

```python
"""End-to-end integration: raw TCP client → AudioTcpServer → AudioTcpReader."""
from __future__ import annotations

import asyncio
import socket

import pytest

from backend.audio import AudioTcpReader
from backend.audio_tcp_server import (
    AudioTcpServer,
    HANDSHAKE_MAGIC,
    STREAM_TAG_MIC,
    STREAM_TAG_SYSTEM,
)


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_end_to_end_both_streams() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        sys_chunks: list[bytes] = []
        mic_chunks: list[bytes] = []

        async def on_sys(data: bytes) -> None: sys_chunks.append(data)
        async def on_mic(data: bytes) -> None: mic_chunks.append(data)

        sys_reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_sys,
        )
        mic_reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_MIC, on_audio_chunk=on_mic,
        )
        await sys_reader.start()
        await mic_reader.start()

        # Two concurrent clients, one per stream
        _, w_sys = await asyncio.open_connection("127.0.0.1", port)
        w_sys.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))

        _, w_mic = await asyncio.open_connection("127.0.0.1", port)
        w_mic.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_MIC]))

        sys_payload = b"\x01\x02" * 64
        mic_payload = b"\x03\x04" * 64
        w_sys.write(sys_payload)
        w_mic.write(mic_payload)
        await w_sys.drain()
        await w_mic.drain()
        await asyncio.sleep(0.2)

        assert b"".join(sys_chunks) == sys_payload
        assert b"".join(mic_chunks) == mic_payload

        for w in (w_sys, w_mic):
            w.close()
            await w.wait_closed()

        await sys_reader.stop()
        await mic_reader.stop()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_client_reconnect_resumes_stream() -> None:
    port = _pick_port()
    server = AudioTcpServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        received: list[bytes] = []

        async def on_chunk(data: bytes) -> None:
            received.append(data)

        reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
        )
        await reader.start()

        # First client
        _, w1 = await asyncio.open_connection("127.0.0.1", port)
        w1.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))
        w1.write(b"FIRST")
        await w1.drain()
        await asyncio.sleep(0.1)
        w1.close()
        await w1.wait_closed()
        await asyncio.sleep(0.1)

        # Second client (simulating Swift restart)
        _, w2 = await asyncio.open_connection("127.0.0.1", port)
        w2.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))
        w2.write(b"SECOND")
        await w2.drain()
        await asyncio.sleep(0.1)

        flat = b"".join(received)
        assert b"FIRST" in flat
        assert b"SECOND" in flat

        w2.close()
        await w2.wait_closed()
        await reader.stop()
    finally:
        await server.stop()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_audio_tcp_integration.py -v`
Expected: both tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_audio_tcp_integration.py
git commit -m "$(cat <<'EOF'
test: end-to-end AudioTcpServer ↔ AudioTcpReader

Raw TCP client exercises both stream tags concurrently and verifies
byte-for-byte delivery. Second test covers client reconnect after a
disconnect, mirroring a Swift crash / restart.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: SwiftPM test target scaffolding

Add a test target so Task 12 can drive the Swift `TcpStreamWriter` under `swift test`. This task only wires the harness with one trivial assertion; the real tests arrive in Task 12.

**Files:**
- Modify: `swift/AudioCapture/Package.swift`
- Create: `swift/AudioCapture/Tests/AudioCaptureTests/SmokeTests.swift`

Existing `Package.swift` has `.executableTarget(name: "AudioCapture", path: "Sources/AudioCapture", …)`. The test target can't depend directly on an executable, so we split the AudioCapture sources into a library target + a thin executable that imports it. This is one commit and keeps the runtime binary identical.

- [ ] **Step 1: Restructure Package.swift**

Replace `Package.swift`. (Keep existing linker/platform settings — the snippet below keeps the common ones; if your current file has more, preserve them verbatim in both targets.)

```swift
// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "AudioCapture",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "AudioCapture", targets: ["AudioCaptureCLI"]),
    ],
    targets: [
        .target(
            name: "AudioCaptureCore",
            path: "Sources/AudioCaptureCore",
            linkerSettings: [
                .linkedFramework("ScreenCaptureKit"),
                .linkedFramework("CoreAudio"),
                .linkedFramework("CoreMedia"),
                .linkedFramework("CoreGraphics"),
                .linkedFramework("AVFoundation"),
            ]
        ),
        .executableTarget(
            name: "AudioCaptureCLI",
            dependencies: ["AudioCaptureCore"],
            path: "Sources/AudioCaptureCLI"
        ),
        .testTarget(
            name: "AudioCaptureTests",
            dependencies: ["AudioCaptureCore"],
            path: "Tests/AudioCaptureTests"
        ),
    ]
)
```

- [ ] **Step 2: Move sources**

```bash
cd swift/AudioCapture
mkdir -p Sources/AudioCaptureCore Sources/AudioCaptureCLI Tests/AudioCaptureTests
# All library code moves to the Core target
git mv Sources/AudioCapture/AudioMixer.swift      Sources/AudioCaptureCore/
git mv Sources/AudioCapture/MicCapture.swift      Sources/AudioCaptureCore/
git mv Sources/AudioCapture/PipeWriter.swift      Sources/AudioCaptureCore/
git mv Sources/AudioCapture/ScreenAudioCapture.swift Sources/AudioCaptureCore/
# Entrypoint stays in the CLI target
git mv Sources/AudioCapture/main.swift            Sources/AudioCaptureCLI/
rmdir Sources/AudioCapture
```

Each file in `Sources/AudioCaptureCore/` that previously had no `public` modifier needs types/methods used by `main.swift` to become `public`. In this branch we only need `PipeWriter`'s public API to remain importable (it is deleted in Task 13 anyway); others can gain `public` scoped as used by `main.swift`. Since `main.swift` is the sole external consumer, add `public` to:

- `AudioMixer` class + `init`, `start`, `stop`, and whatever `main.swift` calls.
- `MicCapture` class + `init`, `start(mixer:)`, `stop`.
- `ScreenAudioCapture` class + `init(mixer:)`, `start`, `stop`, `checkPermission()`.
- `CaptureError` enum (if referenced).
- `PipeWriter` class + `init`, `start`, `write(_:)`, `stop`.

Use `Grep` to confirm every symbol `main.swift` references, then add `public` only to those.

- [ ] **Step 3: Add a smoke test**

Create `swift/AudioCapture/Tests/AudioCaptureTests/SmokeTests.swift`:

```swift
import XCTest
@testable import AudioCaptureCore

final class SmokeTests: XCTestCase {
    func testAudioCaptureCoreImports() {
        // Trivial: if the module imports, the test target is wired correctly.
        XCTAssertTrue(true)
    }
}
```

- [ ] **Step 4: Verify build + test**

```bash
cd swift/AudioCapture
swift build
swift test
```

Expected: build succeeds, `SmokeTests.testAudioCaptureCoreImports` PASSes.

- [ ] **Step 5: Update build artifact paths**

Anything referencing `.build/debug/AudioCapture` / `.build/release/AudioCapture` still works — SwiftPM produces an executable named `AudioCapture` from the `AudioCaptureCLI` target. Verify:

```bash
ls swift/AudioCapture/.build/debug/AudioCapture
```

Expected: the binary exists. If not, ensure `product(name: "AudioCapture")` matches in Package.swift.

- [ ] **Step 6: Commit**

```bash
git add swift/AudioCapture/Package.swift \
        swift/AudioCapture/Sources \
        swift/AudioCapture/Tests
git commit -m "$(cat <<'EOF'
chore: split AudioCapture into core library + CLI + test target

Introduces AudioCaptureCore library target so tests can @testable import
it. AudioCaptureCLI stays as the executable (unchanged runtime binary).
Smoke test verifies the harness is wired.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Swift `TcpStreamWriter` (TDD)

Replace `PipeWriter` with `TcpStreamWriter`, which connects to `127.0.0.1:<port>`, sends the 2-byte handshake, then streams bytes. Public API matches `PipeWriter`: `start()`, `write(_:)`, `stop()`.

**Files:**
- Create: `swift/AudioCapture/Sources/AudioCaptureCore/TcpStreamWriter.swift`
- Create: `swift/AudioCapture/Tests/AudioCaptureTests/TcpStreamWriterTests.swift`

- [ ] **Step 1: Write the failing tests**

Create `swift/AudioCapture/Tests/AudioCaptureTests/TcpStreamWriterTests.swift`:

```swift
import XCTest
import Network
@testable import AudioCaptureCore

final class TcpStreamWriterTests: XCTestCase {

    /// Spins up an NWListener on an ephemeral port and exposes the first
    /// connection's received bytes through an async collector.
    final class LocalListener {
        let listener: NWListener
        var port: UInt16 { listener.port?.rawValue ?? 0 }
        private var received = Data()
        private let lock = NSLock()
        private let receivedExpectation: XCTestExpectation?

        init(expectedBytes: Int, waiter: XCTestExpectation?) throws {
            let params = NWParameters.tcp
            self.listener = try NWListener(using: params, on: .any)
            self.receivedExpectation = waiter
            let expected = expectedBytes
            listener.newConnectionHandler = { conn in
                conn.start(queue: .main)
                func receive() {
                    conn.receive(minimumIncompleteLength: 1, maximumLength: 4096) {
                        [weak self] data, _, isComplete, err in
                        guard let self = self else { return }
                        if let data = data, !data.isEmpty {
                            self.lock.lock()
                            self.received.append(data)
                            let done = self.received.count >= expected
                            self.lock.unlock()
                            if done { self.receivedExpectation?.fulfill() }
                        }
                        if isComplete || err != nil { return }
                        receive()
                    }
                }
                receive()
            }
        }

        func start() {
            listener.start(queue: .main)
        }

        func snapshot() -> Data {
            lock.lock(); defer { lock.unlock() }
            return received
        }

        func stop() {
            listener.cancel()
        }
    }

    func testHandshakeBytesAreSentFirst() throws {
        let exp = expectation(description: "received handshake")
        let srv = try LocalListener(expectedBytes: 2, waiter: exp)
        srv.start()
        // Wait briefly for the listener to publish a port
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))

        let writer = TcpStreamWriter(
            host: "127.0.0.1",
            port: srv.port,
            streamTag: 0x01,
        )
        writer.start()
        // No payload write — handshake alone should arrive
        wait(for: [exp], timeout: 2.0)

        let bytes = srv.snapshot()
        XCTAssertEqual(bytes[0], 0xAD)
        XCTAssertEqual(bytes[1], 0x01)

        writer.stop()
        srv.stop()
    }

    func testPayloadBytesFollowHandshake() throws {
        let exp = expectation(description: "received payload")
        let payload = Data([0x10, 0x20, 0x30, 0x40])
        let srv = try LocalListener(expectedBytes: 2 + payload.count, waiter: exp)
        srv.start()
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))

        let writer = TcpStreamWriter(
            host: "127.0.0.1",
            port: srv.port,
            streamTag: 0x02,
        )
        writer.start()
        // Give the connection a moment to complete before writing
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        writer.write(payload)

        wait(for: [exp], timeout: 2.0)

        let bytes = srv.snapshot()
        XCTAssertEqual(bytes[0], 0xAD)
        XCTAssertEqual(bytes[1], 0x02)
        XCTAssertEqual(bytes.subdata(in: 2..<bytes.count), payload)

        writer.stop()
        srv.stop()
    }

    func testWriteBeforeConnectIsDropped() throws {
        // No listener → connect fails → writes are no-ops, not crashes.
        let writer = TcpStreamWriter(
            host: "127.0.0.1",
            port: 1, // reserved, will refuse
            streamTag: 0x01,
        )
        writer.start()
        writer.write(Data([0x00, 0x00]))
        // Nothing to assert — test passes if no crash within 0.2s
        RunLoop.main.run(until: Date().addingTimeInterval(0.2))
        writer.stop()
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd swift/AudioCapture
swift test 2>&1 | head -40
```

Expected: compile error "cannot find 'TcpStreamWriter' in scope".

- [ ] **Step 3: Implement `TcpStreamWriter`**

Create `swift/AudioCapture/Sources/AudioCaptureCore/TcpStreamWriter.swift`:

```swift
import Foundation
import Darwin

/// Thread-safe writer that streams raw bytes to a TCP server after sending
/// a 2-byte handshake (0xAD magic + stream tag).
///
/// Public API matches the removed PipeWriter:
///   - start()              kicks off connection establishment
///   - write(_ data: Data)  enqueues bytes; safe from any thread
///   - stop()               closes the socket and drops pending writes
///
/// If the server is unreachable or the connection drops, the writer
/// reconnects with a 500 ms backoff. Writes issued while disconnected
/// are silently dropped (audio is realtime — buffering old bytes is
/// worse than dropping them).
public final class TcpStreamWriter {
    private static let handshakeMagic: UInt8 = 0xAD
    private static let reconnectDelayNs: UInt64 = 500_000_000 // 500 ms

    private let host: String
    private let port: UInt16
    private let streamTag: UInt8
    private let queue = DispatchQueue(label: "tcp.stream.writer", qos: .userInteractive)
    private var fd: Int32 = -1
    private var stopped = false

    public init(host: String, port: UInt16, streamTag: UInt8) {
        self.host = host
        self.port = port
        self.streamTag = streamTag
    }

    public func start() {
        queue.async { [self] in self.connectLoop() }
    }

    public func write(_ data: Data) {
        queue.async { [self] in
            guard fd >= 0 else { return }
            let ok = data.withUnsafeBytes { ptr -> Bool in
                guard let base = ptr.baseAddress, ptr.count > 0 else { return true }
                return Darwin.write(fd, base, ptr.count) >= 0
            }
            if !ok {
                fputs("TcpStreamWriter: write failed (errno \(errno)), reconnecting…\n",
                      stderr)
                Darwin.close(fd)
                fd = -1
                connectLoop()
            }
        }
    }

    public func stop() {
        queue.sync {
            stopped = true
            if fd >= 0 {
                Darwin.close(fd)
                fd = -1
            }
        }
    }

    // MARK: - Private

    private func connectLoop() {
        while !stopped {
            if connectOnce() { return }
            // Backoff before retry
            let deadline = DispatchTime.now() + .nanoseconds(Int(Self.reconnectDelayNs))
            _ = queue.sync {
                // Sleep while holding nothing; queue is released by sync returning
            }
            Thread.sleep(forTimeInterval: 0.5)
        }
    }

    /// Returns true on success; false means caller should back off and retry.
    private func connectOnce() -> Bool {
        let sock = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard sock >= 0 else { return false }

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        inet_pton(AF_INET, host, &addr.sin_addr)

        let connectRes = withUnsafePointer(to: &addr) { ptr -> Int32 in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(sock, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        if connectRes != 0 {
            Darwin.close(sock)
            return false
        }

        var handshake: [UInt8] = [Self.handshakeMagic, streamTag]
        let sent = handshake.withUnsafeBufferPointer { buf -> Int in
            Darwin.write(sock, buf.baseAddress, buf.count)
        }
        if sent != handshake.count {
            Darwin.close(sock)
            return false
        }
        fd = sock
        fputs("TcpStreamWriter: connected (tag \(streamTag))\n", stderr)
        return true
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd swift/AudioCapture
swift test
```

Expected: all `TcpStreamWriterTests` PASS (plus the smoke test).

- [ ] **Step 5: Commit**

```bash
git add swift/AudioCapture/Sources/AudioCaptureCore/TcpStreamWriter.swift \
        swift/AudioCapture/Tests/AudioCaptureTests/TcpStreamWriterTests.swift
git commit -m "$(cat <<'EOF'
feat: add TcpStreamWriter for audio transport

Connects to 127.0.0.1:<port>, sends 2-byte handshake (0xAD + tag), then
streams raw bytes. Reconnects with 500 ms backoff on failure. Public API
matches the old PipeWriter (start / write / stop). XCTest coverage for
handshake order, payload delivery, and connect-fail no-crash.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Swift `main.swift` rewrite; delete `PipeWriter.swift`

Replace the FIFO plumbing with `TcpStreamWriter` instances. Read `AUDIO_BACKEND_PORT` from the process env (default 9090).

**Files:**
- Modify: `swift/AudioCapture/Sources/AudioCaptureCLI/main.swift`
- Delete: `swift/AudioCapture/Sources/AudioCaptureCore/PipeWriter.swift`

- [ ] **Step 1: Rewrite `main.swift`**

Replace the entire contents of `swift/AudioCapture/Sources/AudioCaptureCLI/main.swift`:

```swift
import Foundation
import ScreenCaptureKit
import AVFoundation
import AudioCaptureCore

// ── Config ─────────────────────────────────────────────────────────────────

let envPort = ProcessInfo.processInfo.environment["AUDIO_BACKEND_PORT"]
let port: UInt16 = envPort.flatMap { UInt16($0) } ?? 9090
let host = "127.0.0.1"

fputs("AudioCapture: target \(host):\(port)\n", stderr)

// ── Signal handling ────────────────────────────────────────────────────────

signal(SIGPIPE, SIG_IGN)

let systemWriter = TcpStreamWriter(host: host, port: port, streamTag: 0x01)
let micWriter    = TcpStreamWriter(host: host, port: port, streamTag: 0x02)
let mixer        = AudioMixer(systemWriter: systemWriter, micWriter: micWriter)
let capture      = ScreenAudioCapture(mixer: mixer)
let micCapture   = MicCapture()

func handleShutdown() {
    Task {
        fputs("AudioCapture: shutting down…\n", stderr)
        micCapture.stop()
        await capture.stop()
        mixer.stop()
        systemWriter.stop()
        micWriter.stop()
        exit(0)
    }
}

let sigTermSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigTermSource.setEventHandler { handleShutdown() }
sigTermSource.resume()

let sigIntSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
sigIntSource.setEventHandler { handleShutdown() }
sigIntSource.resume()

signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)

// ── Permission check & start ───────────────────────────────────────────────

Task {
    fputs("AudioCapture: checking Screen Recording permission…\n", stderr)
    do {
        try await ScreenAudioCapture.checkPermission()
    } catch CaptureError.permissionDenied {
        fputs("AudioCapture: \(CaptureError.permissionDenied)\n", stderr)
        exit(2)
    } catch {
        fputs("AudioCapture: unexpected error checking permission: \(error)\n", stderr)
        exit(1)
    }
    fputs("AudioCapture: permission OK\n", stderr)

    systemWriter.start()
    micWriter.start()

    mixer.start()

    do {
        try await capture.start()
    } catch {
        fputs("AudioCapture: failed to start capture: \(error)\n", stderr)
        exit(1)
    }

    do {
        try micCapture.start(mixer: mixer)
    } catch {
        fputs("MicCapture: failed to start: \(error)\n", stderr)
    }
}

dispatchMain()
```

Notes:
- `AudioMixer` previously took two `PipeWriter` instances. Confirm its public `init` accepts any writer with the same interface. If not, either (a) change `AudioMixer.init` to take two closures `(Data) -> Void`, passing `systemWriter.write` / `micWriter.write`; or (b) define a shared `public protocol AudioSink { func write(_ data: Data) }` in `AudioCaptureCore` and have both `PipeWriter` (pre-delete) and `TcpStreamWriter` conform. Pick (a) — simpler and matches the current call site which only uses `write`.

- [ ] **Step 2: If needed, adjust `AudioMixer.init` to accept closures**

If Step 1 required the closure approach, edit `swift/AudioCapture/Sources/AudioCaptureCore/AudioMixer.swift`:

Find the current `init(systemWriter:micWriter:)` which stores `PipeWriter` references, replace property types with `private let writeSystem: (Data) -> Void` and `private let writeMic: (Data) -> Void`, and update `init`:

```swift
public init(
    systemWriter: TcpStreamWriter,
    micWriter: TcpStreamWriter
) {
    self.writeSystem = { [weak systemWriter] data in systemWriter?.write(data) }
    self.writeMic    = { [weak micWriter]    data in micWriter?.write(data) }
}
```

Everywhere `systemWriter.write(...)` / `micWriter.write(...)` is called in `AudioMixer`, substitute `writeSystem(...)` / `writeMic(...)`.

- [ ] **Step 3: Delete `PipeWriter.swift`**

```bash
rm swift/AudioCapture/Sources/AudioCaptureCore/PipeWriter.swift
```

- [ ] **Step 4: Verify build + tests**

```bash
cd swift/AudioCapture
swift build
swift test
```

Expected: build succeeds, all Swift tests PASS.

- [ ] **Step 5: Commit**

```bash
git add -A swift/AudioCapture
git commit -m "$(cat <<'EOF'
feat: route Swift audio capture through TCP instead of FIFOs

main.swift drops mkfifo/unlink, constructs two TcpStreamWriter instances
(system + mic), and reads AUDIO_BACKEND_PORT from env (default 9090).
AudioMixer switches to closure-based writers. PipeWriter.swift is deleted.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Electron — forward `AUDIO_BACKEND_PORT` on spawn + Vitest unit test

The Electron main process spawns the Swift binary (`frontend/overlay/src/main/index.ts:128`). Forward `AUDIO_BACKEND_PORT` so the child dials the same port the backend is listening on. Extract the spawn-env builder into a pure function so we can unit-test it.

**Files:**
- Modify: `frontend/overlay/src/main/index.ts`
- Create: `frontend/overlay/src/main/capture-env.ts`
- Create: `frontend/overlay/tests/capture-env.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/overlay/tests/capture-env.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { buildCaptureEnv } from "../src/main/capture-env";

describe("buildCaptureEnv", () => {
  it("forwards AUDIO_BACKEND_PORT from the parent env", () => {
    const env = buildCaptureEnv({ AUDIO_BACKEND_PORT: "9191" });
    expect(env.AUDIO_BACKEND_PORT).toBe("9191");
  });

  it("defaults AUDIO_BACKEND_PORT to 9090 when unset", () => {
    const env = buildCaptureEnv({});
    expect(env.AUDIO_BACKEND_PORT).toBe("9090");
  });

  it("coerces numeric AUDIO_TCP_PORT when AUDIO_BACKEND_PORT is absent", () => {
    const env = buildCaptureEnv({ AUDIO_TCP_PORT: "9292" });
    expect(env.AUDIO_BACKEND_PORT).toBe("9292");
  });

  it("AUDIO_BACKEND_PORT wins over AUDIO_TCP_PORT when both are set", () => {
    const env = buildCaptureEnv({
      AUDIO_BACKEND_PORT: "1111",
      AUDIO_TCP_PORT: "2222",
    });
    expect(env.AUDIO_BACKEND_PORT).toBe("1111");
  });

  it("preserves unrelated env vars", () => {
    const env = buildCaptureEnv({ PATH: "/usr/bin", HOME: "/tmp" });
    expect(env.PATH).toBe("/usr/bin");
    expect(env.HOME).toBe("/tmp");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend/overlay
npx vitest run tests/capture-env.test.ts
```

Expected: `Cannot find module '../src/main/capture-env'`.

- [ ] **Step 3: Implement `buildCaptureEnv`**

Create `frontend/overlay/src/main/capture-env.ts`:

```typescript
/**
 * Compute the environment the Swift AudioCapture child process should run under.
 *
 * Picks AUDIO_BACKEND_PORT from the parent env if set; otherwise falls back to
 * AUDIO_TCP_PORT (the backend's bind-port variable); otherwise defaults to 9090.
 */
export function buildCaptureEnv(
  parentEnv: NodeJS.ProcessEnv,
): NodeJS.ProcessEnv {
  const port =
    parentEnv.AUDIO_BACKEND_PORT ?? parentEnv.AUDIO_TCP_PORT ?? "9090";
  return {
    ...parentEnv,
    AUDIO_BACKEND_PORT: port,
  };
}
```

- [ ] **Step 4: Wire `buildCaptureEnv` into `spawnCapture`**

Edit `frontend/overlay/src/main/index.ts`. At the top, add:

```typescript
import { buildCaptureEnv } from "./capture-env";
```

In `spawnCapture` at `frontend/overlay/src/main/index.ts:128`, change:

```typescript
  captureProcess = spawn(CAPTURE_BINARY, [], {
    stdio: ["ignore", "ignore", "pipe"],
  });
```

to:

```typescript
  captureProcess = spawn(CAPTURE_BINARY, [], {
    stdio: ["ignore", "ignore", "pipe"],
    env: buildCaptureEnv(process.env),
  });
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd frontend/overlay
npx vitest run
```

Expected: all tests PASS (existing + new).

- [ ] **Step 6: Commit**

```bash
git add frontend/overlay/src/main/capture-env.ts \
        frontend/overlay/src/main/index.ts \
        frontend/overlay/tests/capture-env.test.ts
git commit -m "$(cat <<'EOF'
feat: forward AUDIO_BACKEND_PORT to the spawned Swift capture binary

spawnCapture now injects an env built by buildCaptureEnv, which prefers
AUDIO_BACKEND_PORT, falls back to AUDIO_TCP_PORT, and defaults to 9090.
Vitest unit tests cover all four branches plus passthrough of unrelated
vars.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Docker compose + `.env.example` + `CLAUDE.md`

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Publish the audio port in `docker-compose.yml`**

In `docker-compose.yml`, under the `backend` service's `ports:`, add:

```yaml
    ports:
      - "8000:8000"
      - "127.0.0.1:9090:9090"
```

- [ ] **Step 2: Document `AUDIO_TCP_PORT` in `.env.example`**

Append to `.env.example`:

```
# Port the backend binds for the Swift audio TCP transport (loopback only).
# Swift reads AUDIO_BACKEND_PORT with the same value; Electron forwards it
# automatically.
AUDIO_TCP_PORT=9090
```

- [ ] **Step 3: Update `CLAUDE.md` Docker section**

Replace the existing "Docker (backend only)" note in `CLAUDE.md`:

```markdown
### Docker (backend only)

````bash
cp .env.example .env           # first-time setup, then fill in API keys
docker compose up -d --build   # build image and start backend on :8000
docker compose logs -f backend # tail logs
curl localhost:8000/health     # smoke test
docker compose down            # stop; SQLite data persists in the named volume
````

The backend container now accepts live audio from the host Swift binary
over loopback TCP (port `9090` by default, override with `AUDIO_TCP_PORT`).
Run the Swift ScreenCaptureKit binary on the host and it will connect to
`127.0.0.1:9090`. The Electron overlay spawns the Swift binary and
forwards `AUDIO_BACKEND_PORT` automatically.

**Single-instance note:** `docker-compose.yml` sets `container_name:
persuasion-dojo-backend`, so only one instance of this stack can run at
a time on a given Docker host.
```

- [ ] **Step 4: Run the full backend + frontend + swift test suites one last time**

```bash
pytest -m "not integration and not eval" --ignore=tests/test_pipeline_e2e.py -q
cd frontend/overlay && npx vitest run && cd -
cd swift/AudioCapture && swift test && cd -
```

Expected: all three suites PASS.

- [ ] **Step 5: Manual E2E smoke (documented, not automated)**

```bash
docker compose up -d --build
# Launch Electron overlay in another terminal: cd frontend/overlay && npm run dev
# Overlay spawns AudioCapture (host), which dials 127.0.0.1:9090 into the container.
# Start a session via the overlay UI; confirm audio_level / final_transcript
# messages on the WebSocket.
```

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example CLAUDE.md
git commit -m "$(cat <<'EOF'
feat: publish audio TCP port in docker-compose and document env vars

docker-compose.yml publishes 127.0.0.1:9090:9090 so the host Swift
binary can reach the containerized backend. .env.example documents
AUDIO_TCP_PORT; CLAUDE.md's Docker section now describes the live-audio
workflow.

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

After all 15 tasks:

```bash
# Python
pytest -m "not integration and not eval" --ignore=tests/test_pipeline_e2e.py -v --cov=backend
# Frontend
cd frontend/overlay && npx vitest run && cd -
# Swift
cd swift/AudioCapture && swift test && cd -
# Manual E2E smoke
docker compose up -d --build
```

Expected:
- Python: all tests pass, coverage at/above existing threshold (CI fails <75%).
- Vitest: all tests pass.
- `swift test`: all XCTests pass.
- Manual E2E: overlay shows `final_transcript` and `coaching_prompt` messages
  when the user speaks.
