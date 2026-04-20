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
