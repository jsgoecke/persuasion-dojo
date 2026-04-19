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
