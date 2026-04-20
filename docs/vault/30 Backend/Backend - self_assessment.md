---
title: Backend - self_assessment
description: 12-item Likert self-assessment that classifies the user's own Communicator Superpower with optional Haiku micro-argument analysis.
tags: [module, lang/python, layer/profile]
type: module
module_path: backend/self_assessment.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - pre_seeding]]"
  - "[[Backend - profiler]]"
  - "[[Backend - scoring]]"
  - "[[Communicator Superpowers]]"
updated: 2026-04-19
---

# backend/self_assessment.py

Classifies the user's own [[Communicator Superpowers]] archetype from a
12-item Likert instrument on two axes — Focus (Logic/Narrative) and Stance
(Advocate/Analyze). Confidence is derived from MAD consistency and timing;
a neutral band of ±15 around center is reported when evidence is thin.

## Public surface
- `ITEMS` — the 12 Likert items
- `score_responses()` — raw Likert → axis scores
- `classify_micro_argument()` — optional Haiku analysis of a written argument
- `map_to_archetype()` — axes → Superpower
- `build_result()` → `SelfAssessmentResult`

## Imports
[[Backend - pre_seeding]]

## Imported by
[[Backend - profiler]], [[Backend - scoring]], [[Backend - main]]

## Tests
`tests/test_self_assessment.py`
