# Situational Archetype Flexibility — Architecture

> v0.9.0.0 · 2026-03-31
> Implements distribution-based profiles, Flexibility Score, BKT skill mastery, Thompson Sampling, and per-participant convergence.

---

## Research Foundation

Three research traditions underpin this system:

| Tradition | Source | What it says | What we build |
|-----------|--------|-------------|---------------|
| **Whole Trait Theory** | Fleeson (2001) | Traits are density distributions, not fixed points | Archetype as mean + variance per axis |
| **CAPS Model** | Mischel & Shoda (1995) | Behavior varies predictably by situation (if-then signatures) | Context → archetype mapping |
| **TRACOM Versatility** | Wilson Learning | Versatility (style adaptation) is the strongest predictor of interpersonal effectiveness | Flexibility Score = range x appropriateness |

---

## System Overview

```
                    Session N ends
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   ┌────────────┐  ┌──────────┐  ┌──────────────┐
   │ EWMA update│  │ M2 update│  │ Per-participant│
   │ (mean axes)│  │ (variance│  │ convergence   │
   │            │  │  tracking│  │ (4 NLP signals│
   │ Layer 1+2  │  │  Layer 1+│  │  per pair)    │
   └─────┬──────┘  └────┬─────┘  └───────┬──────┘
         │              │                │
         ▼              ▼                ▼
   ProfileSnapshot   FlexScore     SessionParticipant
   (mean + var)      + CAPS sig    Observation rows
         │              │                │
         ├──────────────┤                │
         ▼              ▼                ▼
   ┌─────────────────────────────────────────┐
   │          Coaching Engine (Haiku)         │
   │  - Flex note injected into self-layer   │
   │  - BKT-weighted bullet selection        │
   │  - Thompson Sampling for exploration    │
   └─────────────────────────────────────────┘
```

---

## 1. Distribution-Based Profiles

Previously, archetypes were fixed points (single focus/stance scores). Now each axis tracks **mean + M2 accumulator** using Welford's online algorithm.

### Welford M2 Update

```
_welford_m2_update(old_m2, old_mean, new_mean, new_obs, obs_confidence)
  → new_m2 = old_m2 + weight × (obs - old_mean) × (obs - new_mean)
    where weight = obs_confidence
```

M2 is the running sum of squared deviations. Variance is derived on read:

```
m2_to_variance(m2, n_sessions)
  → 0.0           if n < 2
  → max(0.0, m2/n) otherwise
```

### Where M2 Lives

| Model | Fields | Scope |
|-------|--------|-------|
| `User` | `core_focus_var`, `core_stance_var` | All sessions aggregated |
| `ContextProfile` | `focus_var`, `stance_var` | Per context (board/team/1:1/client/all-hands) |
| `Participant` | `obs_focus_var`, `obs_stance_var` | Per counterpart across all meetings |
| `ParticipantContextProfile` | `focus_var`, `stance_var` | Per counterpart per context |

All fields store M2, not variance. Named `_var` for brevity. Callers must use `m2_to_variance()` to get actual variance.

### Update Flow

At session end, `apply_session_observation()` runs two sequential updates per axis:

1. **EWMA mean** (existing): `new_mean = (n × old + confidence × obs) / (n + confidence)`
2. **M2 accumulator** (new): `new_m2 = _welford_m2_update(old_m2, old_mean, new_mean, obs, confidence)`

Order matters. M2 needs the new mean from step 1.

---

## 2. Flexibility Score

Operationalizes TRACOM Versatility as a single 0-1 number.

### Formula

```
flexibility = range_score × appropriateness_score
```

**Range score** — how much does your style vary?
```
range_score = min(1.0, sqrt(focus_var + stance_var) / 100)
```
On a -100 to +100 axis, maximum std_dev is ~100. Dividing normalizes to [0, 1].

**Appropriateness score** — does your style fit the situation?
```
For each qualified context (≥ 3 sessions):
  1.0  if archetype ∈ CONTEXT_IDEALS[context]
  0.5  if context has no defined ideal
  0.0  if archetype doesn't match
```
Average across qualified contexts.

### Context Ideals (heuristic, configurable)

```python
CONTEXT_IDEALS = {
    "board":     ["Firestarter", "Architect"],
    "team":      ["Bridge Builder"],
    "1:1":       ["Inquisitor", "Bridge Builder"],
    "client":    ["Firestarter"],
    "all-hands": ["Firestarter", "Bridge Builder"],
}
```

These are informed judgment, not empirical findings. Unknown contexts get partial credit (0.5) rather than being penalized.

### Gates

Returns `None` (insufficient data) when:
- Fewer than 2 qualified contexts
- Any context with fewer than `min_sessions_per_context` (3) sessions

### Output

```python
@dataclass
class FlexibilityScore:
    range_score: float           # 0-1, distribution width
    appropriateness_score: float # 0-1, context matching
    flexibility: float           # range × appropriateness
    dominant_contexts: list[tuple[str, str]]  # (context, archetype)
```

---

## 3. CAPS If-Then Signatures

Per Mischel & Shoda: people express consistent situation-behavior patterns ("if board meeting, then Firestarter; if 1:1, then Inquisitor").

```python
@dataclass
class CAPSSignature:
    signatures: dict[str, str]   # context → archetype
    stability: float             # 0-1, how consistent
    signature_sessions: int      # total sessions across contexts
```

**Stability** measures whether the user expresses the *same* archetype everywhere (high stability = low flexibility) or different archetypes in different contexts (low stability = high flexibility).

Computed from `ContextProfile` rows with ≥ 3 sessions each. Maps each context's (focus, stance) to an archetype via `map_to_archetype()`.

---

## 4. Per-Participant Convergence

Previously, convergence was a single aggregate score for the whole session. Now it's broken down per (user, counterpart) pair.

### How It Works

```python
per_participant_convergence(utterances, user_speaker, participant_speakers, min_utterances=5)
  → dict[speaker_id, (score, signal_results)]
```

For each participant:
1. Filter utterances to only user + that participant
2. Require ≥ 5 utterances from *each* side (below this, signal is noise)
3. Run the standard `convergence_score()` on the filtered pair
4. Return the per-pair score and individual signal results

### Four Convergence Signals

| Signal | Weight | What it detects |
|--------|--------|----------------|
| **Language Style Matching** | 35% | Function-word alignment across 8 LIWC categories, first-half vs second-half trajectory |
| **Pronoun Convergence** | 25% | Shift from I/you to we/our framing |
| **Uptake & Building-On** | 25% | Fraction of uptake markers vs resistance markers |
| **Question-Type Arc** | 15% | Audience questions evolving from challenging → clarifying → confirmatory |

### Storage

Per-pair results populate `SessionParticipantObservation`:

```
SessionParticipantObservation
  session_id, participant_id
  convergence_score    — composite (0-100)
  lsm_score           — Language Style Matching component
  pronoun_score        — pronoun convergence component
  uptake_score         — uptake/building-on component
```

---

## 5. Bayesian Knowledge Tracing (BKT)

Replaces frequency-decay skill badges with proper Bayesian mastery estimation.

### The Model

Each (user, skill) pair has four parameters:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| P(L0) `p_know` | 0.1 | Prior probability of knowing the skill |
| P(T) `p_transit` | 0.05 | Probability of learning on each opportunity |
| P(G) `p_guess` | 0.2 | Probability of getting it right without knowing |
| P(S) `p_slip` | 0.1 | Probability of getting it wrong despite knowing |

### Update Rule

Standard BKT posterior update after observing correct/incorrect:

```
If observed correct:
  P(know|correct) = P(know) × (1 - P(slip))
                    ───────────────────────────────────────
                    P(know) × (1 - P(slip)) + (1 - P(know)) × P(guess)

If observed incorrect:
  P(know|incorrect) = P(know) × P(slip)
                      ─────────────────────────────────────
                      P(know) × P(slip) + (1 - P(know)) × (1 - P(guess))

Then apply learning:
  P(know_new) = P(know|obs) + (1 - P(know|obs)) × P(transit)
```

### Skill Taxonomy (5 keys)

| Key | What it tracks |
|-----|---------------|
| `elm:ego_threat` | Managing defensive reactions in counterparts |
| `elm:shortcut` | Detecting and disrupting surface agreement |
| `pairing:archetype_match` | Adapting approach to counterpart's style |
| `timing:talk_ratio` | Maintaining optimal 25-45% talk-time balance |
| `convergence:uptake` | Building on audience contributions |

### Classification

```python
classify_skill_opportunity(triggered_by, effectiveness_score, counterpart_archetype)
  → list[(skill_key, observed_correct)]
```

Maps each coaching prompt's trigger + effectiveness to skill observations. A prompt is "correct" if `effectiveness_score ≥ 0.5`.

### Conservative Learning Rate

P(T) = 0.05 means ~20 correct observations to reach mastery (P(know) > 0.85). This is intentional. With bi-weekly sessions and ~3 coaching prompts per skill per session, convergence takes ~2-3 months of real use.

---

## 6. Thompson Sampling for Bullet Selection

The ACE loop's Selector now uses Thompson Sampling for explore/exploit balance.

### The Idea

Each coaching bullet has `helpful_count` and `harmful_count`. Instead of ranking by `helpful - harmful` (pure exploitation), draw from a Beta distribution:

```
thompson_sample_score(helpful, harmful)
  → random draw from Beta(1 + helpful, 1 + harmful)
```

Bullets with few observations have wide distributions (high variance draws). Bullets with many helpful observations concentrate near 1.0. This naturally balances exploration of uncertain bullets with exploitation of proven ones.

### Scaling

Thompson scores are in [0, 1] but deterministic relevance scores can reach 10+. The Thompson component is scaled by `_W_THOMPSON = 2.0`:

```
contextual_relevance_score = base_relevance + 2.0 × thompson_sample
```

Setting `explore=False` disables Thompson sampling entirely (backward compatible).

### BKT-Aware Bullet Weighting

When `skill_mastery` is provided to `relevance_score()`:

| Condition | Weight | Effect |
|-----------|--------|--------|
| `p_know > 0.85` (mastered) | -2.0 | Deprioritize — user already knows this |
| `0.3 ≤ p_know ≤ 0.7` (learning zone) | +1.5 | Boost — zone of proximal development |

---

## 7. Coaching Engine Integration

### Flex Note

Injected into the self-layer prompt (not ELM-triggered prompts). Descriptive, not prescriptive:

| Condition | Note |
|-----------|------|
| `focus_var + stance_var > 500` | "This person adapts their style across different contexts." |
| `< 100` and `≥ 5 sessions` | "This person tends to use the same style regardless of context." |
| 100-500 or insufficient data | No note (intentional dead zone — avoid premature labeling) |

The coaching engine decides what to *do* with this information. The flex note just provides context.

Killswitch: `_FLEX_NOTE_ENABLED = True` (set to `False` to disable without code change).

### Fingerprint Enrichment

`BehavioralFingerprint` now includes:
- `flexibility_score` — computed via `compute_flexibility_score()` from participant variance data
- `caps_signature` — context → archetype mapping

The `coaching_summary()` method appends "high situational flexibility" when `flexibility_score > 0.3`.

---

## 8. Database Schema Additions

```
User
  + core_focus_var    FLOAT DEFAULT 0.0   — M2 accumulator
  + core_stance_var   FLOAT DEFAULT 0.0   — M2 accumulator
  + core_sessions     INT   DEFAULT 0     — for M2→variance conversion

ContextProfile
  + focus_var         FLOAT DEFAULT 0.0
  + stance_var        FLOAT DEFAULT 0.0

Participant
  + obs_focus_var     FLOAT DEFAULT 0.0
  + obs_stance_var    FLOAT DEFAULT 0.0

ParticipantContextProfile
  + focus_var         FLOAT DEFAULT 0.0
  + stance_var        FLOAT DEFAULT 0.0

SessionParticipantObservation
  + convergence_score FLOAT NULL
  + lsm_score         FLOAT NULL
  + pronoun_score     FLOAT NULL
  + uptake_score      FLOAT NULL

SkillMastery (new table)
  id, user_id, skill_key (unique together)
  p_know, p_transit, p_guess, p_slip
  opportunities, correct_count, updated_at
```

All new columns have defaults. Auto-migrated by the existing `ALTER TABLE ADD COLUMN` loop in `database.py`. SQLite returns defaults for rows created before migration.

---

## 9. Key Constants

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| `_MIN_PAIR_UTTERANCES` | 5 | signals.py | Min utterances per speaker for per-pair convergence |
| `CONTEXT_IDEALS` | dict | scoring.py | Heuristic mapping of context → ideal archetypes |
| `SKILL_KEYS` | 5-tuple | scoring.py | Canonical skill taxonomy for BKT |
| `_W_THOMPSON` | 2.0 | coaching_bullets.py | Thompson Sampling scaling factor |
| `_W_SKILL_MASTERED` | -2.0 | coaching_bullets.py | Penalty for mastered skills |
| `_W_SKILL_LEARNING` | 1.5 | coaching_bullets.py | Bonus for learning-zone skills |
| `_FLEX_NOTE_ENABLED` | True | coaching_engine.py | Killswitch for flex notes |
| `_RETIRE_THRESHOLD_MARGIN` | 2 | coaching_bullets.py | Harmful ≥ helpful + 2 retires bullet |
| `_MAX_ACTIVE_BULLETS` | 100 | coaching_bullets.py | Cap on active coaching bullets |

---

## 10. Known Gaps (P1 follow-ups)

| Gap | Impact | Status |
|-----|--------|--------|
| **BKT session-end integration** (Phase 3C) | `SkillMastery` table defined but never written. No cross-session learning. | TODOS.md |
| **BKT in non-ELM bullet selection** | `relevance_score()` only applies BKT weight when `bullet.elm_state` is set. 3/5 skill keys never affect selection. | TODOS.md |
| **convergence:uptake never emitted** | `classify_skill_opportunity()` has no code path for this skill key. P(know) stays at prior forever. | TODOS.md |
| **Debrief UI** | Flexibility Score, CAPS signature, and per-participant convergence computed but not visible to users. | TODOS.md |

---

## 11. File Map

| File | What changed |
|------|-------------|
| `backend/models.py` | Welford M2 functions, variance fields on 4 models, `SkillMastery` table, `ProfileSnapshot` enrichment |
| `backend/scoring.py` | `FlexibilityScore`, `compute_flexibility_score`, `CAPSSignature`, `compute_caps_signature`, `bkt_update`, `classify_skill_opportunity`, `CONTEXT_IDEALS`, `SKILL_KEYS` |
| `backend/signals.py` | `per_participant_convergence()`, `_MIN_PAIR_UTTERANCES` |
| `backend/coaching_engine.py` | Flex note injection, `_FLEX_NOTE_ENABLED` killswitch |
| `backend/coaching_bullets.py` | `thompson_sample_score`, `contextual_relevance_score`, BKT-aware `relevance_score`, `_W_THOMPSON` |
| `backend/fingerprint.py` | `flexibility_score` + `caps_signature` in `BehavioralFingerprint` |
| `backend/main.py` | Per-participant convergence at session end, `SessionParticipantObservation` population |

---

## 12. Testing

32 new tests across 5 files:

| File | New tests | What they cover |
|------|-----------|-----------------|
| `tests/test_models.py` | 8 | Welford M2 update, variance conversion, negative clamping, snapshot variance |
| `tests/test_scoring.py` | 8 | FlexibilityScore (high/low variance, insufficient data), appropriateness, CAPS signature |
| `tests/test_signals.py` | 5 | Per-participant convergence (multi-speaker, threshold filtering, single-participant match) |
| `tests/test_coaching_engine.py` | 4 | Flex note injection (high/low variance), killswitch |
| `tests/test_bkt.py` | 7 | BKT convergence, skill classification, adversarial inputs (zero params, near-mastery) |

All 1032 tests pass (~53s). All new scoring/BKT functions are pure (no I/O, no mocks needed).
