---
title: Backend - pre_seeding
description: Claude Haiku classifier that pre-seeds a participant's Communicator Superpower from free-text description, email, or LinkedIn bio.
tags: [module, lang/python, layer/profile]
type: module
module_path: backend/pre_seeding.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - profiler]]"
  - "[[Backend - self_assessment]]"
  - "[[Backend - linkedin]]"
  - "[[Communicator Superpowers]]"
updated: 2026-04-19
---

# backend/pre_seeding.py

Pre-meeting participant classifier. Reads free text — description, email
body, LinkedIn bio — and returns a probable [[Communicator Superpowers]]
type with a 0.0–1.0 confidence so the coaching engine has a working
profile before the first utterance lands.

## Public surface
- `SuperpowerType` — literal: `Architect`, `Firestarter`, `Inquisitor`, `Bridge Builder`
- `PreSeedResult` — dataclass with type + confidence + rationale
- `classify()` — Claude Haiku call

## Imports
(Anthropic SDK)

## Imported by
[[Backend - profiler]], [[Backend - self_assessment]],
[[Backend - sparring]], [[Backend - main]]

## Tests
`tests/evals/pre_seeding.py`, `tests/test_pre_seeding.py`
