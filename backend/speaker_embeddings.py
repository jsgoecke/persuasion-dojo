"""
WeSpeaker ONNX voiceprint extraction module.

Extracts 256-dim ECAPA-TDNN speaker embeddings from raw PCM audio segments.
Used to boost speaker resolver confidence when a voiceprint matches a known
participant. Does NOT replace the LLM resolver, only provides an additional
signal.

Dependencies (optional):
    pip install wespeakerruntime numpy

If wespeakerruntime is not installed, VoiceprintExtractor.available() returns
False and the resolver operates without voiceprint boost.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# Minimum segment length for reliable embeddings (from experiment E1)
MIN_SEGMENT_DURATION_S = 5.0


def _is_available() -> bool:
    """Check if wespeakerruntime and numpy are importable."""
    try:
        import numpy  # noqa: F401
        import wespeakerruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _pcm_to_fbank(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    num_mel_bins: int = 80,
    frame_length_ms: int = 25,
    frame_shift_ms: int = 10,
) -> "np.ndarray":
    """Compute log-Mel filterbank features from raw 16-bit PCM bytes.

    Pure numpy implementation, no torchaudio/torchcodec/ffmpeg needed.
    """
    import numpy as np
    from numpy.fft import rfft

    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    # Pre-emphasis
    samples = np.append(samples[0], samples[1:] - 0.97 * samples[:-1])

    frame_len = int(sample_rate * frame_length_ms / 1000)
    frame_shift = int(sample_rate * frame_shift_ms / 1000)
    n_fft = 1
    while n_fft < frame_len:
        n_fft *= 2

    n_frames = max(1, 1 + (len(samples) - frame_len) // frame_shift)
    frames = np.zeros((n_frames, frame_len))
    for i in range(n_frames):
        start = i * frame_shift
        chunk = samples[start : start + frame_len]
        frames[i, : len(chunk)] = chunk

    window = np.hamming(frame_len)
    frames *= window

    mag = np.abs(rfft(frames, n=n_fft)) ** 2

    # Mel filterbank
    low_freq, high_freq = 20.0, sample_rate / 2.0
    mel_low = 2595.0 * np.log10(1.0 + low_freq / 700.0)
    mel_high = 2595.0 * np.log10(1.0 + high_freq / 700.0)
    mel_points = np.linspace(mel_low, mel_high, num_mel_bins + 2)
    hz_points = 700.0 * (10.0 ** (mel_points / 2595.0) - 1.0)
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)

    fbank_matrix = np.zeros((num_mel_bins, n_fft // 2 + 1))
    for m in range(num_mel_bins):
        f_left, f_center, f_right = bins[m], bins[m + 1], bins[m + 2]
        for k in range(f_left, f_center):
            if f_center != f_left:
                fbank_matrix[m, k] = (k - f_left) / (f_center - f_left)
        for k in range(f_center, f_right):
            if f_right != f_center:
                fbank_matrix[m, k] = (f_right - k) / (f_right - f_center)

    fbank_feat = np.dot(mag, fbank_matrix.T)
    fbank_feat = np.where(fbank_feat > 0, fbank_feat, np.finfo(float).eps)
    fbank_feat = np.log(fbank_feat)

    return fbank_feat.astype(np.float32)


class VoiceprintExtractor:
    """Extract and compare speaker embeddings using WeSpeaker ECAPA-TDNN."""

    def __init__(self) -> None:
        self._model = None
        if not _is_available():
            logger.info("VoiceprintExtractor: wespeakerruntime not available, disabled")
            return
        try:
            import wespeakerruntime as wespeaker
            self._model = wespeaker.Speaker(lang="en")
            logger.info("VoiceprintExtractor: loaded WeSpeaker ECAPA-TDNN model")
        except Exception:
            logger.warning("VoiceprintExtractor: failed to load model", exc_info=True)

    @property
    def available(self) -> bool:
        return self._model is not None

    def extract_embedding(
        self, pcm_bytes: bytes, sample_rate: int = 16000,
    ) -> "np.ndarray | None":
        """Extract normalized embedding from raw 16-bit PCM audio.

        Returns a 1-D numpy array (256 floats) or None on failure.
        Requires at least MIN_SEGMENT_DURATION_S of audio.
        """
        if not self.available:
            return None

        import numpy as np

        # Check minimum duration
        n_samples = len(pcm_bytes) // 2
        duration_s = n_samples / sample_rate
        if duration_s < MIN_SEGMENT_DURATION_S:
            return None

        try:
            fbank = _pcm_to_fbank(pcm_bytes, sample_rate=sample_rate)
            fbank_batch = fbank[np.newaxis, :, :]  # [1, T, D]
            embed = self._model.extract_embedding_feat(fbank_batch)
            if embed is None:
                return None
            embed = np.array(embed).flatten()
            norm = np.linalg.norm(embed)
            if norm > 0:
                embed = embed / norm
            return embed
        except Exception:
            logger.debug("VoiceprintExtractor: extraction failed", exc_info=True)
            return None

    @staticmethod
    def cosine_similarity(a: "np.ndarray", b: "np.ndarray") -> float:
        """Cosine similarity between two normalized embeddings."""
        import numpy as np
        return float(np.dot(a, b))

    @staticmethod
    def update_centroid(
        existing: "np.ndarray", new: "np.ndarray", sessions: int,
    ) -> "np.ndarray":
        """EMA update: blend new embedding into existing centroid.

        Weight of new = 1 / (sessions + 1). Returns normalized centroid.
        """
        import numpy as np
        alpha = 1.0 / (sessions + 1)
        centroid = (1.0 - alpha) * existing + alpha * new
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        return centroid

    def compute_speaker_centroid(
        self,
        embeddings: list["np.ndarray"],
        drop_outliers: int = 2,
    ) -> "np.ndarray | None":
        """Compute centroid from multiple embeddings with outlier rejection.

        Drops the `drop_outliers` embeddings with lowest avg similarity to
        others (from experiment E5b), then averages and normalizes.
        """
        import numpy as np

        if not embeddings:
            return None
        if len(embeddings) <= drop_outliers:
            # Not enough to drop, just average
            centroid = np.mean(embeddings, axis=0)
            norm = np.linalg.norm(centroid)
            return centroid / norm if norm > 0 else centroid

        # Compute avg similarity of each embedding to all others
        scores = []
        for i, e in enumerate(embeddings):
            sims = [self.cosine_similarity(e, embeddings[j])
                    for j in range(len(embeddings)) if j != i]
            scores.append((i, float(np.mean(sims))))

        # Sort by avg similarity (ascending) and drop worst
        scores.sort(key=lambda x: x[1])
        keep_indices = sorted(idx for idx, _ in scores[drop_outliers:])
        kept = [embeddings[i] for i in keep_indices]

        centroid = np.mean(kept, axis=0)
        norm = np.linalg.norm(centroid)
        return centroid / norm if norm > 0 else centroid


# ── Serialization helpers (for DB storage) ────────────────────────────────

def centroid_to_json(centroid: "np.ndarray") -> str:
    """Serialize a numpy embedding to JSON string for DB storage."""
    return json.dumps([round(float(x), 6) for x in centroid])


def centroid_from_json(json_str: str) -> "np.ndarray":
    """Deserialize a JSON string back to numpy embedding."""
    import numpy as np
    return np.array(json.loads(json_str), dtype=np.float32)
