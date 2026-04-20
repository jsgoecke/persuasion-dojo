"""Tests for backend/speaker_embeddings.py — voiceprint extraction, comparison, and storage."""

from __future__ import annotations

import json

import numpy as np
import pytest

from backend.speaker_embeddings import (
    MIN_SEGMENT_DURATION_S,
    VoiceprintExtractor,
    _pcm_to_fbank,
    centroid_from_json,
    centroid_to_json,
)


# ── Fbank computation ──────────────────────────────────────────────────────

class TestFbank:
    def test_output_shape(self):
        """Fbank output should have 80 mel bins (default)."""
        # 1 second of silence at 16kHz, 16-bit
        pcm = b"\x00\x00" * 16000
        fbank = _pcm_to_fbank(pcm, sample_rate=16000)
        assert fbank.shape[1] == 80
        assert fbank.shape[0] > 0  # at least 1 frame

    def test_different_durations(self):
        """Longer audio should produce more frames."""
        pcm_1s = b"\x00\x00" * 16000
        pcm_2s = b"\x00\x00" * 32000
        fb1 = _pcm_to_fbank(pcm_1s)
        fb2 = _pcm_to_fbank(pcm_2s)
        assert fb2.shape[0] > fb1.shape[0]

    def test_dtype_is_float32(self):
        pcm = b"\x00\x00" * 16000
        fbank = _pcm_to_fbank(pcm)
        assert fbank.dtype == np.float32


# ── Serialization ──────────────────────────────────────────────────────────

class TestSerialization:
    def test_roundtrip(self):
        """centroid_to_json → centroid_from_json should preserve values."""
        original = np.random.randn(256).astype(np.float32)
        original = original / np.linalg.norm(original)
        json_str = centroid_to_json(original)
        restored = centroid_from_json(json_str)
        np.testing.assert_allclose(restored, original, atol=1e-5)

    def test_json_is_valid(self):
        arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        json_str = centroid_to_json(arr)
        parsed = json.loads(json_str)
        assert isinstance(parsed, list)
        assert len(parsed) == 3


# ── Cosine similarity ─────────────────────────────────────────────────────

class TestCosineSimilarity:
    def test_identical_vectors(self):
        a = np.random.randn(256).astype(np.float32)
        a = a / np.linalg.norm(a)
        sim = VoiceprintExtractor.cosine_similarity(a, a)
        assert abs(sim - 1.0) < 1e-5

    def test_orthogonal_vectors(self):
        a = np.zeros(256, dtype=np.float32)
        b = np.zeros(256, dtype=np.float32)
        a[0] = 1.0
        b[1] = 1.0
        sim = VoiceprintExtractor.cosine_similarity(a, b)
        assert abs(sim) < 1e-5

    def test_opposite_vectors(self):
        a = np.random.randn(256).astype(np.float32)
        a = a / np.linalg.norm(a)
        sim = VoiceprintExtractor.cosine_similarity(a, -a)
        assert abs(sim + 1.0) < 1e-5


# ── EMA centroid update ───────────────────────────────────────────────────

class TestCentroidUpdate:
    def test_first_session_takes_new(self):
        """With sessions=0, alpha=1.0 so result should equal the new vector."""
        old = np.random.randn(256).astype(np.float32)
        old = old / np.linalg.norm(old)
        new = np.random.randn(256).astype(np.float32)
        new = new / np.linalg.norm(new)
        result = VoiceprintExtractor.update_centroid(old, new, sessions=0)
        # alpha = 1/(0+1) = 1.0, so result ≈ new (normalized)
        np.testing.assert_allclose(result, new / np.linalg.norm(new), atol=1e-5)

    def test_many_sessions_favors_existing(self):
        """With many sessions, new embedding barely moves the centroid."""
        old = np.zeros(256, dtype=np.float32)
        old[0] = 1.0
        new = np.zeros(256, dtype=np.float32)
        new[1] = 1.0
        result = VoiceprintExtractor.update_centroid(old, new, sessions=100)
        # alpha = 1/101 ≈ 0.01, so result should be very close to old
        assert result[0] > 0.99

    def test_result_is_normalized(self):
        old = np.random.randn(256).astype(np.float32)
        new = np.random.randn(256).astype(np.float32)
        result = VoiceprintExtractor.update_centroid(old, new, sessions=5)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-5


# ── Outlier rejection centroid ─────────────────────────────────────────────

class TestSpeakerCentroid:
    def _make_extractor(self):
        """Create extractor without loading WeSpeaker model."""
        ext = VoiceprintExtractor.__new__(VoiceprintExtractor)
        ext._model = None  # Skip model loading, only test centroid math
        return ext

    def test_empty_returns_none(self):
        ext = self._make_extractor()
        assert ext.compute_speaker_centroid([]) is None

    def test_single_embedding(self):
        ext = self._make_extractor()
        e = np.random.randn(256).astype(np.float32)
        e = e / np.linalg.norm(e)
        result = ext.compute_speaker_centroid([e], drop_outliers=0)
        np.testing.assert_allclose(result, e, atol=1e-5)

    def test_outlier_rejection(self):
        """Dropping the outlier should improve centroid quality."""
        ext = self._make_extractor()
        # 4 similar vectors + 1 outlier
        base = np.random.randn(256).astype(np.float32)
        base = base / np.linalg.norm(base)
        cluster = [base + np.random.randn(256).astype(np.float32) * 0.01 for _ in range(4)]
        cluster = [v / np.linalg.norm(v) for v in cluster]
        outlier = -base  # opposite direction
        outlier = outlier / np.linalg.norm(outlier)
        all_embeds = cluster + [outlier]

        # Without outlier rejection
        c_all = ext.compute_speaker_centroid(all_embeds, drop_outliers=0)
        # With outlier rejection (drop 1)
        c_clean = ext.compute_speaker_centroid(all_embeds, drop_outliers=1)

        # Clean centroid should be closer to the base direction
        sim_all = float(np.dot(c_all, base))
        sim_clean = float(np.dot(c_clean, base))
        assert sim_clean > sim_all

    def test_drop_more_than_available(self):
        """If drop_outliers >= len(embeddings), just average all."""
        ext = self._make_extractor()
        e1 = np.random.randn(256).astype(np.float32)
        e1 = e1 / np.linalg.norm(e1)
        e2 = np.random.randn(256).astype(np.float32)
        e2 = e2 / np.linalg.norm(e2)
        result = ext.compute_speaker_centroid([e1, e2], drop_outliers=5)
        assert result is not None


# ── Extractor with model ───────────────────────────────────────────────────
#
# Marked `voiceprint` — deselected by default locally (see pyproject.toml),
# run unconditionally in CI. If the marker selects this class but the deps
# are missing, we fail loudly rather than skipping — a silent skip in CI
# would mask a broken requirements-voiceprint.txt install.

@pytest.mark.voiceprint
class TestExtractorIntegration:
    @pytest.fixture
    def extractor(self):
        ext = VoiceprintExtractor()
        if not ext.available:
            pytest.fail(
                "VoiceprintExtractor is unavailable despite the `voiceprint` "
                "marker selecting this test. Install the extras: "
                "`pip install -r requirements-voiceprint.txt`."
            )
        return ext

    def test_extract_short_segment_returns_none(self, extractor):
        """Segments shorter than MIN_SEGMENT_DURATION_S should return None."""
        pcm = b"\x00\x00" * (16000 * 2)  # 2 seconds
        assert extractor.extract_embedding(pcm) is None

    def test_extract_long_segment(self, extractor):
        """6s of audio should produce a 256-dim embedding."""
        # Generate some noise to avoid zero-energy edge cases
        rng = np.random.RandomState(42)
        samples = (rng.randn(16000 * 6) * 1000).astype(np.int16)
        pcm = samples.tobytes()
        embed = extractor.extract_embedding(pcm)
        assert embed is not None
        assert embed.shape == (256,)
        assert abs(np.linalg.norm(embed) - 1.0) < 1e-4

    def test_same_audio_produces_similar_embeddings(self, extractor):
        """Identical audio should produce identical embeddings."""
        rng = np.random.RandomState(42)
        samples = (rng.randn(16000 * 6) * 1000).astype(np.int16)
        pcm = samples.tobytes()
        e1 = extractor.extract_embedding(pcm)
        e2 = extractor.extract_embedding(pcm)
        assert e1 is not None and e2 is not None
        sim = VoiceprintExtractor.cosine_similarity(e1, e2)
        assert sim > 0.99


# ── Voiceprint boost in resolver (F8: tests exercise _resolve_once) ──────────

class TestVoiceprintBoost:
    """Test voiceprint confidence boost through _resolve_once with mocked LLM."""

    @staticmethod
    def _make_resolver_with_mock(known_names, llm_response):
        """Create a SpeakerResolver with a mocked Anthropic client."""
        from unittest.mock import AsyncMock, MagicMock
        from backend.speaker_resolver import SpeakerResolver

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps(llm_response))]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        resolver = SpeakerResolver(
            anthropic_client=mock_client,
            known_names=known_names,
        )
        # Add enough utterances so _resolve_once runs
        for i in range(6):
            resolver.add_utterance("counterpart_0", f"Utterance {i}")
        return resolver

    @pytest.mark.asyncio
    async def test_boost_increases_confidence(self):
        """Voiceprint match should boost LLM confidence via _resolve_once."""
        resolver = self._make_resolver_with_mock(
            known_names=["Alice"],
            llm_response={"mappings": [
                {"speaker_id": "counterpart_0", "name": "Alice", "confidence": 0.75},
            ]},
        )
        resolver.set_voiceprint_match("counterpart_0", "Alice", 0.85)
        await resolver._resolve_once()
        # 0.75 + 0.15 = 0.90, capped at lock_threshold - 0.01 = 0.79
        assert resolver._confidences["counterpart_0"] == pytest.approx(
            resolver._lock_threshold - 0.01, abs=1e-5,
        )

    @pytest.mark.asyncio
    async def test_boost_caps_below_lock_threshold(self):
        """F11: Boost should not exceed lock_threshold - 0.01."""
        resolver = self._make_resolver_with_mock(
            known_names=["Alice"],
            llm_response={"mappings": [
                {"speaker_id": "counterpart_0", "name": "Alice", "confidence": 0.78},
            ]},
        )
        resolver.set_voiceprint_match("counterpart_0", "Alice", 0.9)
        await resolver._resolve_once()
        # 0.78 + 0.15 = 0.93, but capped at 0.80 - 0.01 = 0.79
        conf = resolver._confidences["counterpart_0"]
        assert conf < resolver._lock_threshold
        assert "counterpart_0" not in resolver._locked

    @pytest.mark.asyncio
    async def test_no_boost_below_similarity_threshold(self):
        """Voiceprint similarity <= 0.7 should not trigger boost."""
        resolver = self._make_resolver_with_mock(
            known_names=["Alice"],
            llm_response={"mappings": [
                {"speaker_id": "counterpart_0", "name": "Alice", "confidence": 0.75},
            ]},
        )
        resolver.set_voiceprint_match("counterpart_0", "Alice", 0.65)
        await resolver._resolve_once()
        # No boost, confidence stays at 0.75
        assert resolver._confidences["counterpart_0"] == pytest.approx(0.75, abs=1e-5)

    @pytest.mark.asyncio
    async def test_no_boost_wrong_name(self):
        """Voiceprint matching a different name should not boost."""
        resolver = self._make_resolver_with_mock(
            known_names=["Alice", "Bob"],
            llm_response={"mappings": [
                {"speaker_id": "counterpart_0", "name": "Alice", "confidence": 0.75},
            ]},
        )
        # Voiceprint says Bob, LLM says Alice — no boost
        resolver.set_voiceprint_match("counterpart_0", "Bob", 0.9)
        await resolver._resolve_once()
        assert resolver._confidences["counterpart_0"] == pytest.approx(0.75, abs=1e-5)
