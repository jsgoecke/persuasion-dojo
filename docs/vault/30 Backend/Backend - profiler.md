---
title: Backend - profiler
description: Classifies counterparts into a Communicator Superpower type using a rule-based 5-utterance window, plus a user-behavior observer that accumulates session observations.
tags: [module, lang/python, layer/behavior]
type: module
module_path: backend/profiler.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - pre_seeding]]"
  - "[[Backend - self_assessment]]"
  - "[[Backend - models]]"
  - "[[Communicator Superpowers]]"
updated: 2026-04-19
---

# backend/profiler.py

Two collaborating responsibilities: `ParticipantProfiler` classifies
counterparts into one of the four [[Communicator Superpowers]] using a
rolling 5-utterance window; `UserBehaviorObserver` accumulates the user's
own utterances into a `SessionObservation` for downstream scoring. Signals
are regex-based over four axes — Logic, Narrative, Advocacy, Analysis.

## Public surface
- `ParticipantProfiler` — 5-utterance sliding window classifier
- `UserBehaviorObserver` — aggregates user utterances into a session view
- `WindowClassification`, `SessionObservation` — data classes

## Imports
[[Backend - models]], [[Backend - pre_seeding]], [[Backend - self_assessment]]

## Imported by
[[Backend - coaching_engine]], [[Backend - main]]

## Tests
`tests/test_profiler.py`, `tests/test_phase1_signal_chain.py`
