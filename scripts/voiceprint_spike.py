#!/usr/bin/env python3
"""
Voiceprint validation spike — tests WeSpeaker ECAPA-TDNN on mixed SCK audio.

This script validates whether speaker embeddings from WeSpeaker can distinguish
speakers in ScreenCaptureKit-captured mixed audio (single channel, all speakers
mixed together). This is the GATE for implementing Changes 6-9 (voiceprint
extraction, storage, and resolver confidence boost).

Pass criteria:
  - Intra-speaker cosine similarity > 0.6
  - Inter-speaker cosine similarity < 0.4

Usage:
    python scripts/voiceprint_spike.py <audio_file> [--deepgram-key KEY]

The audio file should be a WAV recorded via ScreenCaptureKit from a real meeting
with 2+ speakers. The script uses Deepgram to get word-level timestamps with
speaker diarization, then extracts per-speaker segments and computes embeddings.

Requirements:
    pip install wespeakerruntime numpy

If wespeakerruntime is not available, the script will report what would be needed
and exit with instructions.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import struct
import sys
import urllib.request
import wave
from collections import defaultdict
from pathlib import Path


def check_dependencies() -> bool:
    """Check if required packages are available."""
    missing = []
    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")
    try:
        import wespeakerruntime  # noqa: F401
    except ImportError:
        missing.append("wespeakerruntime")
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}")
        print(f"Install with: pip install {' '.join(missing)}")
        return False
    return True


def transcribe_with_diarization(audio_path: str, api_key: str) -> dict:
    """Send audio to Deepgram and get word-level timestamps with speaker IDs."""
    with open(audio_path, "rb") as f:
        audio_data = f.read()

    url = "https://api.deepgram.com/v1/listen?model=nova-2&language=en-US&diarize=true&punctuate=true"
    req = urllib.request.Request(
        url,
        data=audio_data,
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "audio/wav",
        },
    )
    resp = urllib.request.urlopen(req, timeout=120)
    return json.loads(resp.read())


def extract_speaker_segments(
    dg_response: dict, min_duration_s: float = 2.0,
) -> dict[int, list[tuple[float, float]]]:
    """Extract per-speaker time segments from Deepgram response.

    Returns {speaker_id: [(start_s, end_s), ...]} where each segment
    is at least min_duration_s long.
    """
    words = dg_response["results"]["channels"][0]["alternatives"][0]["words"]

    # Group consecutive words by speaker
    segments: dict[int, list[tuple[float, float]]] = defaultdict(list)
    current_speaker = None
    seg_start = 0.0
    seg_end = 0.0

    for w in words:
        speaker = w.get("speaker", 0)
        start = w["start"]
        end = w["end"]

        if speaker != current_speaker:
            # Save previous segment
            if current_speaker is not None and (seg_end - seg_start) >= min_duration_s:
                segments[current_speaker].append((seg_start, seg_end))
            current_speaker = speaker
            seg_start = start
        seg_end = end

    # Save last segment
    if current_speaker is not None and (seg_end - seg_start) >= min_duration_s:
        segments[current_speaker].append((seg_start, seg_end))

    return dict(segments)


def extract_pcm_segment(
    audio_path: str, start_s: float, end_s: float,
) -> bytes:
    """Extract a PCM segment from a WAV file given time bounds."""
    with wave.open(audio_path, "rb") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()

        start_frame = int(start_s * sample_rate)
        end_frame = int(end_s * sample_rate)
        n_frames = end_frame - start_frame

        wf.setpos(start_frame)
        raw = wf.readframes(n_frames)

    # Convert to mono 16-bit if needed
    if n_channels > 1:
        # Take first channel only
        samples = struct.unpack(f"<{len(raw) // sample_width}{'h' if sample_width == 2 else 'b'}", raw)
        mono = samples[::n_channels]
        raw = struct.pack(f"<{len(mono)}h", *mono)

    return raw


# F6: Import fbank from backend instead of duplicating
from backend.speaker_embeddings import _pcm_to_fbank


def compute_embeddings(
    audio_path: str,
    segments: dict[int, list[tuple[float, float]]],
    max_segments_per_speaker: int = 5,
) -> dict[int, list]:
    """Extract WeSpeaker embeddings for each speaker's segments.

    Uses extract_embedding_feat with numpy fbank computation to avoid
    torchaudio/torchcodec/ffmpeg dependency chain.
    """
    import numpy as np
    import wespeakerruntime as wespeaker

    model = wespeaker.Speaker(lang="en")

    embeddings: dict[int, list] = {}

    with wave.open(audio_path, "rb") as wf:
        sample_rate = wf.getframerate()

    for speaker_id, segs in segments.items():
        speaker_embeds = []
        for start_s, end_s in segs[:max_segments_per_speaker]:
            pcm = extract_pcm_segment(audio_path, start_s, end_s)

            try:
                # Compute fbank features from raw PCM (no torchaudio needed)
                fbank = _pcm_to_fbank(pcm, sample_rate=sample_rate)
                # extract_embedding_feat expects [B, T, D]
                fbank_batch = fbank[np.newaxis, :, :]

                embed = model.extract_embedding_feat(fbank_batch)
                if embed is not None:
                    embed = np.array(embed).flatten()
                    norm = np.linalg.norm(embed)
                    if norm > 0:
                        embed = embed / norm
                    speaker_embeds.append(embed)
            except Exception as e:
                print(f"  Warning: embedding extraction failed for speaker {speaker_id} "
                      f"segment {start_s:.1f}-{end_s:.1f}s: {e}")

        if speaker_embeds:
            embeddings[speaker_id] = speaker_embeds

    return embeddings


def cosine_similarity(a, b) -> float:
    """Cosine similarity between two normalized vectors."""
    import numpy as np
    return float(np.dot(a, b))


def evaluate_embeddings(embeddings: dict[int, list]) -> dict:
    """Compute intra-speaker and inter-speaker similarity statistics."""
    import numpy as np

    results = {
        "speakers": len(embeddings),
        "intra_speaker": {},
        "inter_speaker": [],
        "pass": False,
    }

    speaker_ids = sorted(embeddings.keys())

    # Intra-speaker similarity (same speaker, different segments)
    for sid in speaker_ids:
        embeds = embeddings[sid]
        if len(embeds) < 2:
            results["intra_speaker"][sid] = {"mean": None, "n_segments": len(embeds)}
            continue
        sims = []
        for i in range(len(embeds)):
            for j in range(i + 1, len(embeds)):
                sims.append(cosine_similarity(embeds[i], embeds[j]))
        results["intra_speaker"][sid] = {
            "mean": round(float(np.mean(sims)), 4),
            "min": round(float(np.min(sims)), 4),
            "max": round(float(np.max(sims)), 4),
            "n_segments": len(embeds),
        }

    # Inter-speaker similarity (different speakers)
    for i, sid_a in enumerate(speaker_ids):
        for sid_b in speaker_ids[i + 1:]:
            sims = []
            for ea in embeddings[sid_a]:
                for eb in embeddings[sid_b]:
                    sims.append(cosine_similarity(ea, eb))
            results["inter_speaker"].append({
                "speakers": [sid_a, sid_b],
                "mean": round(float(np.mean(sims)), 4),
                "min": round(float(np.min(sims)), 4),
                "max": round(float(np.max(sims)), 4),
            })

    # Evaluate pass criteria (must have actual data to pass)
    has_intra_data = any(
        v["mean"] is not None for v in results["intra_speaker"].values()
    )
    intra_ok = has_intra_data and all(
        v["mean"] is None or v["mean"] > 0.6
        for v in results["intra_speaker"].values()
    )
    inter_ok = len(results["inter_speaker"]) > 0 and all(
        v["mean"] < 0.4
        for v in results["inter_speaker"]
    )
    results["pass"] = intra_ok and inter_ok

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Validate WeSpeaker embeddings on mixed SCK audio"
    )
    parser.add_argument("audio_file", help="Path to WAV file from SCK recording")
    parser.add_argument(
        "--deepgram-key",
        default=os.environ.get("DEEPGRAM_API_KEY"),
        help="Deepgram API key (or set DEEPGRAM_API_KEY env var)",
    )
    parser.add_argument(
        "--output", "-o",
        default="scripts/voiceprint_spike_results.json",
        help="Output JSON file for results",
    )
    args = parser.parse_args()

    if not Path(args.audio_file).exists():
        print(f"Error: audio file not found: {args.audio_file}")
        sys.exit(1)

    if not args.deepgram_key:
        print("Error: Deepgram API key required. Set DEEPGRAM_API_KEY or use --deepgram-key")
        sys.exit(1)

    if not check_dependencies():
        sys.exit(1)

    print(f"Audio: {args.audio_file}")
    print()

    # Step 1: Transcribe with diarization
    print("Step 1: Transcribing with Deepgram (diarization enabled)...")
    dg_response = transcribe_with_diarization(args.audio_file, args.deepgram_key)
    print("  Done.")

    # Step 2: Extract per-speaker segments
    print("Step 2: Extracting per-speaker segments...")
    segments = extract_speaker_segments(dg_response, min_duration_s=2.0)
    for sid, segs in sorted(segments.items()):
        total_s = sum(e - s for s, e in segs)
        print(f"  Speaker {sid}: {len(segs)} segments, {total_s:.1f}s total")
    print()

    if len(segments) < 2:
        print("FAIL: Need at least 2 speakers for inter-speaker comparison.")
        print("      Make sure the audio has multiple distinct speakers.")
        sys.exit(1)

    # Step 3: Extract embeddings
    print("Step 3: Extracting WeSpeaker ECAPA-TDNN embeddings...")
    embeddings = compute_embeddings(args.audio_file, segments)
    for sid, embeds in sorted(embeddings.items()):
        print(f"  Speaker {sid}: {len(embeds)} embeddings extracted")
    print()

    # Step 4: Evaluate
    print("Step 4: Evaluating similarity metrics...")
    results = evaluate_embeddings(embeddings)

    print()
    print("=== RESULTS ===")
    print()
    print("Intra-speaker similarity (same person, different segments):")
    for sid, stats in sorted(results["intra_speaker"].items()):
        if stats["mean"] is not None:
            status = "PASS" if stats["mean"] > 0.6 else "FAIL"
            print(f"  Speaker {sid}: mean={stats['mean']:.3f} "
                  f"min={stats['min']:.3f} max={stats['max']:.3f} "
                  f"[{status}] (need > 0.6)")
        else:
            print(f"  Speaker {sid}: only {stats['n_segments']} segment (need 2+ for comparison)")
    print()
    print("Inter-speaker similarity (different people):")
    for pair in results["inter_speaker"]:
        status = "PASS" if pair["mean"] < 0.4 else "FAIL"
        print(f"  Speaker {pair['speakers'][0]} vs {pair['speakers'][1]}: "
              f"mean={pair['mean']:.3f} min={pair['min']:.3f} max={pair['max']:.3f} "
              f"[{status}] (need < 0.4)")
    print()

    if results["pass"]:
        print("VERDICT: PASS — WeSpeaker embeddings can distinguish speakers in mixed SCK audio.")
        print("         Proceed with Changes 6-9 (voiceprint extraction, storage, resolver boost).")
    else:
        print("VERDICT: FAIL — Embeddings cannot reliably distinguish speakers in mixed audio.")
        print("         Ship Changes 1-5 only. Investigate Deepgram multichannel=true or")
        print("         clean per-speaker audio for voiceprint extraction.")

    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")

    sys.exit(0 if results["pass"] else 1)


if __name__ == "__main__":
    main()
