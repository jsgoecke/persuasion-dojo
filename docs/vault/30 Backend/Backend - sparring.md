---
title: Backend - sparring
description: Text-only AI sparring partner — user vs. archetype opponent with a streamed Opus response and a parallel Haiku coaching tip, targeting <3s round-trip.
tags: [module, lang/python, layer/orchestration]
type: module
module_path: backend/sparring.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - pre_seeding]]"
  - "[[Backend - coaching_engine]]"
  - "[[Communicator Superpowers]]"
updated: 2026-04-19
---

# backend/sparring.py

Text-only AI sparring partner. The user argues against an opponent with a
chosen [[Communicator Superpowers]] archetype. Each turn runs two streams
in parallel: the opponent response (Opus, streamed) and a private
coaching tip (Haiku). Round-trip target: <3s.

## Public surface
- `SparringSession` — turn loop
- `SparringTurn` — `role` / `text` / `coaching_tip`
- `run()` → `AsyncIterator[SparringTurn]`
- Up to 10 turns per session

## Imports
[[Backend - pre_seeding]], [[Backend - coaching_engine]]

## Imported by
[[Backend - main]]

## Tests
`tests/test_sparring.py`
