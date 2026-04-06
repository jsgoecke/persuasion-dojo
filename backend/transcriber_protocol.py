"""
Transcriber protocol — shared interface for all transcription backends.

Any transcriber (Deepgram, Moonshine, hybrid) implements this protocol so that
the session pipeline can treat them interchangeably.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Callback types (canonical location)
# ---------------------------------------------------------------------------

UtteranceCallback = Callable[
    [str, str, bool, float, float],   # speaker_id, text, is_final, start_s, end_s
    Awaitable[None],
]

ErrorCallback = Callable[[Exception], Awaitable[None]]

StatusCallback = Callable[
    [str, dict],   # event_name, detail
    Awaitable[None],
]


# ---------------------------------------------------------------------------
# Transcriber protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Transcriber(Protocol):
    """
    Minimal interface for a streaming audio transcriber.

    Implementations: DeepgramTranscriber, MoonshineTranscriber, HybridTranscriber.
    """

    async def connect(self) -> None:
        """Open the transcription stream. Idempotent."""
        ...

    async def send_audio(self, data: bytes) -> None:
        """Enqueue a chunk of raw PCM audio for transcription."""
        ...

    async def disconnect(self) -> None:
        """Close the transcription stream and release resources."""
        ...

    async def finalize(self) -> None:
        """Flush buffered audio and force processing of pending results."""
        ...

    @property
    def is_connected(self) -> bool:
        """True when the transcriber is actively processing audio."""
        ...
