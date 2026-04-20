---
title: Data Model
description: SQLAlchemy ORM schema — User, ContextProfile, Participant, MeetingSession, SessionParticipantObservation, CoachingBullet, SkillMastery.
tags: [architecture, layer/data]
type: concept
related:
  - "[[Backend - models]]"
  - "[[Backend - database]]"
  - "[[Persuasion Score]]"
  - "[[Flexibility Score and CAPS]]"
  - "[[Bayesian Knowledge Tracing]]"
updated: 2026-04-19
---

# Data Model

All tables live in SQLite (WAL mode, async via aiosqlite). Schema defined in [[Backend - models|models.py]].

## Entity-relationship (mermaid)

```mermaid
erDiagram
    User ||--o{ ContextProfile : has
    User ||--o{ MeetingSession : runs
    User ||--o{ SkillMastery : has
    MeetingSession ||--o{ CoachingPrompt : emits
    MeetingSession ||--o{ SessionParticipantObservation : records
    Participant ||--o{ SessionParticipantObservation : observed_in
    CoachingBullet }o--|| User : authored_for
    User {
        string id
        float core_focus
        float core_stance
        float core_focus_var
        float core_stance_var
        float core_confidence
        json self_assessment_snapshot
    }
    ContextProfile {
        string id
        string user_id
        string context
        float focus
        float stance
        float focus_var
        float stance_var
        int n_sessions
    }
    Participant {
        string id
        string name
        string archetype
        json voiceprint_centroid
        json behavioral_evidence
    }
    MeetingSession {
        string id
        string user_id
        timestamp created_at
        int persuasion_score
        float growth_delta
        float obs_focus
        float obs_stance
    }
    SessionParticipantObservation {
        string session_id
        string participant_id
        float convergence_score
        float lsm_score
        float pronoun_score
        float uptake_score
    }
    CoachingPrompt {
        string id
        string session_id
        string layer
        text text
        bool is_fallback
        string triggered_by
    }
    CoachingBullet {
        string id
        text body
        string dedup_key
        int helpful_count
        int harmful_count
    }
    SkillMastery {
        string user_id
        string skill_key
        float p_know
        int n_observations
    }
```

## Three-layer user profile

| layer | scope | update rule |
|-------|-------|-------------|
| **1 — Core** | `User.core_focus`, `User.core_stance` | EWMA over all sessions |
| **2 — Context** | `ContextProfile.focus` / `.stance` | EWMA per (user, context), once ≥3 sessions in that context |
| **3 — Session** | `MeetingSession.obs_focus` / `.obs_stance` | raw behavioural observation for this session only |

Confidence grows exponentially from 0.35 (prior dominant) to 0.95 (behaviour dominant) over ~15 sessions.

## Key invariants

- All profile updates go through `apply_session_observation()` in [[Backend - models|models.py]].
- `get_profile_snapshot()` returns the effective archetype for coaching based on the most trusted layer.
- Welford's M2 algorithm accumulates variance online (no need to store all session points).
- [[Backend - coaching_bullets|CoachingBullet]] rows are append-only; retirement is a flag, not a delete.
- Participant records are created only by the [[Backend - identity|identity resolver]] — guarded against technical terms becoming names.

## Migrations

`init_db()` runs on FastAPI startup and auto-adds new columns. There is no Alembic — schema evolution is code-driven and column-additive only.

## Reference

- Source: `backend/models.py`, `backend/database.py`.
- Tests: `tests/test_models.py`, `tests/test_database.py`, `tests/test_profile_benchmark.py`.
- Design background: `docs/designs/situational-flexibility-architecture.md`.
