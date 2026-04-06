"""
Tests for the Transcriber protocol and type exports.

Phase 0: Verify protocol extraction is correct and backward-compatible.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.transcriber_protocol import (
    ErrorCallback,
    StatusCallback,
    Transcriber,
    UtteranceCallback,
)


# ---------------------------------------------------------------------------
# Minimal concrete implementation for protocol testing
# ---------------------------------------------------------------------------

class _StubTranscriber:
    """Minimal implementation to verify the protocol shape."""

    def __init__(self) -> None:
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def send_audio(self, data: bytes) -> None:
        pass

    async def disconnect(self) -> None:
        self._connected = False

    async def finalize(self) -> None:
        pass

    @property
    def is_connected(self) -> bool:
        return self._connected


# ---------------------------------------------------------------------------
# Protocol satisfaction
# ---------------------------------------------------------------------------

class TestTranscriberProtocol:
    """Verify the Transcriber protocol is correctly defined and usable."""

    def test_stub_satisfies_protocol(self):
        stub = _StubTranscriber()
        assert isinstance(stub, Transcriber)

    def test_deepgram_satisfies_protocol(self):
        """DeepgramTranscriber should satisfy the Transcriber protocol."""
        from backend.transcription import DeepgramTranscriber

        async def _noop(s, t, f, st, en):
            pass

        client = DeepgramTranscriber(
            api_key="test-key",
            on_utterance=_noop,
            _connect_fn=lambda *a, **k: None,
        )
        assert isinstance(client, Transcriber)

    def test_protocol_is_runtime_checkable(self):
        assert isinstance(_StubTranscriber(), Transcriber)
        assert not isinstance("not a transcriber", Transcriber)
        assert not isinstance(42, Transcriber)

    @pytest.mark.asyncio
    async def test_stub_connect_disconnect_lifecycle(self):
        stub = _StubTranscriber()
        assert not stub.is_connected
        await stub.connect()
        assert stub.is_connected
        await stub.disconnect()
        assert not stub.is_connected

    @pytest.mark.asyncio
    async def test_stub_send_audio_does_not_raise(self):
        stub = _StubTranscriber()
        await stub.connect()
        await stub.send_audio(b"\x00" * 1600)

    @pytest.mark.asyncio
    async def test_stub_finalize_does_not_raise(self):
        stub = _StubTranscriber()
        await stub.connect()
        await stub.finalize()


# ---------------------------------------------------------------------------
# Backward-compatible re-exports from transcription.py
# ---------------------------------------------------------------------------

class TestBackwardCompatExports:
    """Verify that importing types from backend.transcription still works."""

    def test_utterance_callback_reexported(self):
        from backend.transcription import UtteranceCallback as UC
        assert UC is UtteranceCallback

    def test_error_callback_reexported(self):
        from backend.transcription import ErrorCallback as EC
        assert EC is ErrorCallback
