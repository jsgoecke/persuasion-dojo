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
