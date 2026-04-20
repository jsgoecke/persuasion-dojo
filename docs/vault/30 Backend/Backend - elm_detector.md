---
title: Backend - elm_detector
description: ELM state machine detecting ego threat, shortcut (peripheral route), consensus protection, and neutral — feeds coaching triggers and scoring penalties.
tags: [module, lang/python, layer/behavior]
type: module
module_path: backend/elm_detector.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - coaching_engine]]"
  - "[[Backend - scoring]]"
  - "[[ELM State Detection]]"
updated: 2026-04-19
---

# backend/elm_detector.py

ELM (Elaboration Likelihood Model) state detector. Classifies each
utterance into `ego_threat`, `shortcut`, `consensus_protection`, or
`neutral`, and drives both high-priority coaching triggers and the Ego
Safety component of the [[Persuasion Score]].

## Public surface
- `ELMDetector` — state machine
- `ELMState` — literal type
- `ELMEvent` — dataclass emitted to consumers
- 2-consecutive-neutral debounce before leaving a charged state

## Imports
(stdlib only)

## Imported by
[[Backend - coaching_engine]], [[Backend - scoring]], [[Backend - main]]

## Tests
`tests/test_elm_detector.py`
