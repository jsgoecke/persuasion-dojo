---
title: Backend - coaching_memory
description: Legacy self-evolving markdown playbook rewritten by Opus — deprecated in favor of coaching_bullets, still wired as a last-resort fallback.
tags: [module, lang/python, layer/coaching]
type: module
module_path: backend/coaching_memory.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - coaching_bullets]]"
  - "[[Backend - coaching_engine]]"
  - "[[Backend - models]]"
updated: 2026-04-19
---

# backend/coaching_memory.py

Legacy self-evolving markdown playbook that Opus rewrites between sessions.
Deprecated in favor of the structured [[Backend - coaching_bullets]] store
but still wired as a last-resort fallback when the bullet store is empty.

## Public surface
- `update_playbook()` — Opus rewrite entry point
- `get_coaching_context()` — fetch current playbook text

## Imports
[[Backend - models]]

## Imported by
[[Backend - coaching_engine]]

## Tests
`tests/test_coaching_memory.py`
