---
title: Backend - seed_tips
description: One-shot seeder that loads data/seed_tips.json into the coaching_bullets table — idempotent via dedup_key.
tags: [module, lang/python, layer/coaching]
type: module
module_path: backend/seed_tips.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - coaching_bullets]]"
  - "[[Backend - database]]"
  - "[[Backend - models]]"
updated: 2026-04-19
---

# backend/seed_tips.py

Seeds the coaching bullet store from `data/seed_tips.json`. Idempotent —
each bullet is keyed by its `dedup_key`, so running the seeder twice is
safe. Typically invoked on first startup or from the CLI.

## Public surface
- `seed_tips()` → `int` count inserted

## Imports
[[Backend - database]], [[Backend - coaching_bullets]], [[Backend - models]]

## Imported by
[[Backend - main]] (on startup)

## Tests
Exercised implicitly by database-init tests in `tests/test_database.py`
and `tests/test_coaching_bullets.py`.
