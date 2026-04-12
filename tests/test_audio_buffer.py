"""Tests for SessionPipeline audio ring buffer methods (F7).

Covers buffer_audio(), extract_audio_segment(), intro pinning,
stream separation (mic vs system), and edge cases.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from backend.main import SessionPipeline


@pytest.fixture
def pipeline():
    """Create a SessionPipeline with a mock coaching engine."""
    from unittest.mock import AsyncMock, MagicMock

    engine = MagicMock()
    engine.process = AsyncMock(return_value=None)
    engine.update_speaker_name = MagicMock()
    engine.user_archetype = None
    return SessionPipeline(
        session_id="test-session",
        user_id="test-user",
        coaching_engine=engine,
    )


class TestBufferAudio:
    def test_system_audio_goes_to_system_buffer(self, pipeline):
        """System audio chunks should land in _audio_buffer_system."""
        pipeline.buffer_audio(b"\x00\x00" * 800, is_mic=False)
        assert len(pipeline._audio_buffer_system) == 1
        assert len(pipeline._audio_buffer_mic) == 0

    def test_mic_audio_goes_to_mic_buffer(self, pipeline):
        """Mic audio chunks should land in _audio_buffer_mic."""
        pipeline.buffer_audio(b"\x00\x00" * 800, is_mic=True)
        assert len(pipeline._audio_buffer_mic) == 1
        assert len(pipeline._audio_buffer_system) == 0

    def test_start_time_set_on_first_chunk(self, pipeline):
        """_audio_start_time should be set on the first buffer_audio call."""
        assert pipeline._audio_start_time is None
        pipeline.buffer_audio(b"\x00\x00" * 800)
        assert pipeline._audio_start_time is not None

    def test_intro_buffer_pins_first_30s(self, pipeline):
        """System audio in the first 30s should be pinned in intro buffer."""
        base_time = time.monotonic()
        # Simulate 10 chunks within 30s window
        with patch("backend.main.time") as mock_time:
            mock_time.monotonic.return_value = base_time
            pipeline.buffer_audio(b"\x01\x00" * 800, is_mic=False)
            assert len(pipeline._audio_intro_buffer) == 1
            assert not pipeline._audio_intro_full

            # Jump to 31s — intro should be marked full
            mock_time.monotonic.return_value = base_time + 31.0
            pipeline.buffer_audio(b"\x02\x00" * 800, is_mic=False)
            assert pipeline._audio_intro_full

            # Further chunks should NOT be added to intro buffer
            intro_len = len(pipeline._audio_intro_buffer)
            mock_time.monotonic.return_value = base_time + 40.0
            pipeline.buffer_audio(b"\x03\x00" * 800, is_mic=False)
            assert len(pipeline._audio_intro_buffer) == intro_len

    def test_mic_audio_not_in_intro_buffer(self, pipeline):
        """Mic audio should never go to intro buffer."""
        pipeline.buffer_audio(b"\x00\x00" * 800, is_mic=True)
        assert len(pipeline._audio_intro_buffer) == 0

    def test_rolling_buffer_max_size(self, pipeline):
        """Rolling buffer should respect maxlen (6000 chunks)."""
        for i in range(6010):
            pipeline.buffer_audio(b"\x00\x00" * 800, is_mic=False)
        assert len(pipeline._audio_buffer_system) == 6000


class TestExtractAudioSegment:
    def test_empty_buffer_returns_none(self, pipeline):
        """Extracting from empty buffer should return None."""
        assert pipeline.extract_audio_segment(0.0, 5.0) is None

    def test_extract_within_range(self, pipeline):
        """Should extract chunks within the requested time range."""
        base_time = time.monotonic()
        with patch("backend.main.time") as mock_time:
            # Add 3 chunks at 0s, 0.05s, 0.1s
            for i in range(3):
                mock_time.monotonic.return_value = base_time + i * 0.05
                pipeline.buffer_audio(bytes([i]) * 1600, is_mic=False)

            # Extract all (0 to 0.15s)
            result = pipeline.extract_audio_segment(0.0, 0.15)
            assert result is not None
            assert len(result) == 3 * 1600

    def test_extract_partial_range(self, pipeline):
        """Should only return chunks within the requested range."""
        base_time = time.monotonic()
        with patch("backend.main.time") as mock_time:
            for i in range(10):
                mock_time.monotonic.return_value = base_time + i * 1.0
                pipeline.buffer_audio(bytes([i]) * 1600, is_mic=False)

            # Request only seconds 2-4 (should get chunks at 2.0, 3.0, 4.0)
            result = pipeline.extract_audio_segment(2.0, 4.0)
            assert result is not None
            assert len(result) == 3 * 1600

    def test_no_duplicate_from_intro_and_rolling(self, pipeline):
        """F3: Chunks in both intro and rolling buffer should not duplicate."""
        base_time = time.monotonic()
        with patch("backend.main.time") as mock_time:
            # Add chunks in intro window (< 30s)
            for i in range(5):
                mock_time.monotonic.return_value = base_time + i * 1.0
                pipeline.buffer_audio(bytes([i]) * 1600, is_mic=False)

            # These chunks are in BOTH intro buffer and rolling buffer
            result = pipeline.extract_audio_segment(0.0, 5.0)
            assert result is not None
            # Should be exactly 5 chunks, not 10 (deduped by timestamp)
            assert len(result) == 5 * 1600

    def test_uses_only_system_audio(self, pipeline):
        """extract_audio_segment should only use system audio, not mic."""
        base_time = time.monotonic()
        with patch("backend.main.time") as mock_time:
            mock_time.monotonic.return_value = base_time
            pipeline.buffer_audio(b"\x01\x00" * 800, is_mic=True)   # mic
            mock_time.monotonic.return_value = base_time + 0.05
            pipeline.buffer_audio(b"\x02\x00" * 800, is_mic=False)  # system

            result = pipeline.extract_audio_segment(0.0, 1.0)
            assert result is not None
            # Should only contain the system chunk
            assert len(result) == 1600

    def test_out_of_range_returns_none(self, pipeline):
        """Requesting a range outside buffered data should return None."""
        base_time = time.monotonic()
        with patch("backend.main.time") as mock_time:
            mock_time.monotonic.return_value = base_time
            pipeline.buffer_audio(b"\x00\x00" * 800, is_mic=False)

            # Request range far in the future
            result = pipeline.extract_audio_segment(100.0, 200.0)
            assert result is None
