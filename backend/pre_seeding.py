"""
Pre-seeding: classify a participant's Communicator Superpower from free text.

Accepts any of:
  - A natural-language description ("Sarah always challenges assumptions and needs data")
  - Pasted emails or chat messages written by the participant
  - LinkedIn bio or professional summary

Returns a PreSeedResult with:
  - type: one of the 4 Superpower types (or None if confidence is too low)
  - confidence: 0.0–1.0
  - state: "active" | "pending" (PENDING when input is insufficient)
  - reasoning: brief explanation for the classification

P0 gate: must correctly classify ≥70% of 5 known-profile participants before deployment.
See tests/evals/pre_seeding.py for the accuracy gate test harness.

Accuracy gate (human): build a test set of 5+ participants with known Superpower types
and run tests/evals/pre_seeding.py against them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal

import anthropic

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

SuperpowerType = Literal["Architect", "Firestarter", "Inquisitor", "Bridge Builder"]
PreSeedState = Literal["active", "pending"]


@dataclass
class PreSeedResult:
    """Result of a pre-seeding classification."""
    type: SuperpowerType | None       # None only when state == "pending"
    confidence: float                  # 0.0 – 1.0
    state: PreSeedState               # "active" = usable; "pending" = insufficient data
    reasoning: str                     # One sentence explaining the classification
    input_length: int                  # Character count of input (for diagnostics)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a expert in the Communicator Superpower framework, which classifies people
across two axes: Logic–Narrative and Advocate–Analyze.

The four Superpower types:

ARCHITECT (Logic + Analyze)
  Strengths: Systematic, data-first, structures arguments clearly, needs evidence before committing.
  Signals: Maps out frameworks, asks for data, wants to understand the full picture, organized.
  Example phrases: "Let me outline the key variables", "What does the data say?", "Walk me through the process."

FIRESTARTER (Narrative + Advocate)
  Strengths: High energy, inspires through story, visionary, moves fast, generates enthusiasm.
  Signals: Big-picture thinking, storytelling, rallies people around a future, impatient with process.
  Example phrases: "Here's where we're going", "Imagine if we...", "Let's not overthink this — let's go."

INQUISITOR (Logic + Advocate)
  Strengths: Questions everything, challenges assumptions, needs evidence to move, sharp and direct.
  Signals: Probes with hard questions, pushes back on weak reasoning, data-hungry, skeptical.
  Example phrases: "What's the evidence?", "Have you considered...", "I need to see the numbers."

BRIDGE BUILDER (Narrative + Analyze)
  Strengths: Reads the room, builds consensus, empathetic, synthesizes diverse perspectives.
  Signals: Focuses on relationships and team dynamics, finds common ground, inclusive language.
  Example phrases: "What does everyone think?", "I want to make sure we're all on the same page."

---

Given a description of a person (natural language, email samples, bio, or meeting notes),
classify their primary Communicator Superpower.

Respond ONLY with valid JSON in this exact schema:
{
  "type": "Architect" | "Firestarter" | "Inquisitor" | "Bridge Builder" | null,
  "confidence": <float 0.0–1.0>,
  "state": "active" | "pending",
  "reasoning": "<one sentence>"
}

Rules:
- Set state="pending" and type=null when the input is too vague to classify reliably
  (single-word descriptions, pure emotional adjectives with no behavioral signal,
  or input under 15 words). Set confidence < 0.40 in this case.
- Set state="active" when there is enough behavioral signal to classify.
- Confidence reflects behavioral evidence, not certainty about the type:
  0.80+ = strong behavioral signals across multiple dimensions
  0.60–0.79 = clear signals for one type, some ambiguity
  0.40–0.59 = weak or mixed signals; type is best guess
  < 0.40 = insufficient information (use state="pending")
- The type in "reasoning" must match the "type" field exactly.
- reasoning must be a single sentence, max 25 words.
"""

# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

# Minimum input length to attempt active classification
_MIN_ACTIVE_CHARS = 20
_MIN_ACTIVE_WORDS = 8


def classify(
    description: str,
    *,
    client: anthropic.Anthropic | None = None,
    model: str = "claude-haiku-4-5",
    max_tokens: int = 200,
) -> PreSeedResult:
    """
    Classify a participant's Communicator Superpower from a free-text description.

    Args:
        description: Free text about the participant. Can be a natural-language
            description, pasted emails, LinkedIn bio, or meeting notes.
        client: Anthropic client (created from env if not provided).
        model: Model to use. Defaults to claude-haiku-4-5 (low-latency).
        max_tokens: Response token cap.

    Returns:
        PreSeedResult — see type definition above.

    Raises:
        ValueError: If description is empty.
        anthropic.APIError: On API failures (caller should handle).
    """
    if not description or not description.strip():
        raise ValueError("description must be non-empty")

    description = description.strip()
    word_count = len(description.split())

    # Fast path: definitively too short for active classification
    if len(description) < _MIN_ACTIVE_CHARS or word_count < _MIN_ACTIVE_WORDS:
        return PreSeedResult(
            type=None,
            confidence=0.0,
            state="pending",
            reasoning="Input too short to identify behavioral signals.",
            input_length=len(description),
        )

    # Truncate to 8,000 chars (system constraint from CLAUDE.md)
    if len(description) > 8000:
        description = description[:8000]

    if client is None:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Classify this person's Communicator Superpower:\n\n{description}",
            }
        ],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Model returned non-JSON response: {raw!r}") from e

    # Validate required fields
    valid_types = {"Architect", "Firestarter", "Inquisitor", "Bridge Builder", None}
    sp_type = parsed.get("type")
    if sp_type not in valid_types:
        raise ValueError(f"Invalid type in response: {sp_type!r}")

    confidence = float(parsed.get("confidence", 0.0))
    state = parsed.get("state", "active")
    reasoning = parsed.get("reasoning", "")

    # Enforce consistency: null type must have pending state
    if sp_type is None and state != "pending":
        state = "pending"
    if state == "pending" and sp_type is not None:
        sp_type = None

    return PreSeedResult(
        type=sp_type,
        confidence=confidence,
        state=state,
        reasoning=reasoning,
        input_length=len(description),
    )
