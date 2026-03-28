#!/usr/bin/env python3
"""
Pre-Seeding Real-World Accuracy Gate — P0 Gate

Run this against 5 people whose Superpower you already know well.
Pass criterion: ≥70% correct classification (4 out of 5+).

Instructions:
1. Think of 5+ colleagues, direct reports, or clients whose communication
   style you know well enough to label (Architect / Firestarter / Inquisitor /
   Bridge Builder).
2. Write a 30–100 word description of each person's behavior — how they ask
   questions, run meetings, make decisions. DO NOT use their names.
3. Fill in the TEST_CASES list below with your descriptions.
4. Run: python scripts/real_world_gate.py
5. ≥70% correct = GATE PASSES = pre_seeding.py is safe to deploy.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.pre_seeding import classify, SuperpowerType

# ---------------------------------------------------------------------------
# FILL THIS IN WITH YOUR REAL TEST CASES
# ---------------------------------------------------------------------------
# Format: (description_string, expected_type)
# expected_type must be one of: "Architect", "Firestarter", "Inquisitor", "Bridge Builder"
#
# Example:
# TEST_CASES = [
#     (
#         "Always asks for the data before agreeing to anything. Pushes back on "
#         "assumptions and won't move until she's stress-tested the logic. Direct.",
#         "Inquisitor",
#     ),
#     ...
# ]

TEST_CASES: list[tuple[str, SuperpowerType]] = [
    (
        "Persuades through story and identity, not data. Opens meetings with emotionally resonant "
        "narratives that make the audience feel part of something larger. When describing strategy, "
        "builds a sense of narrative inevitability — one deal leads to the next leads to the next — "
        "rather than presenting analysis. Uses deliberate emotional distance as a persuasion tactic: "
        "authentically doesn't care if you're not already bought in. Falls flat when the audience "
        "is in analytical mode and needs structure before vision.",
        "Firestarter",
    ),
    (
        "Persuades through structural clarity and logical inevitability. Builds causal chains where "
        "every sentence depends on the prior one. Instinctively turns fuzzy objectives into testable "
        "propositions and wires logical dependencies between teammates' work. Trusts the structure to "
        "carry the argument — rarely wraps reasoning in emotional framing or personal story. Most "
        "effective with technical audiences. With emotionally loaded rooms, the logic lands but "
        "people need someone else to provide the human wrapper.",
        "Architect",
    ),
    (
        "Operates through diagnostic precision — finds the confound, the unexamined assumption, the "
        "gap in the result. When the room accepts a claim, this person asks what's missing before "
        "anyone acts on it. Reframes through comparative lenses rather than asserting prescriptions. "
        "Identifies gaps accurately but sometimes lets correct insights die as observations without "
        "advocating for them. Needs a push to build a 30-second case for why the gap matters.",
        "Inquisitor",
    ),
    (
        "The team's most consistent source of productive friction. Directly challenges ideas by "
        "articulating why they fail and offering an alternative frame — not a competing solution, "
        "but a different philosophy. Earns credibility through honest self-disclosure and "
        "principled pushback on authority. Satisfied when a gap has been acknowledged and addressed, "
        "not when his position has won. Risk is self-censorship: suppresses the analytical instinct "
        "under team momentum and later regrets it.",
        "Inquisitor",
    ),
    (
        "Builds bridges by using personal experience as a diagnostic mirror that reflects the "
        "team's gaps without triggering defensiveness. Asks the foundational question everyone else "
        "is too close to the problem to ask. Interventions are small, well-timed, and grounded in "
        "observable user reality rather than abstract principle. Risk is being heard and validated "
        "without being acted on — the team says 'great point' and proceeds unchanged. Needs to "
        "restate observations as concrete constraints to force a real answer.",
        "Bridge Builder",
    ),
]

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_gate() -> None:
    if not TEST_CASES:
        print("No test cases defined.")
        print()
        print("Edit scripts/real_world_gate.py and fill in TEST_CASES with")
        print("5+ real descriptions of people whose Superpower you know well.")
        print()
        print("Format:")
        print('  TEST_CASES = [')
        print('      ("Description of person...", "Inquisitor"),')
        print('      ("Description of person...", "Firestarter"),')
        print('      ...')
        print('  ]')
        sys.exit(1)

    print("=" * 60)
    print("PRE-SEEDING REAL-WORLD ACCURACY GATE")
    print(f"Running {len(TEST_CASES)} test cases")
    print("Pass criterion: ≥70% correct classification")
    print("=" * 60)
    print()

    correct = 0
    total = len(TEST_CASES)

    for i, (description, expected_type) in enumerate(TEST_CASES, 1):
        result = classify(description)
        predicted = result.type
        is_correct = predicted == expected_type

        tick = "✓" if is_correct else "✗"
        if is_correct:
            correct += 1

        print(f"Case {i}: {tick}")
        print(f"  Expected:  {expected_type}")
        print(f"  Got:       {predicted} (confidence={result.confidence:.2f}, state={result.state})")
        print(f"  Reasoning: {result.reasoning}")
        print(f"  Input:     {description[:80]}{'...' if len(description) > 80 else ''}")
        print()

    rate = correct / total
    print("=" * 60)
    print(f"Result: {correct}/{total} correct ({rate:.0%})")
    print()

    if rate >= 0.70:
        print("✅ GATE PASSED — pre_seeding.py is safe to deploy.")
        print("   Update TODOS.md: mark 'Pre-seed accuracy gate' as ✅ COMPLETE.")
    else:
        print("❌ GATE FAILED — classification accuracy below 70%.")
        print()
        print("Next steps:")
        print("  1. Inspect failed cases above — what type did the model get wrong?")
        print("  2. Look for a pattern: is it confusing Inquisitor/Architect?")
        print("     Or Firestarter/Bridge Builder?")
        print("  3. Refine the system prompt in backend/pre_seeding.py")
        print("     to clarify the distinction between the confused types.")
        print("  4. Re-run this script.")

    print("=" * 60)


if __name__ == "__main__":
    run_gate()
