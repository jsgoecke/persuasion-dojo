---
title: Backend - coaching_engine
description: Real-time coaching prompt generator — uses Claude Haiku plus the structured bullet store to adapt pre-written tips across self / audience / group layers with a 1.5s timeout and fallback.
tags: [module, lang/python, layer/coaching]
type: module
module_path: backend/coaching_engine.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - elm_detector]]"
  - "[[Backend - coaching_memory]]"
  - "[[Backend - coaching_bullets]]"
  - "[[Backend - profiler]]"
  - "[[Backend - models]]"
  - "[[Coaching Engine Architecture]]"
  - "[[ACE Loop]]"
  - "[[Coaching Layers]]"
  - "[[Cadence Rules]]"
updated: 2026-04-19
---

# backend/coaching_engine.py

Real-time coaching engine. Selects personalized tips from the bullet store
and adapts them with Claude Haiku across three simultaneous layers: self,
audience, and group. Enforces a 1.5s timeout with a cached fallback and
applies cadence floors so prompts never fire too fast.

## Public surface
- `CoachingEngine` — entry point
- `CoachingPrompt` — output dataclass
- ELM-triggered floor: 10s (counterpart utterances only)
- General cadence floor: 15s

## Imports
[[Backend - elm_detector]], [[Backend - coaching_memory]],
[[Backend - coaching_bullets]], [[Backend - models]], [[Backend - profiler]]

## Imported by
[[Backend - main]], [[Backend - sparring]]

## Tests
`tests/test_coaching_engine.py`, `tests/test_coaching_quality.py`
