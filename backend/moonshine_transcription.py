"""
Moonshine local transcription client.

Architecture
────────────
                    bytes (PCM 16-bit LE mono 16 kHz)
  audio.py ──────────────────► MoonshineTranscriber
                                      │
                           moonshine-voice (local model)
                                      │
                              TranscriptEvent callbacks
                                      │
                              on_utterance callback
                                      │
                   ┌──────────────────▼──────────────────┐
                   │  {speaker_id, text, is_final,        │
                   │   start_s, end_s}                    │
                   └──────────────────────────────────────┘
                                      │
                              SessionPipeline (main.py)

Moonshine v2 runs entirely on-device (CPU, no GPU required).
Model is lazy-loaded on first connect() and cached at module level.

Audio format: 16-bit signed LE mono 16 kHz PCM (same as Deepgram path).
Moonshine expects float32 [-1.0, 1.0] samples — we convert on input.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from typing import Any, Callable

from backend.transcriber_protocol import StatusCallback, UtteranceCallback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level model cache (survives across sessions)
# ---------------------------------------------------------------------------

_MODEL_CACHE: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# MoonshineTranscriber
# ---------------------------------------------------------------------------

class MoonshineTranscriber:
    """
    Local ASR using Moonshine v2 streaming.

    Conforms to the Transcriber protocol (see backend/transcriber_protocol.py).
    Wraps moonshine-voice's Transcriber + listener-based event API.

    Parameters
    ----------
    on_utterance:
        Async callback invoked for every recognised utterance.
        Signature: ``async def cb(speaker_id, text, is_final, start_s, end_s)``
    on_status:
        Optional async callback for status events.
    model_arch:
        Moonshine model architecture (default: MEDIUM_STREAMING).
    language:
        Language code (default: "en").
    diarize:
        Enable speaker diarization (uses Moonshine built-in, default False).
    """

    def __init__(
        self,
        *,
        on_utterance: UtteranceCallback,
        on_status: StatusCallback | None = None,
        model_arch: str = "MEDIUM_STREAMING",
        language: str = "en",
        diarize: bool = False,
        _transcriber_factory: Callable | None = None,
    ) -> None:
        self._on_utterance = on_utterance
        self._on_status = on_status
        self._model_arch = model_arch
        self._language = language
        self._diarize = diarize
        self._transcriber_factory = _transcriber_factory

        self._transcriber: Any = None
        self._stream: Any = None
        self._connected = False
        self._session_start: float = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None

        # Track the current line text for detecting completion
        self._current_text: str = ""
        self._line_start_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API (Transcriber protocol)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Load the Moonshine model and start the transcriber."""
        if self._connected:
            return

        self._loop = asyncio.get_event_loop()
        self._session_start = time.monotonic()

        if self._transcriber_factory is not None:
            # Test injection
            self._transcriber = self._transcriber_factory()
            self._stream = self._transcriber  # tests use a flat fake
        else:
            # Lazy-load model in thread pool to avoid blocking the event loop
            self._transcriber = await asyncio.to_thread(self._load_model)
            self._stream = self._transcriber.create_stream(update_interval=0.5)

        self._stream.add_listener(self._on_transcript_event)
        self._stream.start()
        self._connected = True

        await self._emit_status("connected", {"backend": "moonshine"})
        logger.info("MoonshineTranscriber connected (model=%s)", self._model_arch)

    async def send_audio(self, data: bytes) -> None:
        """
        Feed a chunk of raw PCM audio to Moonshine.

        Converts 16-bit signed LE PCM to float32 [-1.0, 1.0] as Moonshine expects.
        """
        if not self._connected or not data or self._stream is None:
            return

        # Convert PCM int16 LE → float32 normalized
        n_samples = len(data) // 2
        if n_samples == 0:
            return

        try:
            samples_int = struct.unpack(f"<{n_samples}h", data[:n_samples * 2])
            samples_float = [s / 32768.0 for s in samples_int]
        except struct.error:
            return

        try:
            self._stream.add_audio(samples_float, 16000)
        except Exception as exc:
            logger.warning("Moonshine add_audio error: %s", exc)

    async def disconnect(self) -> None:
        """Stop the transcriber and release resources."""
        if not self._connected:
            return
        self._connected = False

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.remove_all_listeners()
            except Exception as exc:
                logger.warning("Moonshine stream stop error: %s", exc)

        # Flush any pending text as a final utterance
        if self._current_text.strip():
            elapsed = time.monotonic() - self._session_start
            try:
                if self._loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        self._on_utterance(
                            "speaker_0",
                            self._current_text.strip(),
                            True,
                            self._line_start_time,
                            elapsed,
                        ),
                        self._loop,
                    )
            except Exception:
                pass
            self._current_text = ""

        if self._transcriber is not None:
            try:
                self._transcriber.close()
            except Exception:
                pass

        self._stream = None
        self._transcriber = None
        logger.info("MoonshineTranscriber disconnected")

    async def finalize(self) -> None:
        """Force processing of buffered audio."""
        if self._stream is not None and self._connected:
            try:
                self._stream.update_transcription()
            except Exception as exc:
                logger.warning("Moonshine finalize error: %s", exc)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _load_model(self) -> Any:
        """Load or retrieve cached Moonshine model. Runs in thread pool."""
        cache_key = f"{self._language}:{self._model_arch}"
        if cache_key in _MODEL_CACHE:
            logger.info("Moonshine: reusing cached model (%s)", cache_key)
            return _MODEL_CACHE[cache_key]

        from moonshine_voice import ModelArch, get_model_for_language
        from moonshine_voice.transcriber import Transcriber as MVTranscriber

        arch = getattr(ModelArch, self._model_arch)
        model_path, resolved_arch = get_model_for_language(self._language, arch)

        logger.info("Moonshine: loading model %s from %s", self._model_arch, model_path)
        transcriber = MVTranscriber(
            model_path=str(model_path),
            model_arch=resolved_arch,
        )

        _MODEL_CACHE[cache_key] = transcriber
        logger.info("Moonshine: model loaded and cached (%s)", cache_key)
        return transcriber

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _on_transcript_event(self, event: Any) -> None:
        """
        Called by moonshine-voice on transcript updates (from model thread).

        Maps TranscriptEvent to our UtteranceCallback format.
        Schedules async callback on the event loop.
        """
        if self._loop is None or not self._connected:
            return

        line = event.line
        text = line.text if hasattr(line, "text") else ""
        if not text or not text.strip():
            return

        elapsed = time.monotonic() - self._session_start

        # Determine speaker
        if self._diarize and hasattr(line, "speaker_id") and line.has_speaker_id:
            speaker_id = f"speaker_{line.speaker_index}"
        else:
            speaker_id = "speaker_0"

        if line.is_new:
            # New line started
            self._line_start_time = elapsed
            self._current_text = text
            is_final = False
        elif hasattr(line, "is_complete") and line.is_complete:
            # Line completed
            self._current_text = ""
            is_final = True
        elif line.is_updated and line.has_text_changed:
            # Interim update
            self._current_text = text
            is_final = False
        else:
            # No text change, skip
            return

        try:
            asyncio.run_coroutine_threadsafe(
                self._on_utterance(
                    speaker_id,
                    text.strip(),
                    is_final,
                    self._line_start_time,
                    elapsed,
                ),
                self._loop,
            )
        except Exception:
            pass

    async def _emit_status(self, event: str, detail: dict) -> None:
        """Fire the on_status callback if registered."""
        if self._on_status is not None:
            try:
                await self._on_status(event, detail)
            except Exception:
                pass
