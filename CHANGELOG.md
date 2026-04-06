# Changelog

All notable changes to this project will be documented in this file.

## [0.9.2.0] - 2026-04-05

### Fixed
- Participant profiles no longer display "Unknown" archetype when observation data exists on only one axis. The archetype fallback logic now correctly skips the truthy sentinel string "Undetermined" and falls through to the pre-seeded classification.
- Deleting meetings from the Recent card now persists across navigation. The delete endpoint previously used ORM cascade which triggered async lazy-load errors (MissingGreenlet), silently rolling back the transaction. Now uses explicit Core DML deletes for all related rows.

### Added
- Partial archetype classifications: when one communication axis is determined but the other lacks data, profiles show "Logic-leaning", "Narrative-leaning", "Advocacy-leaning", or "Analysis-leaning" in muted colors instead of "Undetermined". Full archetype resolves as more session data accumulates.
- 9 regression tests covering the archetype fallback logic and session delete persistence with full related-row cleanup.

## [0.9.1.0] - 2026-04-05

### Fixed
- Participant profiler no longer classifies strong single-axis speakers as "Unknown." A data-heavy speaker with clear Logic signals but no Advocacy/Analysis signals now gets a tentative Architect classification instead of staying Undetermined permanently. The profiler now uses AND-based neutral band logic (both axes must be ambiguous) rather than OR-based (either axis ambiguous), with a tighter ±10 band tuned for sparse real-speech regex signals.

### Added
- 10 convergence tests proving all 4 archetypes are reachable within 5 realistic utterances, confidence increases monotonically, noisy starts converge after window eviction, and cross-session EWMA convergence works for initially-undetermined participants.

## [0.9.0.0] - 2026-03-31

### Added
- **Distribution-based archetype profiles:** Archetypes are now modeled as density distributions (mean + variance per axis) instead of fixed points, tracking how your communication style varies across contexts. Grounded in Whole Trait Theory (Fleeson).
- **Flexibility Score:** Measures how well you adapt your style to different meeting types. Combines distribution range with context appropriateness, operationalizing TRACOM SOCIAL STYLE Versatility.
- **CAPS if-then signatures:** Maps which archetype you use in which context (board meetings, 1:1s, client calls), based on Mischel & Shoda's situation-behavior model.
- **Bayesian Knowledge Tracing (BKT):** Tracks mastery of 5 coaching skills (ego threat handling, shortcut detection, archetype pairing, timing, convergence) using proper Bayesian updates instead of frequency-decay badges.
- **Thompson Sampling for coaching bullet selection:** Explore/exploit optimization that balances showing proven-helpful bullets with testing under-explored ones.
- **Per-participant convergence scoring:** Breaks down aggregate conversation convergence into per-counterpart scores, so you can see which relationships are building alignment.
- **Flexibility-aware coaching notes:** Real-time coaching prompts now include context about whether you naturally flex across situations or tend to stay in one mode.
- **SkillMastery persistence:** Each coaching skill tracks its own learning curve per user, with a conservative learning rate (P(T)=0.05) that requires consistent evidence before marking a skill as mastered.

### Changed
- Welford variance tracking upgraded to numerically stable M2 accumulator (stores running sum of squared deviations, derives variance on read).
- ProfileSnapshot now includes focus_variance and stance_variance derived from M2.
- Coaching flex notes are descriptive ("adapts their style") not prescriptive ("lean into it"), letting the coaching engine decide the action.
- CONTEXT_IDEALS mapping documented as heuristic with partial credit for unknown contexts.
- Per-participant convergence minimum utterance threshold raised to 5 per side for meaningful signal detection.
