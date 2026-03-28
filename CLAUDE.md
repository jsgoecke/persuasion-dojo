# Persuasion Dojo

A live conversation coaching app that listens to a Zoom call in real time, transcribes it, and surfaces private text prompts telling the user how to be more persuasive in the moment. Grounded in the Communicator Superpower framework (Architect, Firestarter, Inquisitor, Bridge Builder) across two axes: Logic vs. Narrative and Advocate vs. Analyze.

## Commands

```bash
source .venv/bin/activate   # activate Python virtualenv
python main.py              # run entry point
uvicorn backend.main:app --reload  # start FastAPI dev server
pytest                      # run tests
```

## Stack

- **Backend:** Python + FastAPI + WebSockets
- **Audio:** ScreenCaptureKit (Swift binary, captures any meeting app — Zoom/Teams/Meet/Webex/browser)
- **Transcription:** Deepgram streaming API (speaker diarization, ~300ms latency)
- **Coaching engine (real-time):** Claude API (`claude-haiku-4-5` — low latency, <1.5s timeout with fallback)
- **Coaching engine (debrief):** Claude API (`claude-opus-4-6` — background, post-session analysis)
- **Storage:** SQLite via SQLAlchemy (WAL mode, async via aiosqlite, local MVP)
- **Frontend:** React + Vite (floating always-on-top Electron overlay)

## Project structure

```
persuasion-dojo/
├── backend/
│   ├── main.py              # FastAPI app + WebSocket server
│   ├── audio.py             # Named pipe reader (Swift → Python audio stream)
│   ├── transcription.py     # Deepgram streaming client (WebSocket lifecycle)
│   ├── profiler.py          # Participant Superpower profiler (rule-based, 5-utterance window)
│   ├── elm_detector.py      # ELM state detection (ego-threatened / shortcut / consensus)
│   ├── coaching_engine.py   # Claude Haiku prompt generation (3-layer: self/audience/group)
│   ├── scoring.py           # Persuasion Score + Growth Score computation (pure functions)
│   ├── pre_seeding.py       # Pre-meeting participant classification from free text
│   ├── sparring.py          # AI sparring partner mode (text loop, no audio)
│   ├── retro_import.py      # Retroactive audio file processing (Deepgram REST)
│   ├── calendar_service.py  # Google Calendar OAuth + meeting polling
│   ├── team_sync.py         # JSON export/import for Team Intelligence
│   ├── models.py            # User, Participant, Session, Prompt schemas
│   └── database.py          # SQLite via SQLAlchemy (async, WAL mode)
├── swift/
│   └── AudioCapture/        # ScreenCaptureKit binary (Xcode project)
├── frontend/
│   └── overlay/             # React floating always-on-top overlay
├── tests/
│   ├── test_profiler.py
│   ├── test_elm_detector.py
│   ├── test_scoring.py
│   ├── test_coaching_engine.py
│   └── test_transcription.py
├── docs/
│   └── designs/             # CEO plans and design docs
├── .env                     # API keys (never commit)
├── .venv/                   # Python virtualenv (never commit)
└── main.py                  # Entry point (dev only)
```

## Domain model

**Communicator Superpower types:**
- **Architect** — Logic + Analyze. Data-first, systematic, needs structure.
- **Firestarter** — Narrative + Advocate. Energy-driven, inspires through story.
- **Inquisitor** — Logic + Advocate. Questions everything, needs evidence to move.
- **Bridge Builder** — Narrative + Analyze. Reads the room, builds consensus.

**Coaching prompt layers (all three fire simultaneously):**
1. **Self layer** — Is the user in the right mode for this moment?
2. **Audience layer** — Who is this participant and what do they need?
3. **Group layer** — When to push, yield, or invite contribution?

**Example prompts:**
- "Sarah is an Inquisitor — she needs data before she'll move. Anchor your next point in a number."
- "You've been in advocate mode for 4 minutes — ask a question to re-engage the group."
- "You are providing too much information. Ask a question to invite contribution so the idea becomes a shared outcome."

## Key constraints and decisions

- **Audio capture:** ScreenCaptureKit (macOS 12.3+). Hard gate: validate diarization ≥85% accuracy on SCK-captured mixed audio in PoC before committing architecture. Fallback: BlackHole stereo split.
- **Transcription latency:** target <500ms Deepgram `is_final` → coaching trigger; <2s total speech-to-display
- **Privacy:** participant profiles stored locally (SQLite); Claude API processes transcript text — disclose in first-run wizard. Team JSON export is AES-256 encrypted (passphrase required to import). Corporate MDM may block Screen Recording permission; V1 targets personal Mac users.
- **Convergence scoring:** pre-build spike required — annotate 5-10 real transcripts, verify signals ≥75% before building `scoring.py`. If fails: replace Persuasion Score with Session Summary. Pre-seed accuracy gate: classify ≥70% of 5 known profiles correctly before deploying `pre_seeding.py`.
- **Build order:** dev-sign Swift binary → ScreenCaptureKit PoC (SCK audio, not clean recordings) → full notarization CI → distribution
- **Coaching cadence:** ELM-triggered prompts: 10s minimum floor. General prompts (self/group): 60s floor. Both suppressed while user is mid-utterance (wait 500ms silence after `is_final`).
- **Fallback indicator:** When Haiku times out (1.5s) and fallback fires, overlay shows subtle `↻ cached` badge on the prompt.
- **SCK permission check:** Check Screen Recording permission at session start (not just first-run). bundle signature change on update may silently revoke permission.
- **Swift binary supervision:** Python tracks last audio timestamp. If silent >5s (pipe dead), Python sends restart signal to Electron, which restarts the Swift binary.
- **Profile cache flush:** Write-back on confidence delta >0.05 AND every 30s (crash-safe). Not "or session end."
- **Sparring latency target:** <3s total round-trip (user turn → AI opponent response → coaching prompt). Stream the AI opponent response.
- **Persuasion Score disclosure:** Score is a heuristic index. Weights (Timing 30% / Ego Safety 30% / Convergence 40%) are calibrated by user feedback over time, not empirically derived. Disclose in UI.

## Testing

```bash
pytest                              # Python backend unit tests
pytest tests/evals/                 # LLM eval tests (requires API keys)
cd frontend/overlay && npx vitest run  # React component tests
cd frontend/overlay && npx playwright test  # Electron E2E tests
```

**Frameworks:**
- Python unit tests: pytest
- LLM evals: pytest + direct Claude API calls with fixture inputs + property assertions
- React components: Vitest
- Electron E2E: Playwright for Electron

**Test structure:**
```
tests/
├── test_profiler.py          # Superpower classification, window logic, carry-forward
├── test_elm_detector.py      # Ego threat, shortcut mode, consensus protection
├── test_scoring.py           # Persuasion Score, Growth Score (pure functions)
├── test_coaching_engine.py   # Cadence floor, priority queue, fallback path
├── test_transcription.py     # Deepgram reconnect, is_final handling
├── test_database.py          # Write, read, disk-full simulation
├── test_calendar_service.py  # Token refresh, participant matching
├── test_team_sync.py         # Export, import, malformed JSON validation
└── evals/
    ├── coaching_prompts.py   # 10 fixtures: Superpower × ELM state → expected prompt properties
    └── pre_seeding.py        # Pre-seed classification from text/email/bio inputs
```

No test files exist yet — greenfield.

## Target user

Senior executives and salespeople with high-stakes conversations — board meetings, client pitches, procurement reviews. This is a revenue and influence tool, not a self-improvement app.

## gstack

Use gstack skills for all development workflows. gstack is installed at `~/.claude/skills/gstack`.

**Available skills:**

| Skill | When to use |
|-------|-------------|
| `/office-hours` | Reframe a product decision before writing code |
| `/plan-ceo-review` | CEO-level review of any feature idea |
| `/plan-eng-review` | Lock architecture, data flow, edge cases, tests |
| `/plan-design-review` | Rate design dimensions 0–10 |
| `/design-consultation` | Build a design system from scratch |
| `/review` | Pre-landing PR review — finds bugs that pass CI |
| `/investigate` | Systematic root-cause debugging |
| `/design-review` | Design audit + fix loop |
| `/qa` | Open real browser, find bugs, fix, re-verify |
| `/qa-only` | QA report only — no code changes |
| `/ship` | Run tests, review, push, open PR |
| `/document-release` | Update docs after shipping |
| `/cso` | OWASP Top 10 + STRIDE security audit |
| `/retro` | Weekly retro with shipping streaks |
| `/careful` | Warn before destructive commands |
| `/autoplan` | Auto-run CEO → design → eng review pipeline |

**Rules:**
- Use `/browse` from gstack for all web browsing — never `mcp__claude-in-chrome__*` tools
- If skills aren't working: `cd ~/.claude/skills/gstack && ./setup`

## Development principles

- **Boil the lake:** The complete implementation costs minutes more than the shortcut — do the complete thing every time.
- **Search before building:** Check if a runtime or library already solves it before designing from scratch.
- **Bisect commits:** Every commit is one logical change — independently understandable and revertable.
- **Tests are cheap:** Write them. The marginal cost with AI-assisted coding is near zero.

## Design System

Always read `DESIGN.md` before making any visual or UI decisions.
All font choices, colors, spacing, border radii, motion, and aesthetic direction are defined there.
Do not deviate without explicit user approval.

Key rules (enforced in QA):
- Overlay background must be `#1C1C1E` (never pure `#000000`)
- Debrief background must be `#FAFAF9` (never pure `#FFFFFF`)
- Layer badges: Audience `#0EA5E9`, Self `#F59E0B`, Group `#10B981`
- Typography: Instrument Serif (debrief) + Geist (overlay/UI) + Geist Mono (scores)
- Overlay window must use `vibrancy: 'hud'` — never CSS `backdrop-filter: blur()`
- Never recommend Inter, Roboto, Arial, or Helvetica as primary fonts
