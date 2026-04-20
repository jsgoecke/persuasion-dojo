---
title: Backend - signals
description: Convergence signal detectors (LSM, pronoun convergence, uptake ratio, question-type arc) powering the Convergence component of the Persuasion Score.
tags: [module, lang/python, layer/behavior]
type: module
module_path: backend/signals.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - scoring]]"
  - "[[Persuasion Score]]"
  - "[[Scoring Engine]]"
updated: 2026-04-19
---

# backend/signals.py

Validated-NLP convergence signal detectors. Computes Language Style
Matching, pronoun convergence, uptake ratio, and the question-type arc.
Together these power the 40% Convergence component of the
[[Persuasion Score]].

## Public surface
- `convergence_score()` — aggregate signal score
- `SignalResult` — per-signal dataclass
- `FUNCTION_WORD_CATEGORIES` — Niederhoffer & Pennebaker categories

## Imports
(stdlib only)

## Imported by
[[Backend - scoring]]

## Tests
`tests/test_signals.py`
