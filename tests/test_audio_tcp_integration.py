"""End-to-end integration: raw TCP client → AudioTcpServer → AudioTcpReader."""
from __future__ import annotations

import asyncio

import pytest

from backend.audio import AudioTcpReader
from backend.audio_tcp_server import (
    AudioTcpServer,
    HANDSHAKE_MAGIC,
    STREAM_TAG_MIC,
    STREAM_TAG_SYSTEM,
)


def _bound_port(server: AudioTcpServer) -> int:
    """Return the ephemeral port the server bound to."""
    assert server._server is not None
    return server._server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_end_to_end_both_streams() -> None:
    server = AudioTcpServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        port = _bound_port(server)
        sys_chunks: list[bytes] = []
        mic_chunks: list[bytes] = []

        async def on_sys(data: bytes) -> None:
            sys_chunks.append(data)

        async def on_mic(data: bytes) -> None:
            mic_chunks.append(data)

        sys_reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_sys,
        )
        mic_reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_MIC, on_audio_chunk=on_mic,
        )
        await sys_reader.start()
        await mic_reader.start()
        try:
            _, w_sys = await asyncio.open_connection("127.0.0.1", port)
            _, w_mic = await asyncio.open_connection("127.0.0.1", port)
            try:
                w_sys.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))
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
            finally:
                for w in (w_sys, w_mic):
                    w.close()
                    try:
                        await w.wait_closed()
                    except Exception:
                        pass
        finally:
            await sys_reader.stop()
            await mic_reader.stop()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_client_reconnect_resumes_stream() -> None:
    server = AudioTcpServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        port = _bound_port(server)
        received: list[bytes] = []

        async def on_chunk(data: bytes) -> None:
            received.append(data)

        reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
        )
        await reader.start()
        try:
            # First client
            _, w1 = await asyncio.open_connection("127.0.0.1", port)
            try:
                w1.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))
                w1.write(b"FIRST")
                await w1.drain()
                await asyncio.sleep(0.1)
            finally:
                w1.close()
                try:
                    await w1.wait_closed()
                except Exception:
                    pass
            await asyncio.sleep(0.1)

            # Second client (simulating Swift restart)
            _, w2 = await asyncio.open_connection("127.0.0.1", port)
            try:
                w2.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))
                w2.write(b"SECOND")
                await w2.drain()
                await asyncio.sleep(0.1)

                flat = b"".join(received)
                assert b"FIRST" in flat
                assert b"SECOND" in flat
            finally:
                w2.close()
                try:
                    await w2.wait_closed()
                except Exception:
                    pass
        finally:
            await reader.stop()
    finally:
        await server.stop()
