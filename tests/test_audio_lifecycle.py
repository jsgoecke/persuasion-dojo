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
