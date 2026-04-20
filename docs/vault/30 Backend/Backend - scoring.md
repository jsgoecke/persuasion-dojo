---
title: Backend - scoring
description: Persuasion Score (Timing 30% + Ego Safety 30% + Convergence 40%), Growth Score, Flexibility, BKT skill mastery, and CAPS signatures — pure functions.
tags: [module, lang/python, layer/coaching]
type: module
module_path: backend/scoring.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - signals]]"
  - "[[Backend - models]]"
  - "[[Backend - self_assessment]]"
  - "[[Scoring Engine]]"
  - "[[Persuasion Score]]"
  - "[[Flexibility Score and CAPS]]"
  - "[[Bayesian Knowledge Tracing]]"
updated: 2026-04-19
---

# backend/scoring.py

Pure-function scoring layer. Computes the headline Persuasion Score
(Timing 30% + Ego Safety 30% + Convergence 40%), the per-session Growth
Score, the Flexibility Score, Bayesian Knowledge Tracing skill mastery,
and CAPS (Cognitive-Affective Personality System) signatures.

## Public surface
- `compute_persuasion_score()` → `int` 0–100
- `compute_growth_score()` → `float | None`
- `TimingComponent`, `EgoSafetyComponent`, `ConvergenceComponent`
- `CAPSSignature`, `compute_caps_signature()`
- Skill badges for BKT mastery

## Imports
[[Backend - models]], [[Backend - signals]], [[Backend - self_assessment]]

## Imported by
[[Backend - main]]

## Tests
`tests/test_scoring.py`, `tests/test_bkt.py`
