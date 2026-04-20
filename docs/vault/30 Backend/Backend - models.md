---
title: Backend - models
description: SQLAlchemy ORM + pure data layer — three-layer profile architecture, EWMA updates, confidence scheduling, skill mastery.
tags: [module, lang/python, layer/data]
type: module
module_path: backend/models.py
related:
  - "[[Backend Module Graph]]"
  - "[[Data Model]]"
  - "[[Backend - database]]"
  - "[[Backend - self_assessment]]"
  - "[[Backend - scoring]]"
updated: 2026-04-19
---

# backend/models.py

The project's data hub — 12+ other modules import it. Defines the three-layer profile architecture and all scoring-adjacent data types.

## Key classes

- `User` — core axes (focus/stance), self-assessment snapshot, confidence schedule.
- `ContextProfile` — per-context axis scores (board / team / 1:1 / client).
- `Participant` — counterpart records + voiceprints + behavioural evidence.
- `MeetingSession` — session + observations + Persuasion/Growth scores.
- `SessionParticipantObservation` — per-pair convergence scores.
- `CoachingPrompt` — persisted prompts for debrief.
- `CoachingBullet` — ACE bullet store with helpful/harmful counters.
- `SkillMastery` — BKT per-skill posteriors.

## Key functions

- `apply_session_observation()` — EWMA update to Layer 1 and Layer 2.
- `get_profile_snapshot()` — context-aware archetype for coaching.
- `confidence_from_sessions()` — exponential saturation [0.35, 0.95].

## Imports

[[Backend - pre_seeding]], [[Backend - self_assessment]], [[Backend - scoring]] (CAPS signature re-export).

## Imported by

[[Backend - main]], [[Backend - coaching_engine]], [[Backend - coaching_bullets]], [[Backend - coaching_memory]], [[Backend - profiler]], [[Backend - scoring]], [[Backend - elm_detector]], [[Backend - fingerprint]], [[Backend - identity]], [[Backend - seed_tips]], [[Backend - database]].

## Tests

`tests/test_models.py`, `tests/test_database.py`, `tests/test_profile_benchmark.py`.
