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


async def _aclose_writer(w: asyncio.StreamWriter) -> None:
    w.close()
    try:
        await w.wait_closed()
    except Exception:
        pass


@pytest.mark.asyncio
async def test_end_to_end_both_streams() -> None:
    server = AudioTcpServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        port = _bound_port(server)
        sys_payload = b"\x01\x02" * 64
        mic_payload = b"\x03\x04" * 64
        received_sys = bytearray()
        received_mic = bytearray()
        sys_done = asyncio.Event()
        mic_done = asyncio.Event()

        async def on_sys(data: bytes) -> None:
            received_sys.extend(data)
            if len(received_sys) >= len(sys_payload):
                sys_done.set()

        async def on_mic(data: bytes) -> None:
            received_mic.extend(data)
            if len(received_mic) >= len(mic_payload):
                mic_done.set()

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
                w_sys.write(sys_payload)
                w_mic.write(mic_payload)
                await w_sys.drain()
                await w_mic.drain()

                await asyncio.wait_for(sys_done.wait(), timeout=2.0)
                await asyncio.wait_for(mic_done.wait(), timeout=2.0)

                assert bytes(received_sys) == sys_payload
                assert bytes(received_mic) == mic_payload
            finally:
                await _aclose_writer(w_sys)
                await _aclose_writer(w_mic)
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
        first_payload = b"FIRST"
        second_payload = b"SECOND"
        expected = first_payload + second_payload
        received = bytearray()
        all_done = asyncio.Event()

        async def on_chunk(data: bytes) -> None:
            received.extend(data)
            if len(received) >= len(expected):
                all_done.set()

        reader = AudioTcpReader(
            server=server, stream_tag=STREAM_TAG_SYSTEM, on_audio_chunk=on_chunk,
        )
        await reader.start()
        try:
            _, w1 = await asyncio.open_connection("127.0.0.1", port)
            try:
                w1.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))
                w1.write(first_payload)
                await w1.drain()
                # Wait until FIRST has fully arrived before disconnecting.
                async def _wait_first() -> None:
                    while len(received) < len(first_payload):
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(_wait_first(), timeout=2.0)
            finally:
                await _aclose_writer(w1)

            _, w2 = await asyncio.open_connection("127.0.0.1", port)
            try:
                w2.write(bytes([HANDSHAKE_MAGIC, STREAM_TAG_SYSTEM]))
                w2.write(second_payload)
                await w2.drain()
                await asyncio.wait_for(all_done.wait(), timeout=2.0)
                assert bytes(received) == expected
            finally:
                await _aclose_writer(w2)
        finally:
            await reader.stop()
    finally:
        await server.stop()
