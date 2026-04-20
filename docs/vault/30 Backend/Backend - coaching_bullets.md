---
title: Backend - coaching_bullets
description: Structured coaching bullet store (SQLite) — Reflector / Curator / Selector roles with Thompson sampling selection and effectiveness-based retirement.
tags: [module, lang/python, layer/coaching]
type: module
module_path: backend/coaching_bullets.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - coaching_engine]]"
  - "[[Backend - models]]"
  - "[[Backend - seed_tips]]"
  - "[[ACE Loop]]"
updated: 2026-04-19
---

# backend/coaching_bullets.py

Structured coaching bullet store backed by SQLite. Replaces the monolithic
markdown playbook. Three roles collaborate: the **Reflector** (Opus,
post-session) generates bullets; the **Curator** (Python) dedups and
retires; the **Selector** runs in <10ms via Thompson sampling at coaching
time. Part of the [[ACE Loop]].

## Public surface
- `store_bullet()` — insert or dedup
- `get_bullets_for_prompt()` — Selector (Thompson sampling)
- `compute_dedup_key()` — stable hash for idempotent inserts
- `update_effectiveness()` — user feedback → helpful/harmful counts
- Retirement rule: harmful >= helpful + margin

## Imports
[[Backend - models]]

## Imported by
[[Backend - coaching_engine]], [[Backend - seed_tips]], [[Backend - main]]

## Tests
`tests/test_coaching_bullets.py`, `tests/test_coaching_quality.py`
