# Dockerize Backend — Design

**Date:** 2026-04-19
**Branch:** `feat/dockerize-backend`
**Status:** Draft, pending user review

## Summary

Add a production-focused Docker image and `docker-compose.yml` for the FastAPI backend. The Swift ScreenCaptureKit binary, Electron overlay, and frontend Vite dev server remain host-only and are out of scope. The container runs without live audio capture; live-call workflows continue to run directly on the host Mac. A future PR will refactor audio transport from named pipe to TCP and re-open live-audio support inside the container.

## Motivation

- Reproducible backend runtime across machines and CI.
- Single-command local bring-up (`docker compose up`).
- Pave a path toward cloud deployment of the non-audio API surface (retro imports, sparring, calendar, profile APIs, evals).

## Scope

### In scope
- Containerize the FastAPI + SQLite backend as a single prod-style service.
- Provide `docker-compose.yml` that: builds the image, exposes `:8000`, mounts a named volume for SQLite, loads env from `.env`, and defines a `/health` healthcheck.
- Add `.dockerignore` to keep the image lean and prevent secret/artifact leakage.
- Add `.env.example` documenting required env vars.

### Explicitly out of scope
- Live audio capture (named pipe from Swift binary → Python). Requires audio-transport refactor to TCP, which ships in a separate PR first.
- Frontend/Electron containerization.
- Multi-stage dev/prod Dockerfile variants; this PR is prod-focused only.
- CI/test-runner container image.
- Production orchestration (k8s, Fly, ECS).

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Target use case | Backend-only container |
| Dev vs. prod | Prod-focused (no `--reload`, source copied in, slim base) |
| Optional ML deps (numpy, wespeakerruntime) | Include — single `requirements.txt`, one image |
| SQLite persistence | Named Docker volume (`persuasion-dojo-data`) |
| Secrets | `env_file: .env` in compose |
| Live audio (named pipe) | Not bridged; container runs without live audio. TCP refactor is a separate PR first. |
| Base image | `python:3.12-slim` |
| Port | Host `8000` → container `8000` |
| Healthcheck | Yes — existing `GET /health` endpoint, invoked via `python -c urllib.request` (no `curl` in slim image) |
| Container user | Non-root (`app`, uid 10001) |

## File layout

New files at repo root:

```
Dockerfile
.dockerignore
docker-compose.yml
.env.example
```

New file under docs (this spec):

```
docs/superpowers/specs/2026-04-19-dockerize-backend-design.md
```

No source files modified.

## Dockerfile (reference)

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_URL=sqlite+aiosqlite:////app/data/db/persuasion_dojo.db

# Minimal OS deps for building wheels (numpy, cryptography, wespeakerruntime).
# Kept in one layer, cleaned in the same layer.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Dependency layer (cache-friendly)
COPY requirements.txt ./
RUN pip install -r requirements.txt

# App source
COPY backend ./backend
COPY data ./data
COPY main.py pyproject.toml ./

# Writable SQLite dir (volume mount point)
RUN mkdir -p /app/data/db && chown -R app:app /app

USER app
EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Notes:
- `DATABASE_URL` is set at image build time so the app writes to the volume mount by default. Can be overridden via compose/env.
- `build-essential` is only needed for wheels that compile from source. Consider dropping later if all wheels are prebuilt for linux/amd64+arm64 — verified at build time.
- `libsndfile1` supports audio-related numeric deps; confirm at build. If unused, drop.

## docker-compose.yml (reference)

```yaml
services:
  backend:
    build: .
    image: persuasion-dojo-backend:latest
    container_name: persuasion-dojo-backend
    restart: unless-stopped
    env_file: .env
    ports:
      - "8000:8000"
    volumes:
      - persuasion-dojo-data:/app/data/db
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  persuasion-dojo-data:
```

## .env.example

```
# Required
ANTHROPIC_API_KEY=
DEEPGRAM_API_KEY=

# Required if using Google Calendar integration
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Optional
# DATABASE_URL=sqlite+aiosqlite:////app/data/db/persuasion_dojo.db
# DATABASE_ECHO=0
# TURN_TRACKER_ENABLED=true
```

## .dockerignore

```
.git
.github
.venv
__pycache__
*.pyc
.pytest_cache
.coverage
htmlcov
.mypy_cache
.ruff_cache

frontend
swift
docs
tests

node_modules
dist
build

.env
.env.*
!.env.example

*.db
*.db-wal
*.db-shm
```

## Data flow

```
host .env ──env_file──▶ container env
                         │
                         ▼
                 uvicorn backend.main:app
                         │
                         ├─ reads/writes SQLite at /app/data/db/persuasion_dojo.db
                         │  ──mounted──▶ volume persuasion-dojo-data
                         │
                         └─ listens :8000 ──published──▶ host :8000
```

No inbound audio path. Swift binary and Electron continue to run on host and are unaware of the container.

## Testing

Manual verification checklist (run after `docker compose build`):

- [ ] `docker compose up -d` exits 0.
- [ ] `curl -fsS localhost:8000/health` returns `{"status":"ok"}`.
- [ ] `docker compose exec backend id` shows `uid=10001(app)`.
- [ ] `docker compose ps` reports `healthy` after start_period.
- [ ] `docker compose down && docker compose up -d` — any rows written via API persist across restart (write a row via `POST /sessions`, inspect volume after recreate).
- [ ] `docker image inspect persuasion-dojo-backend:latest` — confirm size is in a reasonable ballpark (< ~1.5 GB with ML deps; baseline to record in PR description).

No new automated tests added; existing `pytest` suite continues to run on host.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `wespeakerruntime` or `numpy` wheels unavailable for the container arch, build fails | `build-essential` + `libsndfile1` present so source builds succeed; document expected image size |
| User commits `.env` into image | `.dockerignore` excludes `.env`, `.env.*` (keeps `.env.example`) |
| SQLite locking across restarts with WAL files | Volume holds `.db`, `.db-wal`, `.db-shm`; single-writer semantics preserved because only one container writes |
| Live audio expected to work | Spec explicitly calls out live-audio exclusion; README/docs update in follow-up TCP-refactor PR |
| Healthcheck depends on `urllib` availability | `urllib.request` is in the Python stdlib; no extra OS deps required |

## Out-of-scope follow-ups

1. **Audio TCP refactor PR** — replace named-pipe transport in `backend/audio.py` and Swift binary with TCP. Prerequisite for any future "containerized live audio" story.
2. **Re-dockerize post-TCP** — update Dockerfile/compose to expose audio TCP port once the refactor lands.
3. **CI test container** — separate image/target that runs `pytest` in a reproducible env.
4. **Dev-focused Dockerfile stage** — add a `dev` target with `--reload` and bind-mounted source.
5. **Multi-arch image build** — `buildx` for linux/amd64 + linux/arm64.

## Open items resolved during design

- **DB path configurability:** `backend/database.py:49` already reads `DATABASE_URL` from env — no code change needed. Container sets `DATABASE_URL` to a path under the mounted volume.
- **Env var inventory:** enumerated from `backend/` grep. See `.env.example` above.
- **Existing healthcheck endpoint:** `backend/main.py:588` already exposes `GET /health`.
