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
