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

# Wire protocol constants
HANDSHAKE_MAGIC = 0xAD
STREAM_TAG_SYSTEM = 0x01
STREAM_TAG_MIC = 0x02
_VALID_STREAM_TAGS = frozenset({STREAM_TAG_SYSTEM, STREAM_TAG_MIC})

_READ_CHUNK_BYTES = 4096


class _Connection:
    """A handshake-validated TCP connection with its assigned stream tag."""

    __slots__ = ("tag", "writer")

    def __init__(self, tag: int, writer: asyncio.StreamWriter) -> None:
        self.tag = tag
        self.writer = writer


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
        except ConnectionResetError:
            return
        except asyncio.CancelledError:
            raise
        finally:
            if self._active.get(tag) is conn:
                self._active.pop(tag, None)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
