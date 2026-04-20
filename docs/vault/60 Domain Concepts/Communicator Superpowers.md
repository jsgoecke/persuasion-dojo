---
title: Communicator Superpowers
description: Two-axis framework classifying conversational style into four archetypes used by every coaching layer.
tags: [concept, topic/framework]
type: concept
related:
  - "[[Coaching Layers]]"
  - "[[Backend - profiler]]"
  - "[[Backend - self_assessment]]"
  - "[[Backend - pre_seeding]]"
updated: 2026-04-19
---

# Communicator Superpowers

The Communicator Superpower framework is the spine of Persuasion Dojo. Every participant — the user and their counterparts — is classified on two independent axes:

- **Focus axis:** Logic ↔ Narrative
- **Stance axis:** Advocate ↔ Analyze

The product of these axes gives four archetypes. Each has a distinctive cadence, trigger language, and preferred evidence type. Coaching prompts at all three [[Coaching Layers]] are conditioned on the archetype pairing in play.

## The quadrant

```mermaid
quadrantChart
    title Communicator Superpower Framework
    x-axis Advocate --> Analyze
    y-axis Logic --> Narrative
    Inquisitor: 0.2, 0.2
    Architect: 0.8, 0.2
    Firestarter: 0.2, 0.8
    "Bridge Builder": 0.8, 0.8
```

## The four archetypes

- **Architect** — *Logic + Analyze.* Data-first, systematic, needs structure and evidence. Prefers frameworks, numbers, and ordered arguments. Moves when the logic is airtight.
- **Firestarter** — *Narrative + Advocate.* Energy-driven, leads through story and vision. Uses metaphor and stakes to pull a room forward. Moves when a future feels vivid.
- **Inquisitor** — *Logic + Advocate.* Questions everything, challenges, needs proof. Will not concede on social pressure alone. Moves when evidence survives scrutiny.
- **Bridge Builder** — *Narrative + Analyze.* Reads the room, builds consensus through dialogue. Surfaces unspoken concerns. Moves when the group coheres.

## How archetypes are assigned

| Subject | Source | Notes |
|---|---|---|
| User | [[Backend - self_assessment]] | 8-question intake; locked unless user retakes |
| Counterpart (observed) | [[Backend - profiler]] | Rolling 5-utterance window, rule-based classification |
| Counterpart (cold start) | [[Backend - pre_seeding]] | LLM classification from free-text bio, email, or LinkedIn |

Observed classifications override pre-seeded ones once confidence ≥ 0.6. See [[Flexibility Score and CAPS]] for how archetype distribution across contexts becomes a versatility signal.

## Why two axes, not a single type

A single-label system (e.g. MBTI-style) hides the most coachable move: shifting one axis while holding the other. "You're Architect right now — stay in Logic but switch from Analyze to Advocate" is actionable. "You're a Type 3" is not.
