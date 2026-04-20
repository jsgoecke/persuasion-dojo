---
title: Flexibility Score and CAPS
description: Cross-session versatility metric combining archetype range and context-appropriateness, grounded in CAPS If-Then signatures.
tags: [concept, topic/scoring]
type: concept
related:
  - "[[Backend - scoring]]"
  - "[[Scoring Engine]]"
  - "[[Data Model]]"
updated: 2026-04-19
---

# Flexibility Score and CAPS

Within-session scoring ([[Persuasion Score]]) answers *"how did this go?"*. FlexibilityScore answers the deeper question: *"can you shift styles when the situation requires it?"* It operationalizes TRACOM's **Versatility** construct and Whole Trait Theory's insight that adaptability, not a single fixed style, predicts long-term influence.

## Formula

```
FlexibilityScore = range_score × appropriateness_score
```

### Range score

Standard deviation of the user's archetype position across sessions:

```
range = sqrt(focus_variance + stance_variance) / 100
```

- `focus_variance` — variance on the Logic ↔ Narrative axis.
- `stance_variance` — variance on the Advocate ↔ Analyze axis.
- Division by 100 normalizes the result into a 0–1 band; capped at 1.0.

### Appropriateness score

Fraction of contexts in which the user's archetype matched the ideal for that context:

| Context | Ideal archetypes |
|---|---|
| board / investor | Architect, Firestarter |
| team / standup | Bridge Builder |
| client pitch | Firestarter |
| procurement / vendor | Inquisitor |
| 1:1 / performance | Bridge Builder |

## Gating

FlexibilityScore returns `None` unless the user has **≥ 2 qualified contexts** with **≥ 3 sessions each**. This is deliberate: a range score computed from two sessions in the same context is a noise pattern, not a trait signal.

## CAPS If-Then signatures

Mischel & Shoda's **Cognitive-Affective Personality System** frames personality as context-conditional. The scoring engine extracts If-Then rules from session history:

> *If context = board, then user archetype = Architect (72% of sessions, n=11)*

Surfaced to the user as narrative statements:

- *"You're more Logic-dominant in board settings than in 1:1s."*
- *"With Inquisitor counterparts you shift toward Analyze — that's working."*

Unlike a single fixed Superpower type, CAPS signatures are *actionable*: they describe a pattern the user can reinforce or break. See [[Communicator Superpowers]] for the underlying quadrant.

## Storage

Signatures are persisted in the `capsSignature` table keyed by `(user_id, context_tag)`. See [[Data Model]] and [[Backend - scoring]].
