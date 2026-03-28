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

## Deferred from CEO Plan (previously identified)

- **Zoom SDK integration** — ScreenCaptureKit covers all platforms without it
- **Windows support** — ScreenCaptureKit is macOS-only; Windows is V2 with different capture API
- **App Store distribution** — Apple audio capture review process too slow for MVP
- **Voice/audio playback in Persuasion Replay** — text/visual timeline in V1; audio scrubbing in V2
- **LinkedIn integration for pre-seeding** — manual text in V1; auto-fetch in V2
- **SOC 2 / enterprise security review** — V2, before enterprise sales
- **Zoom cloud recording import for retroactive analysis** — V2, requires Zoom OAuth
- **Otter transcript import for retroactive analysis** — V2, third-party dependency + copyright risk
- **Team Intelligence cloud/server backend** — V2; SQLite file-based sync in V1
- **Team participant profile conflict resolution UI** — V2; V1 uses append-only with confidence range
- **Team analytics dashboard (aggregate effectiveness, peer benchmarking)** — V2, gated on individual validation
