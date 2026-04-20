---
title: Backend - fingerprint
description: On-demand behavioral fingerprint assembler — aggregates sessions, utterances, ELM events, and convergence signals into a rich participant portrait.
tags: [module, lang/python, layer/profile]
type: module
module_path: backend/fingerprint.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - models]]"
  - "[[Backend - profiler]]"
  - "[[Backend - elm_detector]]"
  - "[[Backend - signals]]"
updated: 2026-04-19
---

# backend/fingerprint.py

On-demand assembly of a participant's behavioral fingerprint. Rather than
materializing a heavy denormalized table, it queries evidence across
sessions, utterances, ELM events, and convergence signals and returns a
portrait for the UI and for coaching context.

## Public surface
- `BehavioralFingerprint` — output dataclass
- `get_fingerprint()` — async query (not materialized)
- `ContextVariation` — per-context deltas
- `NotableUtterance` — highlighted evidence

## Imports
[[Backend - models]]

## Imported by
[[Backend - main]]

## Tests
`tests/test_fingerprint.py`
