# Persuasion Dojo

A live conversation coaching app that listens to your Zoom call in real time, transcribes it, and surfaces private text prompts in a floating overlay — telling you how to be more persuasive, in the moment, without breaking your flow.

Built for senior executives and salespeople who have high-stakes conversations regularly: board meetings, client pitches, procurement reviews. This is a revenue and influence tool, not a self-improvement app.

---

## How it works

```
Zoom / Teams / Meet
        │
        ▼
ScreenCaptureKit (Swift binary)
  — captures system audio, no Zoom SDK required
        │  named pipe
        ▼
Deepgram streaming WebSocket
  — real-time transcription, speaker diarization
  — ~300ms speech-to-text latency
        │  speaker-labelled utterances
        ▼
FastAPI backend (Python)
  ├── ELM Detector       — detects ego threat, shortcut mode, groupthink
  ├── Participant Profiler — classifies counterparts into Superpower types
  ├── Coaching Engine    — generates prompts via Claude Haiku (<1.5s)
  └── Scoring Engine     — computes Persuasion Score + Growth Score
        │  WebSocket
        ▼
Electron overlay (React)
  — always-on-top floating window
  — visible only to you, not to Zoom participants
```

---

## The Communicator Superpower Framework

Every participant is classified on two axes:

|  | **Advocate** | **Analyze** |
|---|---|---|
| **Logic** | **Inquisitor** — challenges everything, needs proof before moving | **Architect** — data-first, systematic, needs structure |
| **Narrative** | **Firestarter** — leads with energy, story, and vision | **Bridge Builder** — reads the room, builds consensus |

The system knows your Superpower type (established via a 5-question onboarding quiz, refined by observed session behavior). It dynamically classifies counterparts in real time based on their word choice, response patterns, and conversational behavior.

### Coaching prompt layers

Three layers fire simultaneously on every trigger:

1. **Self layer** — Are you communicating in the right mode for this moment?
2. **Audience layer** — Who is this participant and what do they need right now?
3. **Group layer** — When to push, when to yield, when to invite contribution?

### Example prompts

> *"She's in Central Route — anchor your next point in a number."*

> *"You've been in advocate mode for 4 minutes — ask a question to re-engage the group."*

> *"He's ego-threatened — back off and ask what concerns him most."*

> *"You are providing too much information — invite contribution so the idea becomes shared."*

---

## ELM State Detection

The coaching engine uses the **Elaboration Likelihood Model** (Petty & Cacioppo, 1986) to classify audience states in real time:

| State | What it means | Coaching response |
|---|---|---|
| `ego_threat` | Audience is defensive or identity-threatened | Back off, acknowledge, ask questions |
| `shortcut` | Audience agreeing without real engagement | Invite pushback, deepen the conversation |
| `consensus_protection` | Group is suppressing dissent (groupthink) | Explicitly open space for dissent |

ELM-triggered prompts fire on a **10-second cadence floor**. General (self/group) prompts fire on a **60-second floor**. All prompts are suppressed while you are mid-utterance.

---

## Persuasion Score

At session end, the system computes a **Persuasion Score (0–100)** and a **Growth Score** (delta vs. your rolling baseline):

| Component | Weight | What it measures |
|---|---|---|
| Timing | 30% | Talk-time ratio — optimal 25–45% of session |
| Ego Safety | 30% | How much defensive pressure you generated |
| Convergence | 40% | Vocabulary adoption, question arc, agreement markers |

> **Disclosure:** Persuasion Score is a heuristic index. Weights are calibrated by user feedback over time, not empirically derived.

---

## Features

- **Live coaching overlay** — always-on-top Electron window, private to you
- **Participant profiling** — real-time Superpower classification, persists across meetings
- **Pre-seeding** — paste a bio, email, or LinkedIn blurb before a meeting to pre-classify a participant
- **Google Calendar integration** — polls upcoming meetings, pre-loads participant context
- **Sparring mode** — practice against an AI opponent before a high-stakes call
- **Retro import** — process a recorded audio file after the fact for coaching analysis
- **Team sync** — AES-256 encrypted JSON export/import to share participant intelligence with teammates
- **Session debrief** — full transcript, prompt history, Persuasion Score, Growth Score

---

## Tech stack

| Layer | Technology |
|---|---|
| Audio capture | Swift + ScreenCaptureKit (macOS 12.3+) |
| Transcription | Deepgram streaming API (speaker diarization) |
| Backend | Python 3.12 + FastAPI + WebSockets |
| Coaching engine (real-time) | Claude Haiku (`claude-haiku-4-5`) — <1.5s timeout with cached fallback |
| Coaching engine (debrief) | Claude Opus (`claude-opus-4-6`) — background, post-session |
| Database | SQLite in WAL mode via SQLAlchemy + aiosqlite |
| Frontend | React + Vite + Electron (always-on-top overlay) |
| Testing | pytest · Vitest · Playwright for Electron |

---

## Setup

### Requirements

- macOS 12.3+ (ScreenCaptureKit)
- Python 3.12+
- Node.js 20+
- Xcode command line tools (`xcode-select --install`)
- A [Deepgram](https://deepgram.com) API key
- An [Anthropic](https://console.anthropic.com) API key

### Install

```bash
git clone https://github.com/YOUR_USERNAME/persuasion-dojo.git
cd persuasion-dojo

# Python backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Electron overlay
cd frontend/overlay
npm install
cd ../..

# Build the Swift audio capture binary
cd swift/AudioCapture
./build.sh
cd ../..
```

### Configure

```bash
cp .env.example .env
# Edit .env and add:
#   ANTHROPIC_API_KEY=sk-ant-...
#   DEEPGRAM_API_KEY=...
```

### Run

```bash
# Terminal 1 — backend
source .venv/bin/activate
uvicorn backend.main:app --reload

# Terminal 2 — Electron overlay
cd frontend/overlay
npm run dev
```

Grant **Screen Recording** permission when macOS prompts. This is required for ScreenCaptureKit to capture Zoom audio.

---

## Testing

```bash
# Python unit tests (free, no API calls)
pytest

# LLM eval tests (requires API keys, costs tokens)
pytest tests/evals/

# React component tests
cd frontend/overlay && npx vitest run

# Electron E2E tests (Playwright)
cd frontend/overlay && npx playwright test
```

---

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full technical breakdown — data model, module responsibilities, audio pipeline, profile update algorithm, and key design decisions.

---

## Privacy

- All participant profiles are stored **locally** on your device (SQLite)
- Transcript text is sent to the Claude API for prompt generation — disclosed in the first-run wizard
- Team JSON exports are **AES-256 encrypted** (passphrase required to import)
- No data is sent to any server other than Deepgram (transcription) and Anthropic (coaching)
