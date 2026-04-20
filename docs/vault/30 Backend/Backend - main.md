---
title: Backend - main
description: FastAPI app — HTTP + WebSocket entry point, session lifecycle orchestrator, OAuth routes.
tags: [module, lang/python, stack/fastapi, layer/orchestration]
type: module
module_path: backend/main.py
related:
  - "[[Backend Module Graph]]"
  - "[[System Overview]]"
  - "[[Backend - audio]]"
  - "[[Backend - coaching_engine]]"
  - "[[Backend - database]]"
  - "[[Running the Backend]]"
updated: 2026-04-19
---

# backend/main.py

FastAPI application — HTTP + WebSocket server. Orchestrates the full session pipeline: onboarding, session lifecycle (create → utterance stream → score → debrief), calendar OAuth, and real-time coaching.

## Public surface

- `POST /sessions` — create a session, returns `session_id`.
- `WS /ws/session/{id}` — stream utterances in, coaching prompts out.
- `GET /sessions/{id}` — session summary + [[Persuasion Score]].
- `POST /self-assessment` — score the 12-item Likert instrument.
- `POST /participant/pre-seed` — classify a counterpart from free text.
- Calendar OAuth routes (if `GOOGLE_CLIENT_ID` set).
- `GET /health` — liveness probe.

## Key internals

- `SessionPipeline` — per-session state: [[Backend - profiler|profiler]], [[Backend - elm_detector|ELM detector]], [[Backend - coaching_engine|coaching engine]].
- `_parse_audio_tcp_port()` — strict integer parse (0–65535) with clear error.
- FastAPI `lifespan` — starts [[Backend - audio_tcp_server|AudioTcpServer]] on startup.

## Imports

[[Backend - audio]], [[Backend - database]], [[Backend - profiler]], [[Backend - elm_detector]], [[Backend - coaching_engine]], [[Backend - coaching_bullets]], [[Backend - scoring]], [[Backend - self_assessment]], [[Backend - pre_seeding]], [[Backend - sparring]], [[Backend - calendar_service]], [[Backend - team_sync]], [[Backend - hybrid_transcription]], [[Backend - transcriber_protocol]], [[Backend - models]].

## Tests

`tests/test_main.py`, `tests/test_audio_lifecycle.py`, `tests/test_database.py`.
