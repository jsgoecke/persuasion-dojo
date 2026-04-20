---
title: Running the Backend
description: Start the FastAPI backend in dev or production mode and exercise its REST and WebSocket endpoints.
tags: [guide, getting-started, stack/fastapi]
type: guide
related:
  - "[[First-Time Setup]]"
  - "[[Backend - main]]"
  - "[[Audio Pipeline]]"
  - "[[Transcription Pipeline]]"
updated: 2026-04-19
---

# Running the Backend

## Development (auto-reload)

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload
```

- Listens on **http://localhost:8000**
- Auto-reloads on any Python file change
- Starts the [[Backend - audio_tcp_server|AudioTcpServer]] on `AUDIO_TCP_PORT` (default 9090)

## Production

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1
```

One worker only — the [[Audio Pipeline|audio pipeline]] and [[Coaching Engine Architecture|coaching engine]] hold per-session in-memory state.

## Endpoints

### Health
```
GET /health  →  {"status": "ok"}
```

### WebSockets
- `WS /ws/session/{session_id}` — live coaching session. See [[Coaching Engine Architecture]].
- `WS /ws/sparring/{session_id}` — text-only AI practice. See [[Backend - sparring|sparring.py]].

### REST — session lifecycle
```
POST   /sessions
GET    /sessions
GET    /sessions/{id}
GET    /sessions/{id}/transcript
DELETE /sessions/{id}
```

### REST — user & participants
```
GET  /users/me
PUT  /users/me
GET  /participants
GET  /participants/{id}
GET  /participants/{id}/fingerprint
PUT  /participants/{id}
PUT  /participants/{id}/assign-name
DELETE /participants/{id}
```

### REST — calendar (if `GOOGLE_CLIENT_ID` configured)
```
GET    /calendar/watch
POST   /calendar/watch
DELETE /calendar/watch
POST   /calendar/webhook
```

See [[Backend - main|backend/main.py]] for the full surface.

## Key WebSocket messages

**Client → Server:**
```json
{"type": "utterance", "speaker_id": "user", "text": "...", "is_final": true, "start": 12.3, "end": 14.1}
{"type": "ping"}
{"type": "session_end"}
```

**Server → Client:**
```json
{"type": "coaching_prompt", "layer": "audience", "text": "...", "is_fallback": false, "triggered_by": "elm:ego_threat", "speaker_id": "speaker_1"}
{"type": "audio_level", "level": 0.42}
{"type": "utterance", "speaker_id": "counterpart_0", "text": "...", "is_final": true}
{"type": "swift_restart_needed", "reason": "silence"}
{"type": "session_ended", "session_id": "...", "persuasion_score": 72, "growth_delta": null}
```

## Next

→ [[Running the Frontend Overlay]]
→ [[Running the Swift Binary]]
