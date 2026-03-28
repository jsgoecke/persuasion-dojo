#!/usr/bin/env python3
"""
Granola → Convergence Spike Format Converter

Converts Granola transcript exports to the format expected by convergence_spike.py.

Usage:
    python scripts/convert_granola.py \
        --transcript /path/to/granola_transcript.json \
        --annotation /path/to/granola_annotation.json \
        --user-speaker "Vish" \
        --out-dir scripts/spike_transcripts/

Output files (in --out-dir):
    {meeting_id}.json           — spike transcript format
    {meeting_id}_annotation.json — spike annotation format
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def convert_transcript(granola: dict, turn_duration_s: float = 10.0) -> dict:
    """
    Convert Granola transcript → spike format.

    Granola format:
        {"meeting_id": "...", "turns": [{"speaker": "Sam", "text": "..."}]}

    Spike format:
        {"session_id": "...", "utterances": [{"speaker": "...", "text": "...", "start": 0.0, "end": 10.0}]}
    """
    meeting_id = granola.get("meeting_id", "unknown")
    turns = granola.get("turns", [])

    utterances = []
    t = 0.0
    for turn in turns:
        speaker = turn.get("speaker", "unknown")
        text = turn.get("text", "").strip()
        if not text:
            continue
        # Estimate duration proportional to word count (rough: ~2 words/sec)
        words = len(text.split())
        duration = max(turn_duration_s, words / 2.0)
        utterances.append(
            {
                "speaker": speaker,
                "text": text,
                "start": round(t, 2),
                "end": round(t + duration, 2),
            }
        )
        t += duration

    return {
        "session_id": meeting_id,
        "utterances": utterances,
    }


def convert_annotation(granola_ann: dict, user_speaker: str) -> dict:
    """
    Convert Granola annotation → spike annotation format.

    Granola annotation format:
        {
          "meeting_id": "...",
          "human_judgment": "converging" | "not_converging",
          "confidence": 0.80,
          "reasoning": {"summary": "..."},
          ...
        }

    Spike annotation format:
        {
          "session_id": "...",
          "user_speaker": "Vish",
          "human_judgment": "converging" | "not_converging",
          "notes": "..."
        }
    """
    meeting_id = granola_ann.get("meeting_id", "unknown")
    human_judgment = granola_ann.get("human_judgment", "not_converging")

    # Extract notes from reasoning summary if available
    reasoning = granola_ann.get("reasoning", {})
    if isinstance(reasoning, dict):
        notes = reasoning.get("summary", "")
    else:
        notes = str(reasoning)

    # Append caveats if present
    caveats = granola_ann.get("caveats", [])
    if caveats:
        caveat_text = " | Caveats: " + "; ".join(caveats)
        notes = notes + caveat_text if notes else caveat_text

    return {
        "session_id": meeting_id,
        "user_speaker": user_speaker,
        "human_judgment": human_judgment,
        "notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Granola exports to spike format")
    parser.add_argument("--transcript", required=True, help="Path to Granola transcript JSON")
    parser.add_argument("--annotation", required=True, help="Path to Granola annotation JSON")
    parser.add_argument(
        "--user-speaker",
        default="Vish",
        help="Which speaker in the transcript is the user being coached (default: Vish)",
    )
    parser.add_argument(
        "--out-dir",
        default="scripts/spike_transcripts",
        help="Output directory for converted files",
    )
    args = parser.parse_args()

    transcript_path = Path(args.transcript)
    annotation_path = Path(args.annotation)
    out_dir = Path(args.out_dir)

    if not transcript_path.exists():
        print(f"ERROR: transcript file not found: {transcript_path}", file=sys.stderr)
        sys.exit(1)
    if not annotation_path.exists():
        print(f"ERROR: annotation file not found: {annotation_path}", file=sys.stderr)
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)

    # Load
    granola_transcript = json.loads(transcript_path.read_text())
    granola_annotation = json.loads(annotation_path.read_text())

    # Convert
    spike_transcript = convert_transcript(granola_transcript)
    spike_annotation = convert_annotation(granola_annotation, args.user_speaker)

    session_id = spike_transcript["session_id"]

    # Write
    transcript_out = out_dir / f"{session_id}.json"
    annotation_out = out_dir / f"{session_id}_annotation.json"

    transcript_out.write_text(json.dumps(spike_transcript, indent=2))
    annotation_out.write_text(json.dumps(spike_annotation, indent=2))

    print(f"✓ Converted transcript → {transcript_out}")
    print(f"  {len(spike_transcript['utterances'])} utterances")
    print(f"  speakers: {sorted({u['speaker'] for u in spike_transcript['utterances']})}")
    print()
    print(f"✓ Converted annotation → {annotation_out}")
    print(f"  user_speaker: {spike_annotation['user_speaker']}")
    print(f"  human_judgment: {spike_annotation['human_judgment']}")
    print()
    print(f"Ready. Run the spike with:")
    print(f"  python scripts/convergence_spike.py --dir {out_dir} --verbose")


if __name__ == "__main__":
    main()
