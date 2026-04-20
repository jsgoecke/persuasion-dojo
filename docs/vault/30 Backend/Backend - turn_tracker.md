---
title: Backend - turn_tracker
description: Vocative-bootstrapped turn adjacency tracker — extracts name mentions ("Thanks Greg") and links them to the next speaker, giving a zero-API-cost speaker signal.
tags: [module, lang/python, layer/identity]
type: module
module_path: backend/turn_tracker.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - speaker_resolver]]"
  - "[[Backend - identity]]"
updated: 2026-04-19
---

# backend/turn_tracker.py

Vocative-bootstrapped turn tracker. When one speaker says "Thanks, Greg",
the next turn is probably Greg's — this module extracts vocative mentions
and links them to the following speaker via a turn-gap heuristic. No API
calls, so it runs for free alongside the LLM resolver.

## Public surface
- `TurnTracker` — main class
- `add_turn()` — feed a new diarized turn
- `get_name_scores()` → `dict[speaker_id, dict[name, score]]`
- Cold-start threshold: 3 linked vocatives before trusting a mapping

## Imports
(stdlib only)

## Imported by
[[Backend - speaker_resolver]]

## Tests
`tests/test_turn_tracker.py`
