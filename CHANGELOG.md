# Changelog

All notable changes to this project will be documented in this file.

## [0.11.1.0] - 2026-04-11

### Added
- WeSpeaker ECAPA-TDNN voiceprint extraction module with custom numpy fbank computation (no torchaudio dependency). Extracts 256-dim speaker embeddings from system audio segments.
- Voiceprint confidence boost: when a speaker's voice matches a known participant's stored voiceprint (cosine similarity > 0.7), resolver confidence gets +0.15 boost, capped below lock threshold to prevent auto-lock.
- Voiceprint centroid persistence: at session end, computes speaker centroid with outlier rejection and EMA-updates the Participant DB for cross-session voice recognition.
- Timestamped audio ring buffer (5 min rolling + 30s pinned intro) for voiceprint segment extraction.
- Separate mic and system audio ring buffers so voiceprint extraction only uses counterpart voices.
- Tap-to-edit popover on live session participant pills with Save/Cancel/Escape flow.
- Gold "?" confidence badge on participant pills when resolver confidence < 0.7, with hysteresis deadband (0.8 to clear).
- Confidence-based prompt suppression: coaching prompts use "the current speaker" when resolver confidence < 0.7 instead of a potentially wrong name.
- Adaptive resolver scheduling: 10s during intro phase (0-120s), 15s default, 60s when all speakers locked.
- Resolver accuracy metrics (total resolutions, user corrections, time to first resolution) for Phase 3 decision gate.
- 34 new tests: 22 for speaker embeddings (fbank, cosine sim, EMA, centroid, boost via _resolve_once), 12 for audio buffer methods.

### Fixed
- Voiceprint extraction now runs in thread pool via `run_in_executor` instead of blocking the event loop.
- Fire-and-forget voiceprint tasks now stored with done callbacks and awaited at session end.
- Audio segment extraction deduplicates overlapping intro and rolling buffer chunks by timestamp.
- Voiceprint matching uses best-match (max similarity) instead of first-match.
- `confirm_profile` now persists corrections to Participant DB (was only updating in-memory resolver).
- Outlier rejection disabled for small embedding counts (< 5) to avoid discarding useful data.
- `uncertainSpeakers` ref cleared on session reset to prevent stale hysteresis across sessions.
- Explicit `SessionPipeline` fields replace monkey-patched attributes for resolver and voiceprint state.

## [0.11.0.0] - 2026-04-10

### Added
- Cache-rank-rotate coaching engine: pre-written coaching bullets are selected by relevance score and personalized by Haiku in real time, replacing from-scratch generation. Eliminates refusals and reduces latency.
- User feedback loop: thumbs-up/down on coaching prompts adjusts bullet scores (2x weight vs auto-scoring). Harmful bullets auto-retire.
- Coaching bullet store with ACE lifecycle: Selector picks best bullet per moment, Curator merges session learnings, Reflector extracts patterns via Opus post-session.
- Layer diversity boost: if recent prompts were all self-layer, audience and group layers get priority to prevent coaching tunnel vision.
- Diversity-preserving cap enforcement: when bullet store exceeds 250, retires lowest-scoring bullets while protecting at least 5 per meeting context type and counterpart archetype.
- 132 seed coaching tips across all 3 layers (self/audience/group) and 4 archetypes, warm-started at helpful_count=1.
- Speaker resolver Phase 1: 10 targeted fixes for faster, more accurate speaker identification during live sessions.
- Speaker resolution interval dropped from 60s to 15s so names appear before introductions scroll away.
- Context window preserves first 20 utterances (introductions) plus last 80 in long meetings.
- Fuzzy name matching (SequenceMatcher, 0.85 threshold) so "Sarah Chen" from Claude matches "Sarah L Chen" on the calendar.
- Cross-session speaker memory: pre-seeds known names from Participant DB (last 90 days) so returning attendees are recognized instantly.
- Confidence decay with flip-flop guard: same-name confidence can drift down, but a different name must strictly beat existing confidence to prevent Alice/Bob oscillation.
- Speaker mappings persist each resolution cycle via callback (crash-safe, no longer lost on session crash).
- Deepgram model upgraded from nova-2 to nova-3 for improved diarization accuracy.
- Utterance dedup in session pipeline: exact duplicate text from same speaker is suppressed.
- Playbook filter strips scoring metrics and markdown tables before sending to Haiku.
- Frontend prompt feedback UI with thumbs-up/down buttons and WebSocket feedback_ack protocol.

### Changed
- Coaching system prompt redesigned for personalization flow: Haiku adapts pre-written tips rather than generating from scratch.
- Bullet store cap raised from 100 to 250 active bullets for long-term users.
- Speaker resolver skips API calls when no new utterances arrive (saves ~50% of Haiku calls during quiet periods).

### Fixed
- `is_fallback` flag now correctly set when Haiku times out and verbatim bullet is shown, restoring the fallback badge in the overlay.
- `datetime.utcnow()` replaced with `datetime.now(timezone.utc)` in speaker persistence callback (deprecated in Python 3.12+).
- `None` values in known_names list no longer cause fuzzy matching errors (filtered at init).

## [0.10.2.0] - 2026-04-09

### Added
- Opening coaching prompt fires at session start with personalized welcome: user name, archetype profile, participant roster with pairing advice, and learned coaching bullets from prior sessions.
- Self-layer coaching now fires on user utterances, not just counterpart turns. Enables "you've been advocating for 4 minutes, ask a question" style coaching.
- Session-end safety net: if backend crashes during scoring, overlay still shows debrief screen with a fallback result instead of hanging.
- 4 new initial prompt tests covering context shifts, confidence lines, fingerprint data, and whitespace-only name edge case.

### Changed
- General coaching cadence floor reduced from 30s to 15s for faster feedback in fast-moving meetings.
- ELM-triggered prompts (audience layer) remain counterpart-only. Self-layer general prompts fire on both speakers.

### Fixed
- `user_display_name.split()[0]` crash when display name is whitespace-only (e.g. `"   "`).
- CoachingEngine `user_id` was not passed from session handler, preventing ACE coaching bullets from loading.
- Frontend fallback `persuasion_score` changed from `0` to `null` so fallback results are distinguishable from real zero scores.
- `test_missing_deepgram_key_cloud_mode_closes_cleanly` patched to clear `os.environ` so real Deepgram key doesn't leak through in test.

## [0.10.1.0] - 2026-04-08

### Added
- Per-person real-time coaching: coaching prompts now name the specific counterpart and tailor advice to their Superpower archetype pairing with yours. "Sarah is an Inquisitor, lead with data" instead of generic tips.
- Calendar auto-seed at session start: when a Google Calendar meeting is happening now (or within 15 minutes), attendees are automatically populated as session participants with archetype lookup.
- User archetype auto-detection: your own Superpower type is inferred from your speech patterns during sessions and persists across sessions via profile cache.
- Post-session debrief: generates per-participant relationship summaries, pairing dynamics, and retro coaching bullets fed back into the ACE coaching store.
- `classify_from_scores()` extracted as a public function in `profiler.py`, eliminating 3x duplication of quadrant classification logic.
- Frontend coaching cards show per-person badges with counterpart name and archetype.
- Echo filter prevents your own voice (picked up by ScreenCaptureKit on system audio) from creating false counterpart utterances. Uses word overlap against recent mic transcripts.
- 50 tests covering the full signal chain, echo filter, archetype classification, debrief cap, retro bullets, and plain-English prompt verification.

### Changed
- Coaching prompts now use plain English instead of academic terminology. "They feel attacked" instead of "Central Route shut down." The underlying ELM detection logic is unchanged.
- Post-session debriefs use the same plain language ("thinking it through", "going along", "defensive") instead of ELM framework terms.

### Fixed
- Race condition in observer signal snapshot: signals list is now copied before iteration to prevent modification during async processing.
- ELM episode history accessed via public `get_episode_history()` instead of private `_episode_log`.
- Bare `except Exception` blocks in calendar auto-seed now log errors via `logger.debug`.
- Debrief functions cap participants at 10 (sorted by utterance count) to prevent prompt bloat.
- Coaching engine user archetype accessed via property instead of private attribute.
- Removed dead `_score_utterance` import from `main.py`.
- Calendar auto-seed captures `now` before async API call to prevent clock drift.

## [0.10.0.0] - 2026-04-05

### Added
- Hybrid transcription: sessions now automatically fall back to local Moonshine transcription when Deepgram is unavailable. Three modes: "auto" (try cloud, fall back to local), "cloud" (Deepgram only), "local" (Moonshine only). Auto mode is the default.
- Deepgram pre-session health check validates API key and connectivity before starting transcription. Failed health checks trigger automatic Moonshine fallback in auto mode.
- Mid-session failover: if Deepgram exhausts reconnect attempts during a live session, audio seamlessly switches to Moonshine with a ring buffer replay of the last ~5 seconds of audio so no context is lost.
- Exponential backoff with jitter for Deepgram reconnects (base * 2^failures, capped at 30s). Increased max reconnect attempts from 5 to 8.
- Ring buffer (deque, ~5s of 50ms chunks) stores recent audio for replay after reconnect or failover.
- Transcriber status events pushed to the frontend WebSocket ("using_cloud", "using_local", "fallback_activated", "reconnecting", "reconnected", "exhausted") so the overlay can show connection state.
- `transcription_mode` parameter on session creation lets users choose their preferred transcription backend.

### Changed
- Transcriber protocol extracted to `backend/transcriber_protocol.py`, enabling swappable transcription backends without changing downstream consumers.
- Audio pipeline in `main.py` now uses `HybridTranscriber` instead of direct `DeepgramTranscriber` instantiation.
- Missing Deepgram API key is no longer fatal in auto/local mode. Only cloud mode requires a valid key.

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
