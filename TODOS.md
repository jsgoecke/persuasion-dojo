# TODOS

Deferred work captured from /plan-ceo-review (2026-03-25, SCOPE EXPANSION mode) and /plan-eng-review (2026-03-25).

---

## P0 — Gates (must complete before writing dependent code)

### ~~Convergence validation spike~~ ✅ COMPLETE
**What:** Run `scripts/convergence_spike.py` against real meeting transcripts. Pass criterion: ≥75% signal agreement.
**Completed (2026-03-25):**
- 1 real Granola transcript tested (`001_design_practices_mar10` — Sailplane design practices meeting)
- Result: **3/3 signals correct (100% agreement)** — all three signals correctly called "converging"
- Signal refinements made during spike: question_type_arc now detects Path B (collaborative → confirmatory arc, not just adversarial → clarifying). False positive pattern `\bbut (what|how|why|if|when)\b` narrowed to prevent "but if" in conditionals triggering as adversarial.
- `scripts/convert_granola.py` — converter from Granola transcript format → spike format (named speakers, no timestamps → utterances with synthetic timestamps)
- `scripts/spike_transcripts/` — real transcript + annotation pairs used in validation

**Next step:** Build `scoring.py` — convergence gate is cleared.
**Priority:** P0 — UNBLOCKED — build scoring.py next

### ~~Design system — run /design-consultation~~ ✅ COMPLETE
**What:** `DESIGN.md` written to repo root on 2026-03-25. Full design system: Instrument Serif + Geist typography, three-color layer badge system, `vibrancy: 'hud'` overlay, #1C1C1E/#FAFAF9 surfaces, 8px spacing, intentional motion. CLAUDE.md updated with enforcement rules.
**Status:** DONE — frontend implementation unblocked.

### ~~Pre-seed accuracy gate~~ ✅ COMPLETE
**What:** Classify 5+ participants with known Superpower types and verify ≥70% accuracy. Pass criterion: ≥70% correct.
**Completed (2026-03-25):**
- 5 real Sailplane team members tested (Firestarter, Architect, Inquisitor ×2, Bridge Builder)
- Result: **5/5 correct (100%)** — all four types classified correctly
- Descriptions sourced from `persuasion_dojo_profiles.md` (behavioral evidence from 5 meeting transcripts)
- `scripts/real_world_gate.py` — TEST_CASES populated and passing

**Next step:** `pre_seeding.py` is cleared for deployment.
**Priority:** P0 — UNBLOCKED — both P0 gates now complete

---

## P1 — Ship soon after V1

### ~~Sentry crash reporting (Electron)~~ ✅ COMPLETE
**Completed (2026-03-25):** Full `frontend/overlay/` scaffold created with `@sentry/electron/main` in the main process, `@sentry/electron/renderer` + `@sentry/react` ErrorBoundary in the renderer, `sentryVitePlugin` for source map uploads gated on `SENTRY_AUTH_TOKEN`, 3 Vitest tests. Release tied to `app.getVersion()`.

### ~~electron-updater notarization validation~~ ✅ COMPLETE
**Completed (2026-03-25):**
- `frontend/overlay/notarize.cjs` — afterSign hook using `@electron/notarize` (notarytool). Gracefully skips when `APPLE_ID` is absent (local dev / CI without secrets). Throws with an actionable message if `APPLE_ID` is set but other credentials are missing.
- `frontend/overlay/scripts/verify-notarization.sh` — post-build script that runs `codesign --verify --deep --strict` + `spctl --assess` against every `.dmg` in `dist/`, then cross-checks the artifact listed in `latest-mac.yml` is among the verified files. Fails the release if any DMG is rejected.
- `.github/workflows/release.yml` — macOS-14 CI workflow triggered on `v*` tags: imports signing cert into a temp keychain → `npm run package -- --publish always` (signs + notarizes via hook + publishes to GitHub Releases) → runs verify-notarization.sh → cleans up keychain.
- `electron-builder.json` — `afterSign: "./notarize.cjs"`, removed `"notarize": false`.
- `package.json` — added `@electron/notarize@^2.5.0` devDependency.

### ~~Retroactive import: progress indicator + cancellation~~ ✅ COMPLETE
**Completed (2026-03-25):** `ProgressCallback` and `cancel_event: asyncio.Event` added to `retro_import.py`. Pre-counts non-empty utterances so denominator is correct from the first call. Cooperative cancel checked before each utterance. 9 new tests (39 total, 0.05s).

---

## P2 — V2 (after individual validation)

### ~~Google Calendar push webhooks (replace polling)~~ ✅ COMPLETE
**Completed (2026-03-25):**
- `backend/calendar_service.py` — `WatchChannel` dataclass with `is_active`/`needs_renewal`/`expires_at` properties; `register_push_watch()`, `stop_push_watch()`, `active_watch`, `is_watch_active`; watch state persisted to `~/.persuasion_dojo_watch.json` (survives restarts); `_httpx_post` updated to support both `data=` (form-encoded) and `json=` (JSON body); 204 No Content handled.
- `backend/main.py` — `GET /calendar/watch`, `POST /calendar/watch`, `DELETE /calendar/watch`, `POST /calendar/webhook` (handles Google's `sync` handshake and `exists` change notifications); `WatchRequest`/`WatchResponse` Pydantic schemas; `_get_calendar_service()` singleton from env vars.
- `tests/test_calendar_service.py` — 18 new tests: `TestWatchChannelProperties`, `TestRegisterPushWatch`, `TestStopPushWatch`, `TestWatchPersistence`. Total: 61 tests, 0.09s.
**Note:** `POST /calendar/webhook` requires a publicly reachable URL — gated on cloud backend (V2). V1 polling continues to work unchanged.

### ~~Apple MDM configuration profile~~ ✅ COMPLETE
**Completed (2026-03-25):**
- `resources/mdm/PersuasionDojo.mobileconfig` — signed Privacy Preferences Policy Control payload (`com.apple.TCC.configuration-profile-policy`) granting ScreenCapture (main app + SCK helper) and Microphone permissions for `com.persuasiondojo.overlay`. Comments explain each field, deployment steps, and payload UUID convention.
- `scripts/build-mdm-profile.sh` — signs the profile with `security cms -S` using a Developer ID Application certificate (auto-detected from system/login keychains; overridable via `SIGN_CERT=`). Validates the plist, creates `dist/`, verifies the signature post-signing, and prints MDM upload + manual install instructions. `chmod +x` applied.
**Deploy:** `./scripts/build-mdm-profile.sh` → upload `dist/PersuasionDojo-signed.mobileconfig` to Jamf/Mosyle/Kandji. Requires a valid Apple Developer signing certificate in the Keychain.

---

## P1 — Situational Flexibility Follow-ups

### Wire convergence:uptake skill key to BKT
**What:** `classify_skill_opportunity()` currently doesn't emit `convergence:uptake` observations. Convergence signals come from `signals.py` (different code path than coaching prompts). Wire the convergence signal results into BKT at session end so this skill key can track mastery.
**Priority:** P1
**Context:** Identified during /ship pre-landing review (2026-03-31). The skill key exists in SKILL_KEYS but never receives BKT observations, so P(know) stays at 0.1 prior forever.

### Wire BKT session-end integration (Phase 3C)
**What:** `SkillMastery` model and pure functions (`classify_skill_opportunity`, `bkt_update`) exist but are never called from `main.py`. Need to add session-end code that: (1) iterates prompt effectiveness scores, (2) calls `classify_skill_opportunity()` for each, (3) calls `bkt_update()`, (4) creates/updates `SkillMastery` rows in the database. Without this, the BKT system has no memory across sessions and the `skill_mastery` table stays empty.
**Priority:** P1
**Context:** Identified during adversarial review of feat/situational-flexibility (2026-03-31). Phase 3C was planned but not implemented.

### Wire BKT into non-ELM bullet selection
**What:** `relevance_score()` in `coaching_bullets.py` only applies BKT weighting when `bullet.elm_state` is set. This means 3 of 5 skill keys (`pairing:archetype_match`, `timing:talk_ratio`, `convergence:uptake`) never affect bullet selection even after BKT session-end integration is wired. Need to map bullets to skill keys by more than just `elm_state`.
**Priority:** P1
**Context:** Identified during adversarial review of feat/situational-flexibility (2026-03-31).

### Debrief UI for flexibility data
**What:** Flexibility Score, CAPS signature, and per-participant convergence are computed but invisible to users. Need debrief view panels to surface this data.
**Priority:** P1
**Context:** Identified in CEO review finding #3. All the data is computed and stored, but there's no frontend to show it.

---

## P1 — Speaker Identification Follow-ups

### Deepgram multichannel=true investigation
**What:** Investigate whether Deepgram `multichannel=true` with dual audio streams (mic + system) improves diarization accuracy vs current single mixed-audio stream. We already capture both streams separately via ScreenCaptureKit.
**Priority:** P1
**Context:** Deferred from Speaker ID Phase 1 /autoplan CEO review (2026-04-10). Outside Phase 1 blast radius (audio pipeline change).

### Nova-3 validation on real SCK audio
**What:** Validate Deepgram nova-3 diarization accuracy on real ScreenCaptureKit-captured audio before shipping to users. Nova-3 benchmarks are on clean recordings, not SCK mixed audio.
**Priority:** P1
**Context:** Deferred from Speaker ID Phase 1 /autoplan CEO review (2026-04-10). Critical to confirm the nova-2 → nova-3 upgrade doesn't regress.

### ~~Manual speaker tagging fallback UX~~ ✅ COMPLETE
**Completed (2026-04-11):** Tap-to-edit popover on live session participant pills with Save/Cancel/Escape flow. Gold "?" confidence badge when resolver confidence < 0.7, with hysteresis deadband (0.8 to clear). Confidence-based prompt suppression uses "the current speaker" when confidence < 0.7.
**Context:** Implemented in Speaker ID Phase 2 (feat/coaching-quality-cache-rank-rotate).

### ~~Adaptive resolver scheduling~~ ✅ COMPLETE
**Completed (2026-04-11):** 10s during intro phase (0-120s), 15s default, 60s when all speakers locked. Resolver skips API calls when no new utterances arrive.
**Context:** Implemented in Speaker ID Phase 2 (feat/coaching-quality-cache-rank-rotate).

### ~~Speaker ID Phase 2 decision gate~~ ✅ COMPLETE
**Completed (2026-04-11):** Resolver accuracy metrics (total resolutions, user corrections, time to first resolution) tracked per session. WeSpeaker ECAPA-TDNN voiceprint extraction + storage + confidence boost shipped alongside, so the decision gate is moot. Voiceprint centroids persist cross-session via EMA updates.
**Context:** Implemented in Speaker ID Phase 2 (feat/coaching-quality-cache-rank-rotate).

---

## Deferred from CEO Plan (previously identified)

- **Zoom SDK integration** — ScreenCaptureKit covers all platforms without it
- **Windows support** — ScreenCaptureKit is macOS-only; Windows is V2 with different capture API
- **App Store distribution** — Apple audio capture review process too slow for MVP
- **Voice/audio playback in Persuasion Replay** — text/visual timeline in V1; audio scrubbing in V2
- ~~**LinkedIn integration for pre-seeding**~~ ✅ COMPLETE — auto-fetch from public LinkedIn profiles via `backend/linkedin.py` + ProfilesPane URL input
- **SOC 2 / enterprise security review** — V2, before enterprise sales
- **Zoom cloud recording import for retroactive analysis** — V2, requires Zoom OAuth
- **Otter transcript import for retroactive analysis** — V2, third-party dependency + copyright risk
- **Team Intelligence cloud/server backend** — V2; SQLite file-based sync in V1
- **Team participant profile conflict resolution UI** — V2; V1 uses append-only with confidence range
- **Team analytics dashboard (aggregate effectiveness, peer benchmarking)** — V2, gated on individual validation
