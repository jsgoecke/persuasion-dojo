# Architecture

Deep technical reference for Persuasion Dojo. Read this before touching any backend module.

---

## System overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        macOS host                               │
│                                                                 │
│  ┌──────────────┐  system pipe        ┌─────────────────────┐  │
│  │ Swift binary │ ──────────────────► │  system AudioPipe   │  │
│  │ (SCK + mic)  │                     │  (diarization ON)   │  │
│  │              │  mic pipe           ├─────────────────────┤  │
│  │  AudioMixer  │ ──────────────────► │  mic AudioPipe      │  │
│  │  (split out) │                     │  (no diarization)   │  │
│  └──────────────┘                     └──────────┬──────────┘  │
│                                                  │ PCM chunks  │
│                                       ┌──────────▼──────────┐  │
│                                       │ 2× Deepgram WS      │  │
│                                       │ transcription.py     │  │
│                                       └──────────┬──────────┘  │
│                                          user │  │ counterpart │
│                                       ┌──────────▼──────────┐  │
│                                       │  backend/main.py    │  │
│                                       │  SessionPipeline    │  │
│                                       │  ├─ ELMDetector     │  │
│                                       │  ├─ Profiler        │  │
│                                       │  ├─ CoachingEngine  │  │
│                                       │  ├─ SpeakerResolver │  │
│                                       │  └─ ScoringEngine   │  │
│                                       └──────────┬──────────┘  │
│                                                  │ WebSocket   │
│  ┌───────────────────────────────────┐           │             │
│  │ Electron overlay (React)          │ ◄─────────┘             │
│  │ always-on-top, user-only          │                         │
│  └───────────────────────────────────┘                         │
│                                                                 │
│                          SQLite (WAL mode)                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Audio pipeline

### Why ScreenCaptureKit

ScreenCaptureKit (macOS 12.3+) captures system audio at the OS level — it intercepts the audio mix before it leaves the device. This means it captures Zoom, Teams, Google Meet, Webex, and any browser-based meeting tool without a plugin, SDK agreement, or screen recording of video.

The Swift binary (`swift/AudioCapture/`) runs as a separate process. Its `AudioMixer` splits screen audio (counterparts) and mic audio (user) into **two named pipes** (`/tmp/persuasion_audio.pipe` for system, `/tmp/persuasion_mic.pipe` for mic). Python reads from both in `backend/audio.py` — one `AudioPipeReader` per pipe.

### Fallback

If the Screen Recording permission is revoked (macOS silently revokes it on bundle signature change after an update), `audio.py` detects the pipe going silent for >5 seconds and sends a restart signal to Electron, which relaunches the Swift binary.

### Deepgram streaming

Each pipe feeds its own Deepgram WebSocket session in `backend/transcription.py`:
- **System transcriber** (diarization ON) — counterpart audio. Deepgram labels speakers `speaker_0`, `speaker_1`, etc. The session handler prefixes these as `counterpart_0`, `counterpart_1`.
- **Mic transcriber** (diarization OFF) — user audio. All utterances get speaker ID `"user"`.

Deepgram returns `is_final: true` for committed utterances and `is_final: false` for interim partials. Only `is_final` utterances feed the coaching pipeline.

### Audio lifecycle (session start/end)

The Swift AudioCapture binary is managed by Electron's main process via IPC:

```
Session start ("Go Live"):
  Renderer → swift:start IPC → Electron main → spawnCapture()
  → kills orphans by PID (pgrep on full binary path + process.kill, not pkill — avoids race)
  → spawns fresh Swift binary → creates /tmp/persuasion_audio.pipe + /tmp/persuasion_mic.pipe
  → Python creates two AudioPipeReaders (system + mic), reusing existing FIFOs

Session end:
  Python _handle_session_end() → sends {"type": "session_ended"} over WS
  → Renderer receives session_ended → swift:stop IPC → Electron main → stopCapture()
  → SIGTERM to Swift binary (PID-specific, not pkill)
  Python AudioPipeReader.stop() → removes the named pipe (single owner)

  Also: ws.onclose calls stopCapture() as safety net (only when session_ended wasn't received).
  50ms delay before ws.close() ensures client processes session_ended before close frame.

Server shutdown (lifespan):
  Cancels tracked background tasks (debrief, playbook updates)
  Pipe cleanup owned by AudioPipeReader.stop() — not duplicated here
```

**Why this matters:** without explicit lifecycle management, the Swift binary outlives its session. Subsequent "Go Live" sessions find the old process still writing to the pipe, flooding Deepgram with stale audio. The `swift:start` → `swift:stop` cycle prevents orphaned processes.

---

## Backend modules

### `main.py` — FastAPI + WebSocket server

Session lifecycle:
1. `POST /sessions` — creates a `MeetingSession` row, returns `session_id`
2. `WS /ws/session/{id}` — Electron connects; utterances stream in, prompts stream out
3. `msg type="session_end"` — scoring runs, session row updated, WS closes
4. `GET /sessions/{id}` — debrief read (transcript, prompts, scores)

The `SessionPipeline` object is instantiated per session and owns the ELMDetector, ParticipantProfiler, and CoachingEngine for that session.

### `elm_detector.py` — ELM State Detection

Implements the Elaboration Likelihood Model (Petty & Cacioppo, 1986).

**States:**
- `ego_threat` — hostile pushback, dismissive challenges, defensive language
- `shortcut` — 3 consecutive pure-agreement utterances (≤15 words, no questions)
- `consensus_protection` — premature closure language, groupthink signals

**Transitions:**
```
neutral ──[ego signals]──────────► ego_threat
        ──[consensus signals]─────► consensus_protection
        ──[3 pure agreements]─────► shortcut

ego_threat         ──[2 neutral utts]──► neutral  (debounced)
consensus_protection ──[2 neutral utts]──► neutral  (debounced)
shortcut           ──[question/substance]──► neutral (immediate)
any state          ──[ego signals]──────► ego_threat (overrides)
```

ELM events are counted (`ego_threat_events`) and feed the Ego Safety component of the Persuasion Score.

### `profiler.py` — Participant Superpower Profiler

Two classes:

**`ParticipantProfiler`** — classifies counterparts (not the user) in real time.
- Sliding window of the last 5 utterances per speaker
- Scores each utterance on 4 signal axes: logic, narrative, advocacy, analysis
- Maps (focus_score, stance_score) → archetype using AND-based neutral band (both axes must be ambiguous for Undetermined, ±10 band) — looser than `self_assessment.py` (±15) since regex signals on real speech are sparser. When one axis is determined but the other is in the neutral band, `self_assessment.map_to_archetype()` returns partial classifications ("Logic-leaning", "Narrative-leaning", "Advocacy-leaning", "Analysis-leaning") instead of "Undetermined"
- Carry-forward: once classified, a speaker always has a label (doesn't revert to Undetermined as window rotates)

**`UserBehaviorObserver`** — accumulates all user utterances for the session.
- Produces a `SessionObservation` at session end
- `obs_confidence` grows with utterance count (exponential saturation)
- Feeds `apply_session_observation()` to update the user's 3-layer profile in the database

### `coaching_engine.py` — Claude Haiku Prompt Generation

**Cadence floors:**
- ELM-triggered: 10-second minimum between prompts
- General (self/group): 60-second minimum
- All suppressed while `user_is_speaking=True` (waits 500ms silence after `is_final`)

**Timeout and fallback:**
- Haiku call has a 1.5-second timeout
- On timeout: returns last cached prompt for that layer with `is_fallback=True`
- Overlay renders a `↻ cached` badge on fallback prompts
- If no cached prompt exists yet: returns `None` (no prompt shown)

**Prompt structure:**
System prompt locks the format to: `<WHY clause ≤8 words> — <ACTION ≤12 words, verb-first>`.
Max tokens: 80 (≈25 words). No preamble, no labels, no quotes.

**Archetype pairing advice:**
The engine receives both the user's Superpower type and the counterpart's classified type. It uses cross-archetype pairing rules (e.g. "Architect speaking to Firestarter: lead with the vision before the data") to shape the action clause.

### `scoring.py` — Persuasion Score + Growth Score + FlexibilityScore + BKT

Pure functions, no I/O.

**FlexibilityScore** — measures how well a user adapts across contexts (TRACOM Versatility). Computed as `range × appropriateness`, where range is distribution width from M2 variance and appropriateness is fraction of contexts matching ideal archetypes (with partial credit for unknown contexts). Requires min 2 contexts, 3 sessions each.

**Bayesian Knowledge Tracing (BKT)** — tracks mastery of 5 coaching skills (`elm:ego_threat`, `elm:shortcut`, `pairing:*`, `timing:talk_ratio`, `convergence:uptake`). Standard Bayesian update with conservative P(T)=0.05 learning rate.

**CAPS If-Then Signatures** — maps context → archetype from `ContextProfile` data. Stability metric measures how consistent the signature is.

**Persuasion Score** — three components:

**Timing (30%)**
Talk-time ratio (user words / total words).
- Sweet spot: 25–45% → high score
- Dominates (>60%) or silent (<15%) → low score

**Ego Safety (30%)**
Based on `ego_threat_events` count from ELMDetector.
Measures how much defensive pressure the user generated.

**Convergence (40%)**
Composite of three NLP signals (from `signals.py`):
- `vocabulary_adoption` (33%) — counterparts adopting the user's language
- `question_type_arc` (33%) — shift from challenge questions to clarifying questions
- `agreement_markers` (34%) — weighted agreement signals (substantive vs. filler)

Validated at 80% accuracy against 5 annotated real transcripts (2026-03-25).

**Growth Score:**
Delta of current Persuasion Score vs. the user's rolling EWMA baseline.
`None` until 2+ sessions exist.

### `models.py` — 3-Layer Profile Architecture

The user profile has three layers that update independently:

```
Layer 1 — Core axes (User.core_focus / User.core_stance)
  Aggregate across ALL sessions.
  Starts from self-assessment prior (confidence ≈ 0.35).
  Converges toward behavioral evidence over ~8–10 sessions.
  Tracks variance via Welford's M2 accumulator (core_focus_var, core_stance_var).

Layer 2 — Context-stratified (ContextProfile)
  One row per (user, context): board / team / 1:1 / client / all-hands.
  Used by coaching_engine once min_context_sessions (3) is reached.
  Allows the system to detect "you're more logic-dominant in board settings."

Layer 3 — Session observations (MeetingSession.obs_focus / obs_stance)
  Raw behavioral read for a single session.
  Feeds EWMA update to Layers 1 and 2.
```

**Confidence schedule:**
```
confidence = 1.0 − 0.65 × e^(−sessions / 7.0)
  clamped to [0.35, 0.95]

Sessions 0  → 0.35 (self-assessment prior dominates)
Sessions 3  → ≈0.58
Sessions 7  → ≈0.76
Sessions 15 → ≈0.91 (behavioral evidence dominates)
```

**Profile cache flush:**
Write-back on confidence delta >0.05 AND every 30 seconds (crash-safe).
Not "or session end" — crash mid-session would otherwise lose all updates.

### `linkedin.py` — LinkedIn Public Profile Scraper

Fetches public OpenGraph meta tags and JSON-LD structured data from LinkedIn profile URLs. Used by the pre-seed endpoint (`POST /participants/pre-seed`) when a URL is provided instead of (or alongside) free text. Extracts name, headline, and summary without authentication — reads only what LinkedIn renders for search engines. The regex validator anchors the URL to prevent SSRF via path traversal.

### `pre_seeding.py` — Pre-Meeting Participant Classification

Before a meeting, the user can paste a bio, email thread, LinkedIn URL, or LinkedIn blurb for a participant. The module classifies the text into a Superpower type using signal-pattern matching (same logic/narrative/advocacy/analysis axes as the profiler). Accuracy gate: must classify ≥70% of 5 known profiles correctly before deployment.

### `speaker_resolver.py` — LLM-Based Speaker Name Resolution

Maps diarized `counterpart_N` labels to real attendee names during live sessions. Runs a background asyncio task that periodically (every 60s) sends the accumulated transcript + calendar roster to Claude Haiku, which returns name → speaker_id mappings with confidence scores. High-confidence mappings (≥0.8) are locked automatically. User can confirm or edit names via the frontend, which sends a `confirm_profile` WebSocket message that permanently locks the mapping. Names are validated against the known attendees list before being accepted (LLM output trust boundary).

### `identity.py` — Speaker Identity Resolution

Maps speaker names to persistent `Participant` records across sessions. Uses fuzzy name matching (≥85% similarity ratio) against existing profiles. Rejects generic IDs (`speaker_N`, `counterpart_N`) that carry no identity signal.

### `calendar_service.py` — Google Calendar Integration

OAuth 2.0 flow (stored token, refresh handled automatically). Polls upcoming events, extracts attendee names and emails, and pre-populates the pre-seed UI before the meeting starts. Requires `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`.

### `team_sync.py` — Team Intelligence Export/Import

Exports participant profiles as AES-256 encrypted JSON. A passphrase is required to import — the passphrase is never stored. Allows a team to share intelligence about a client or counterpart without exposing raw transcript data. Malformed or tamper-detected JSON is rejected on import.

### `sparring.py` — AI Sparring Partner Mode

Text-only mode (no audio). The user types their pitch or argument; an AI opponent responds in the style of a specified Superpower type. The coaching engine fires on each exchange. Target round-trip latency: <3 seconds. The AI opponent response is streamed to reduce perceived latency.

### `retro_import.py` — Retroactive Audio + Text Transcript Processing

Processes recorded audio files (`.wav`, `.m4a`, `.mp3`) through Deepgram's REST API, or text transcripts (`.txt`, `.md`, `.json`, `.vtt`, `.srt`) via the universal parser. The `parse_text_transcript()` function auto-detects 9 formats: JSON/Deepgram, WebVTT, SRT, Teams inline VTT, Google Meet, Zoom bracket/leading timestamp, Markdown bold, and plain text. Produces a full transcript and runs the coaching pipeline retroactively, extracting participant profiles and generating a debrief.

---

## WebSocket message protocol

### Client → server

```json
{"type": "utterance", "speaker_id": "user", "text": "...", "is_final": true, "start": 12.3, "end": 14.1}
{"type": "ping"}
{"type": "session_end"}
{"type": "confirm_profile", "speaker_id": "counterpart_0", "name": "Sarah Chen"}
```

### Server → client

```json
{"type": "coaching_prompt", "layer": "audience", "text": "...", "is_fallback": false, "triggered_by": "elm:ego_threat", "speaker_id": "counterpart_0"}
{"type": "pong"}
{"type": "session_ended", "session_id": "...", "persuasion_score": 72, "growth_delta": 4.2}
{"type": "speaker_identified", "speaker_id": "counterpart_0", "name": "Sarah Chen", "confidence": 0.85}
{"type": "profile_detected", "speaker_id": "counterpart_0", "suggested_name": "Sarah Chen", "archetype": "Inquisitor", "confidence": 0.72, "is_existing": false}
{"type": "swift_restart_needed"}
{"type": "audio_level", "level": 0.42}
{"type": "no_audio", "message": "No audio detected. ..."}
{"type": "error", "message": "..."}
```

**Audio lifecycle messages:**
- `session_ended` — client stops AudioCapture on receipt (no separate stop_capture message — that design raced with ws.close()). `ws.onclose` also calls stopCapture as a safety net, but only when `session_ended` was never received (crash/disconnect).
- `swift_restart_needed` — sent when the silence watchdog fires (no audio for 5s), tells Electron to restart the Swift binary
- `audio_level` — RMS audio level (0.0–1.0), sent ~4×/sec for the sound level indicator
- `no_audio` — warning when no audio arrives within the first 5 seconds, or Deepgram connection fails

---

## Database schema (SQLite, WAL mode)

```
User
  id, email, display_name
  core_focus, core_stance          — Layer 1 axes (-1.0 to +1.0)
  core_focus_var, core_stance_var  — Welford M2 accumulators (variance = M2/n)
  core_confidence                  — 0.35 → 0.95 over sessions
  core_sessions                    — count for M2→variance conversion
  self_assessment_archetype        — prior from onboarding quiz
  total_sessions, persuasion_score_ewma, growth_score_ewma

ContextProfile
  id, user_id, context             — board / team / 1:1 / client / all-hands
  focus, stance, confidence, sessions
  focus_var, stance_var            — M2 accumulators per context

Participant
  id, name, org, email
  archetype                        — persisted Superpower type
  obs_focus_var, obs_stance_var    — M2 accumulators
  confidence, total_sessions

MeetingSession
  id, user_id, name, context, started_at, ended_at
  persuasion_score, growth_delta
  obs_focus, obs_stance            — Layer 3 session observation

SessionParticipantObservation
  id, session_id, participant_id
  convergence_score, lsm_score, pronoun_score, uptake_score

SkillMastery
  id, user_id, skill_key           — unique per (user_id, skill_key)
  p_know, p_transit, p_guess, p_slip  — BKT parameters
  opportunities, correct_count
  updated_at

CoachingPrompt
  id, session_id, speaker_id
  layer, text, triggered_by, is_fallback
  timestamp
```

---

## How the system learns profiles over time

### User profile learning

Every user has a profile on two axes: **focus** (Logic ↔ Narrative) and **stance** (Advocate ↔ Analyze). These are continuous scores from −100 to +100. The system starts with a prior from the onboarding quiz and refines it through observed behavior across every session.

**The learning pipeline runs end-to-end like this:**

```
During the session
──────────────────
UserBehaviorObserver (profiler.py)
  accumulates every user utterance
  scores each on logic / narrative / advocacy / analysis signals
  → produces SessionObservation(focus_score, stance_score, obs_confidence)

obs_confidence = f(utterance_count)   ← exponential saturation
  Few utterances (< 5)  → obs_confidence ≈ 0.2  (low weight)
  Many utterances (≥ 30) → obs_confidence ≈ 0.95 (near full weight)

At session end
──────────────
apply_session_observation(user, context_profiles, obs)
  Layer 1 update (core axes — all sessions aggregated):
    new_focus = (old_sessions × old_focus + obs_confidence × obs_focus)
                ────────────────────────────────────────────────────────
                            old_sessions + obs_confidence

  Layer 2 update (context-specific — board / team / 1:1 / client):
    same EWMA formula, applied only to the matching ContextProfile row

  Confidence update:
    confidence = 1.0 − 0.65 × e^(−sessions / 7.0)  clamped to [0.35, 0.95]
```

**Key property:** a session with few utterances (obs_confidence ≈ 0.2) contributes only 20% of a full session's weight to the aggregate. A 30-minute board meeting with 40 utterances contributes nearly a full session. This prevents sparse sessions from corrupting the profile.

**Confidence schedule — how long until behavioral evidence dominates:**

| Sessions | Confidence | What it means |
|----------|-----------|---------------|
| 0 | 0.35 | Self-assessment prior only |
| 3 | ≈0.58 | Behavioral evidence beginning to take hold |
| 7 | ≈0.76 | Roughly 50/50 prior vs. observed |
| 15 | ≈0.91 | Behavioral evidence dominant |
| 25+ | ≈0.95 | Maximum (prior contributes < 5%) |

### The three-layer profile architecture

The system doesn't just have one profile per user — it has three layers:

```
Layer 1 — Core (User.core_focus / User.core_stance)
  Aggregate across ALL sessions and ALL contexts.
  The baseline: "who you are across all your meetings."

Layer 2 — Context-stratified (ContextProfile)
  One profile per meeting type: board / team / 1:1 / client / all-hands.
  Unlocks after 3 sessions in that context (MIN_CONTEXT_SESSIONS).
  Detects situational style shifts: "you're more Logic-dominant in board meetings."

Layer 3 — Session observation (MeetingSession.obs_focus / obs_stance)
  Raw behavioral read for a single session.
  Feeds the EWMA update to Layers 1 and 2.
  Not used directly for coaching — it's the input, not the output.
```

**How the coaching engine selects which layer to use:**

```python
if context_sessions >= MIN_CONTEXT_SESSIONS (3):
    use Layer 2 (context-specific profile)
    if Layer 2 archetype ≠ Layer 1 archetype:
        context_shifts = True
        # coaching hint: "In board meetings you shift toward Firestarter —
        #  lead with the vision before the data today"
else:
    use Layer 1 (core profile)
```

### Participant profile learning

Counterpart profiles work identically — same EWMA formula, same confidence schedule, same context stratification. The difference is the signal source: participant utterances are classified by `ParticipantProfiler`, not `UserBehaviorObserver`.

**Carry-forward rule:** once a participant has been observed in at least one utterance, they always have a classification. As the sliding window (default: 5 utterances) rotates, the classification updates in place rather than reverting to "Undetermined." This prevents the overlay from going blank mid-conversation when a counterpart goes quiet for a moment.

**Persistence across meetings:** participant profiles are stored in SQLite keyed by name + org. When the same person appears in a future meeting, their profile picks up where it left off. By session 3, the system has enough signal to predict their preferred persuasion mode before they've said a word.

### Pre-seeding as a cold-start bypass

For a first meeting with someone the system has never seen, the user can paste a bio, email thread, or LinkedIn blurb before the meeting. `pre_seeding.py` classifies the text using the same signal patterns as the live profiler — giving the coaching engine a prior with which to start the audience layer immediately, rather than waiting for 5 live utterances.

Pre-seed accuracy gate: ≥70% correct classification on 5 known profiles before the feature is trusted in production.

---

## ACE Loop — Agentic Context Engineering

The coaching engine gets smarter over time through a closed adaptive loop built on three roles: **Reflector → Curator → Selector**. This is the ACE (Agentic Context Engineering) pipeline.

```
Session ends
     │
     ▼
┌─────────────┐     JSON delta entries      ┌─────────────┐
│  Reflector  │ ──────────────────────────► │   Curator   │
│  (Opus)     │   "what worked / didn't"    │  (Python)   │
└─────────────┘                             └──────┬──────┘
                                                   │ merge
                                                   ▼
                                          ┌─────────────────┐
                                          │   Bullet Store  │
                                          │   (SQLite)      │
                                          │  CoachingBullet │
                                          └──────┬──────────┘
                                                 │ top-N bullets
                                                 ▼
                                          ┌─────────────┐
                                          │  Selector   │
                                          │  (Python,   │
                                          │  <10ms)     │
                                          └──────┬──────┘
                                                 │ injected into prompt context
                                                 ▼
                                          ┌─────────────┐
                                          │ Haiku call  │
                                          │ (real-time) │
                                          └─────────────┘
                                                 │
                                          prompt effectiveness score
                                                 │
                                                 ▼
                                        helpful/harmful counters
                                        updated on bullet rows
```

### The three roles

**Reflector (Claude Opus — runs post-session, background)**
Reads the full session transcript, the prompts that were generated, and any effectiveness signals (did the conversation shift after the prompt?). Extracts up to 8 structured JSON delta entries per session — discrete lessons like:
- "When this user faces an Inquisitor in a board context, leading with a statistic before the narrative works better than the reverse"
- "Ego-threat prompts are less effective for this user when they are already in advocate mode"

**Curator (deterministic Python — no LLM)**
Merges delta entries into the bullet store without an LLM call. Deduplicates using a content hash (stop-word-stripped token set). Retires bullets where `harmful_count >= helpful_count + 2`. Caps the active store at 100 bullets. Fast, deterministic, crash-safe.

**Selector (Python relevance scoring — <10ms)**
Before each Haiku call, scores every active bullet for relevance to the current moment. Uses **Thompson Sampling** (Beta distribution draws scaled by `_W_THOMPSON=2.0`) for explore/exploit balance, plus BKT-aware penalties for mastered skills and bonuses for skills in the zone of proximal development. Weighted signals:

| Signal | Weight |
|--------|--------|
| Net helpful score (helpful − harmful) | 0.5 |
| Evidence count (sessions it's been validated on) | 0.3 |
| Archetype match (user type + counterpart type) | 3.0 |
| ELM state match | 2.5 |
| Meeting context match (board / team / 1:1) | 1.5 |
| Archetype mismatch penalty | −0.5 |
| ELM state mismatch penalty | −0.3 |

Top 15 bullets (≤500 words) are injected into the Haiku system prompt as a coaching playbook. Haiku sees only what's relevant to the current moment — no stale or contradictory advice.

### Why this architecture

A naive approach would store a growing markdown playbook and pass the whole thing to Claude every call. This breaks in three ways:
1. **Cost** — growing context window means growing token cost per prompt
2. **Noise** — stale or contradictory lessons degrade prompt quality
3. **Latency** — large context = slower Haiku calls, risks breaching the 1.5s timeout

The ACE loop keeps context small, fresh, and ranked by proven effectiveness. The Selector runs in <10ms so it adds zero perceptible latency to the real-time coaching path.

### Feedback loop

Each generated prompt is scored for effectiveness after the session (did the conversation converge? did the user's behaviour change?). Effectiveness scores update `helpful_count` / `harmful_count` on the bullet rows directly. Bullets that consistently fail get retired automatically by the Curator on the next session. This creates a self-improving system that personalises to each user over time without any manual curation.

---

## Key design decisions

**Why ScreenCaptureKit over BlackHole?**
BlackHole requires users to reconfigure their system audio output and creates a permanent virtual device. SCK captures the audio mix in software with a single permission grant. Better UX, no system reconfiguration.

**Why Deepgram over Whisper?**
Whisper processes audio in chunks (2–5s latency). Deepgram streams word-by-word with speaker diarization built in (~300ms latency). The coaching value disappears if the prompt arrives 5 seconds after the moment that triggered it.

**Why Claude Haiku for real-time prompts?**
Haiku is the fastest Claude model. At a 1.5-second timeout with a cached fallback, the overlay never blocks or goes blank — it either shows a fresh prompt or a cached one. Opus runs in the background for debrief analysis where latency doesn't matter.

**Why SQLite over Postgres?**
This is a local desktop app (MVP). SQLite in WAL mode handles concurrent reads and writes fine for one user. No server to maintain, no network dependency, no data leaving the device.

**Why named pipe over shared memory for Swift → Python audio?**
Named pipes are the simplest IPC primitive on macOS with no shared state. If the Swift binary crashes, the pipe breaks cleanly and Python detects it. Shared memory requires explicit synchronisation and cleanup.

**Why not Zoom SDK?**
Zoom SDK requires a developer agreement, review process, and limits what you can do with audio. SCK captures any meeting tool without any of that. The tradeoff is macOS-only — acceptable for the V1 target user (personal Mac).

---

## Frontend (Electron overlay)

React + Vite inside Electron. The window is `always-on-top`, transparent background (`vibrancy: 'hud'`), and non-focusable by default — clicking through it to Zoom works normally.

**Key components:**
- `Overlay.tsx` — the always-on-top coaching prompt display
- `OnboardingWizard.tsx` — 5-question Superpower assessment (first run)
- `PreSeedPane.tsx` — paste bio/email to pre-classify a participant
- `ProfilesPane.tsx` — view all known participant profiles
- `SparringPane.tsx` — AI sparring partner mode
- `RetroImportPane.tsx` — process a recorded audio file
- `CalendarPane.tsx` — Google Calendar upcoming meetings
- `TeamSyncPane.tsx` — export/import team intelligence
- `HistoryTray.tsx` — scrollable session debrief history
- `SkillBadgesPane.tsx` — growth tracking across sessions

**Design system:** defined in `DESIGN.md`. Key constraints enforced in QA:
- App background: `#1A1A1E` (never pure black)
- Gold accent: `#D4A853` for coaching intelligence and CTAs
- Typography: Playfair Display (titles) + DM Sans (body/UI) + JetBrains Mono (code/scores)
- Never use Geist, Instrument Serif, Inter, Roboto, Arial, or Helvetica

---

## Known Gaps (P1 planned follow-ups)

These are implemented-but-unwired subsystems. The infrastructure exists, but the integration points are not yet connected.

| Gap | Impact | What exists | What's missing |
|-----|--------|-------------|----------------|
| **BKT session-end integration** | `skill_mastery` table stays empty — no cross-session learning | `SkillMastery` model, `bkt_update()`, `classify_skill_opportunity()` in `scoring.py` | Session-end code in `main.py` to iterate prompt effectiveness, call BKT functions, and write rows |
| **BKT in non-ELM bullet selection** | 3 of 5 skill keys never affect prompt selection | `relevance_score()` in `coaching_bullets.py` applies BKT weight | Only fires when `bullet.elm_state` is set — `pairing:archetype_match`, `timing:talk_ratio`, `convergence:uptake` are ignored |
| **convergence:uptake skill key** | P(know) stays at 0.1 prior forever for this skill | Skill key defined in `SKILL_KEYS` | `classify_skill_opportunity()` has no code path to emit observations from convergence signals |
| **Debrief UI for flexibility data** | Flexibility Score, CAPS signature, and per-participant convergence are computed but invisible | All data computed and stored in DB at session end | No frontend panels to surface it in the post-session review screen |

See `TODOS.md` for full descriptions and `docs/designs/situational-flexibility-architecture.md` §10 for architectural context.
