---
title: Bayesian Knowledge Tracing
description: Per-skill mastery model using Bayesian posterior updates to replace frequency-decay badges with learning curves.
tags: [concept, topic/scoring]
type: concept
related:
  - "[[Backend - scoring]]"
  - "[[Scoring Engine]]"
  - "[[ACE Loop]]"
  - "[[Data Model]]"
updated: 2026-04-19
---

# Bayesian Knowledge Tracing

BKT (Corbett & Anderson, 1995) models the probability that a learner has acquired a latent skill, updating the posterior on each observed opportunity to apply that skill. Persuasion Dojo uses BKT to track user skill mastery, replacing the earlier frequency-decay badge system which could not distinguish *"hasn't practiced recently"* from *"hasn't yet mastered."*

## Model parameters

| Parameter | Value | Meaning |
|---|---|---|
| `P(L0)` — prior | 0.10 | probability of mastery before any observation |
| `P(T)` — transit | **0.05** | probability of learning on any given opportunity |
| `P(G)` — guess | 0.15 | correct despite not having learned |
| `P(S)` — slip | 0.10 | incorrect despite having learned |

The conservative `P(T) = 0.05` means roughly **20 correct observations** are required to reach the mastery threshold (≥ 0.95 posterior). This is intentionally slow — fast learning curves generate false confidence.

## Tracked skills

| `skill_key` | Meaning |
|---|---|
| `elm:ego_threat` | managing defensive reactions without inflaming |
| `elm:shortcut` | detecting surface agreement and deepening |
| `pairing:archetype_match` | adapting to the counterpart's Superpower |
| `timing:talk_ratio` | holding the 25–45% talk-time band |
| `convergence:uptake` | building on counterpart contributions |

Each opportunity is emitted by the signal chain and tagged **correct / incorrect** by a deterministic classifier. Opportunities are stored raw; the posterior is recomputed on read.

## Known gap (Phase 3C)

The session-end integration for BKT is **not yet implemented**:

- `SkillMastery` table rows are **never written** at session end.
- The `convergence:uptake` observation emitter is stubbed — no opportunities are ever produced.

Until Phase 3C lands, mastery dashboards render "insufficient data" for most users. See [[Roadmap and TODOs]].

## Why BKT over a neural model

- **Explainable.** A posterior is a single number the user can see and trust.
- **Tiny data regime.** A typical user produces 5–20 opportunities per session; deep models would overfit.
- **Adversarial inputs.** Unit tests cover pathological opportunity streams — BKT degrades gracefully; neural models do not.

See `tests/test_bkt.py` for convergence, skill-opportunity classification, and adversarial-input cases. Related scoring concepts: [[Persuasion Score]], [[Flexibility Score and CAPS]]. Model code and table definitions in [[Backend - scoring]] and [[Data Model]]. The [[ACE Loop]] consumes mastery scores to down-weight bullets for skills the user already has.
