---
title: Environment Variables
description: Every environment variable consumed by the backend, frontend, and Swift binary — what it's for, where it's read, and whether it's required.
tags: [guide, getting-started, config]
type: guide
related:
  - "[[First-Time Setup]]"
  - "[[Docker Deployment]]"
  - "[[AudioCapture Binary]]"
updated: 2026-04-19
---

# Environment Variables

Copy `.env.example` → `.env` and fill in the values below.

## Required

### `ANTHROPIC_API_KEY`
- **Purpose:** authenticate with Claude API for real-time coaching (Haiku) and post-session analysis (Opus).
- **Consumed by:** [[Backend - coaching_engine|coaching_engine.py]], [[Backend - self_assessment|self_assessment.py]], [[Backend - coaching_bullets|coaching_bullets.py]], [[Backend - coaching_memory|coaching_memory.py]], [[Backend - main|main.py]].
- **Obtain:** [console.anthropic.com](https://console.anthropic.com).
- **Example:** `sk-ant-v4-...`

### `DEEPGRAM_API_KEY`
- **Purpose:** streaming speech-to-text with speaker diarization (primary transcriber).
- **Consumed by:** [[Backend - transcription|transcription.py]], [[Backend - retro_import|retro_import.py]], [[Backend - hybrid_transcription|hybrid_transcription.py]].
- **Obtain:** [console.deepgram.com](https://console.deepgram.com).
- **Required unless you run purely on local [[Backend - moonshine_transcription|Moonshine]].**

## Optional — Google Calendar integration

### `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
- **Purpose:** OAuth 2.0 for the [[Backend - calendar_service|calendar service]] (auto-seed meeting participants).
- **Consumed by:** `backend/main.py::_get_calendar_service()`, `backend/calendar_service.py`.
- **Obtain:** Google Cloud Console → enable Calendar API → create OAuth 2.0 Web Application credentials.

## Audio transport

### `AUDIO_TCP_PORT`
- **Purpose:** TCP port where the [[Backend - audio_tcp_server|AudioTcpServer]] listens. Swift dials this port.
- **Default:** `9090` (parsed by `backend/main.py::_parse_audio_tcp_port()`).
- **Range:** 0–65535. Validated on startup.

### `AUDIO_TCP_HOST`
- **Purpose:** bind address for the audio TCP server.
- **Default:** `127.0.0.1` (loopback). In Docker this is set to `0.0.0.0` so host Swift can reach the container listener via published port.

### `AUDIO_BACKEND_PORT`
- **Purpose:** the port the Swift binary dials. The Electron main process forwards `AUDIO_BACKEND_PORT || AUDIO_TCP_PORT || 9090` to the Swift child via `capture-env.ts::buildCaptureEnv()`.
- **Consumed by:** `swift/AudioCapture/Sources/AudioCaptureCLI/main.swift`.

## Database

### `DATABASE_URL`
- **Purpose:** SQLAlchemy async DSN.
- **Default:** `sqlite+aiosqlite:///persuasion_dojo.db` (project root).
- **Docker:** overridden to `sqlite+aiosqlite:////app/data/db/persuasion_dojo.db` inside the container.
- **Consumed by:** [[Backend - database|database.py]].

### `DATABASE_ECHO`
- **Purpose:** enable SQLAlchemy SQL logging (dev).
- **Values:** `1`, `true`, `yes`.
- **Default:** `0`.

## Feature flags

### `TURN_TRACKER_ENABLED`
- **Purpose:** enable turn-tracker-driven constraints in the coaching engine.
- **Consumed by:** `backend/main.py` SessionPipeline.
- **Values:** `true` / `false`.

## Sentry / release signing (CI only)

Used in `.github/workflows/release.yml`: `CSC_LINK`, `CSC_KEY_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`, `SENTRY_DSN`, `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT`. See [[Release Pipeline]].
