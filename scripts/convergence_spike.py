#!/usr/bin/env python3
"""
Convergence Validation Spike — P0 Gate

Run this script against 5–10 annotated transcripts from real meetings.
Pass criterion: ≥75% signal agreement with your human annotations.

Usage:
    python scripts/convergence_spike.py --transcript scripts/sample_transcript.json \
                                        --annotation scripts/sample_annotation.json

    # Run against a whole directory:
    python scripts/convergence_spike.py --dir scripts/spike_transcripts/

Annotation file format:
    {
        "session_id": "meeting_20240315",
        "user_speaker": "speaker_0",
        "human_judgment": "converging",          # "converging" | "not_converging"
        "human_success_time": 420.0,             # seconds; null if never converged
        "notes": "Group agreed to proceed around minute 7"
    }

Transcript file format:
    {
        "session_id": "meeting_20240315",
        "utterances": [
            {"speaker": "speaker_0", "text": "...", "start": 0.0, "end": 5.2},
            ...
        ]
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path so backend imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.signals import (
    agreement_markers,
    question_type_arc,
    vocabulary_adoption,
    convergence_score,
    SignalResult,
)


# ---------------------------------------------------------------------------
# Agreement computation
# ---------------------------------------------------------------------------

def signal_agrees(result: SignalResult, human_converging: bool) -> bool:
    """Does this signal's prediction match the human annotation?"""
    return result.converging == human_converging


def run_spike_on_transcript(
    transcript: dict,
    annotation: dict,
    verbose: bool = False,
) -> dict:
    """
    Run all three signals on a single transcript and compare to human annotation.

    Returns a result dict with per-signal agreement and overall outcome.
    """
    session_id = transcript.get("session_id", "unknown")
    utterances = transcript["utterances"]
    user_speaker = annotation["user_speaker"]
    human_converging = annotation["human_judgment"] == "converging"

    combined, results = convergence_score(utterances, user_speaker)

    signal_results = []
    agreements = []
    for result in results:
        agrees = signal_agrees(result, human_converging)
        agreements.append(agrees)
        signal_results.append({
            "signal": result.signal,
            "predicted_converging": result.converging,
            "score": round(result.score, 3),
            "agrees_with_human": agrees,
        })
        if verbose:
            print(f"\n  [{result.signal}]")
            for line in result.evidence:
                print(f"    {line}")

    agreement_rate = sum(agreements) / len(agreements)
    all_agree = all(agreements)

    return {
        "session_id": session_id,
        "human_judgment": annotation["human_judgment"],
        "human_notes": annotation.get("notes", ""),
        "combined_score": round(combined, 3),
        "signal_results": signal_results,
        "agreement_rate": round(agreement_rate, 3),
        "all_signals_agree": all_agree,
    }


def find_transcript_pair(
    transcript_path: Path,
    annotation_dir: Path,
) -> Path | None:
    """Find the annotation file matching a transcript by session_id or filename stem."""
    stem = transcript_path.stem.replace("transcript", "annotation")
    candidates = [
        annotation_dir / f"{stem}.json",
        annotation_dir / f"{transcript_path.stem}_annotation.json",
        transcript_path.parent / f"{transcript_path.stem}_annotation.json",
    ]
    for c in candidates:
        if c.exists() and c != transcript_path:
            return c
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_spike(
    transcript_paths: list[Path],
    annotation_paths: list[Path],
    verbose: bool = False,
) -> None:
    assert len(transcript_paths) == len(annotation_paths), (
        "Mismatch: must have one annotation per transcript"
    )

    print("=" * 70)
    print("CONVERGENCE VALIDATION SPIKE")
    print(f"Running on {len(transcript_paths)} transcript(s)")
    print(f"Pass criterion: ≥75% signal agreement across all signals")
    print("=" * 70)

    all_results = []
    for t_path, a_path in zip(transcript_paths, annotation_paths):
        transcript = json.loads(t_path.read_text())
        annotation = json.loads(a_path.read_text())

        print(f"\n[{transcript.get('session_id', t_path.stem)}]")
        print(f"  Human judgment: {annotation['human_judgment']}")
        if annotation.get("notes"):
            print(f"  Notes: {annotation['notes']}")

        result = run_spike_on_transcript(transcript, annotation, verbose=verbose)
        all_results.append(result)

        for sr in result["signal_results"]:
            tick = "✓" if sr["agrees_with_human"] else "✗"
            print(
                f"  {tick} {sr['signal']:30s}  "
                f"predicted={'converging' if sr['predicted_converging'] else 'not_converging':15s}  "
                f"score={sr['score']:.2f}"
            )
        print(f"  Agreement rate: {result['agreement_rate']:.0%}")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_signals = len(all_results) * 3
    total_agreements = sum(
        sum(1 for sr in r["signal_results"] if sr["agrees_with_human"])
        for r in all_results
    )
    overall_rate = total_agreements / total_signals if total_signals else 0.0

    # Per-signal breakdown
    signal_names = ["vocabulary_adoption", "question_type_arc", "agreement_markers"]
    for sig in signal_names:
        sig_results = [
            sr for r in all_results
            for sr in r["signal_results"]
            if sr["signal"] == sig
        ]
        agree = sum(1 for sr in sig_results if sr["agrees_with_human"])
        rate = agree / len(sig_results) if sig_results else 0.0
        tick = "✓" if rate >= 0.75 else "✗"
        print(f"  {tick} {sig:30s}  {agree}/{len(sig_results)} transcripts  ({rate:.0%})")

    print(f"\n  Overall signal agreement: {total_agreements}/{total_signals} ({overall_rate:.0%})")
    print()

    PASS_THRESHOLD = 0.75
    passed = overall_rate >= PASS_THRESHOLD

    if passed:
        print("  ✅ SPIKE PASSED — signals reliably track persuasion success.")
        print("     Proceed to implement scoring.py with Convergence component.")
    else:
        print("  ❌ SPIKE FAILED — signals do not reliably track persuasion success.")
        print()
        print("  Failure path (from TODOS.md):")
        print("    → Replace Persuasion Score with Session Summary")
        print("    → Session Summary = {prompts_fired, timing, participation_distribution}")
        print("    → Growth Score and debrief remain unchanged")
        print("    → Only convergence-dependent scoring is cut")
        print()
        # Identify the weakest signal
        weakest = min(
            signal_names,
            key=lambda s: sum(
                1 for r in all_results
                for sr in r["signal_results"]
                if sr["signal"] == s and sr["agrees_with_human"]
            )
        )
        print(f"  Weakest signal: {weakest}")
        print("  Recommendation: inspect false predictions above and refine patterns")
        print("  in backend/signals.py before re-running the spike.")

    print("=" * 70)

    # Write JSON results for record-keeping
    output_path = Path("scripts/spike_results.json")
    output_path.write_text(json.dumps({
        "overall_agreement": round(overall_rate, 3),
        "passed": passed,
        "threshold": PASS_THRESHOLD,
        "transcript_count": len(all_results),
        "results": all_results,
    }, indent=2))
    print(f"\nDetailed results written to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convergence Validation Spike")
    parser.add_argument("--transcript", type=Path, help="Single transcript JSON file")
    parser.add_argument("--annotation", type=Path, help="Single annotation JSON file (paired with --transcript)")
    parser.add_argument("--dir", type=Path, help="Directory containing transcript+annotation JSON pairs")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full signal evidence for each transcript")
    args = parser.parse_args()

    transcript_paths: list[Path] = []
    annotation_paths: list[Path] = []

    if args.dir:
        t_paths = sorted(p for p in args.dir.glob("*.json") if not p.stem.endswith("_annotation"))
        for t_path in t_paths:
            a_path = find_transcript_pair(t_path, args.dir)
            if a_path:
                transcript_paths.append(t_path)
                annotation_paths.append(a_path)
            else:
                print(f"WARNING: No annotation found for {t_path.name} — skipping")
    elif args.transcript and args.annotation:
        transcript_paths.append(args.transcript)
        annotation_paths.append(args.annotation)
    else:
        print("Provide either --dir or both --transcript and --annotation")
        print()
        print(f"Example with sample data:")
        print(f"  python scripts/convergence_spike.py \\")
        print(f"      --transcript scripts/sample_transcript.json \\")
        print(f"      --annotation scripts/sample_annotation.json \\")
        print(f"      --verbose")
        sys.exit(1)

    if not transcript_paths:
        print("No transcript+annotation pairs found.")
        sys.exit(1)

    run_spike(transcript_paths, annotation_paths, verbose=args.verbose)


if __name__ == "__main__":
    main()
