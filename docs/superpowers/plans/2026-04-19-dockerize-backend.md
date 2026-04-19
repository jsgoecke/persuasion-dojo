# Dockerize Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-focused Docker image and `docker-compose.yml` for the FastAPI backend, with SQLite persistence via a named volume and a `/health` healthcheck.

**Architecture:** Single-service `docker-compose.yml` that builds a `python:3.12-slim` image, runs uvicorn as a non-root user on port 8000, loads env vars from `.env`, mounts a named volume at `/app/data/db` for SQLite, and exercises the existing `GET /health` endpoint via `urllib.request` for healthcheck. Swift ScreenCaptureKit binary and Electron overlay remain host-only and are not containerized.

**Tech Stack:** Docker, docker compose v2, Python 3.12, FastAPI, uvicorn, SQLite (WAL mode via aiosqlite).

**Spec:** `docs/superpowers/specs/2026-04-19-dockerize-backend-design.md`

**Branch:** `feat/dockerize-backend`

---

## Pre-flight

- [ ] **Confirm you are on the correct branch**

Run:
```bash
cd /Users/jasongoecke/Projects/vish/persuasion-dojo
git status
```
Expected: `On branch feat/dockerize-backend`, working tree clean (spec commit `d370c41` is the tip).

- [ ] **Confirm Docker is installed and running**

Run:
```bash
docker --version
docker compose version
docker info >/dev/null && echo "daemon up"
```
Expected: Docker >= 24, Compose v2, "daemon up".

---

## Task 1: `.dockerignore`

**Files:**
- Create: `.dockerignore`

- [ ] **Step 1: Create `.dockerignore` at repo root**

Create file `/Users/jasongoecke/Projects/vish/persuasion-dojo/.dockerignore` with exactly this content:

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

- [ ] **Step 2: Verify file written**

Run:
```bash
wc -l .dockerignore
head -1 .dockerignore
```
Expected: ~27 lines; first line is `.git`.

- [ ] **Step 3: Commit**

```bash
git add .dockerignore
git commit -m "chore: add .dockerignore for backend image builds"
```

---

## Task 2: `.env.example`

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Create `.env.example` at repo root**

Create file `/Users/jasongoecke/Projects/vish/persuasion-dojo/.env.example` with exactly this content:

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

- [ ] **Step 2: Verify `.env.example` is not ignored by `.dockerignore`**

Run:
```bash
docker run --rm -v "$PWD:/ctx" alpine sh -c 'cd /ctx && ls -la .env.example'
```
Expected: file listed (exists, ~250 bytes).

Note: the `.dockerignore` uses `!.env.example` negation so this file will still be shipped into the build context if ever needed, while `.env` and `.env.*` are excluded.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "chore: add .env.example documenting required env vars"
```

---

## Task 3: Dockerfile

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Create `Dockerfile` at repo root**

Create file `/Users/jasongoecke/Projects/vish/persuasion-dojo/Dockerfile` with exactly this content:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_URL=sqlite+aiosqlite:////app/data/db/persuasion_dojo.db

# OS deps needed to compile wheels from source when prebuilt wheels are unavailable
# (numpy, cryptography, wespeakerruntime) plus libsndfile for audio numeric deps.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

# Non-root runtime user
RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Dependency layer first for cache reuse
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application source
COPY backend ./backend
COPY data ./data
COPY main.py pyproject.toml ./

# Writable SQLite directory (volume mount point) with correct ownership
RUN mkdir -p /app/data/db && chown -R app:app /app

USER app
EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Build the image**

Run:
```bash
docker build -t persuasion-dojo-backend:latest .
```
Expected: build succeeds. Final line resembles `naming to docker.io/library/persuasion-dojo-backend:latest`. If a wheel fails to compile, the most likely culprit is a missing apt package — capture the error, add the package to the `apt-get install` list, and rebuild before moving on.

- [ ] **Step 3: Smoke-test the image (no compose yet)**

Run:
```bash
# Ephemeral run without mounts or env — just confirm uvicorn boots
docker run --rm -d --name pd-smoke -p 8000:8000 persuasion-dojo-backend:latest
sleep 2
curl -fsS http://localhost:8000/health
echo
docker stop pd-smoke
```
Expected: `{"status":"ok"}`. If the app fails at import time because a required env var is missing, that's expected to surface only when a code path is exercised — the `/health` endpoint must respond regardless. If `/health` 500s on a missing env var, capture the traceback and stop; that indicates a real issue that needs fixing before moving on.

- [ ] **Step 4: Confirm non-root user**

Run:
```bash
docker run --rm --entrypoint id persuasion-dojo-backend:latest
```
Expected output: `uid=10001(app) gid=10001(app) groups=10001(app)`.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile
git commit -m "feat: add prod-focused Dockerfile for FastAPI backend

python:3.12-slim base, non-root user (uid 10001), uvicorn on :8000,
SQLite DB path defaults to /app/data/db/persuasion_dojo.db so a
compose volume can persist data."
```

---

## Task 4: docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create `docker-compose.yml` at repo root**

Create file `/Users/jasongoecke/Projects/vish/persuasion-dojo/docker-compose.yml` with exactly this content:

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

- [ ] **Step 2: Provide a local `.env` if one does not exist**

A missing `.env` will cause `docker compose up` to fail. If you don't already have one on disk:

```bash
[ -f .env ] || cp .env.example .env
```

Fill in `ANTHROPIC_API_KEY` and `DEEPGRAM_API_KEY` with real values (or placeholders for smoke testing — `/health` doesn't exercise them).

- [ ] **Step 3: Validate compose syntax**

Run:
```bash
docker compose config >/dev/null && echo "compose config OK"
```
Expected: `compose config OK`.

- [ ] **Step 4: Bring the stack up**

Run:
```bash
docker compose up -d --build
```
Expected: `✔ Container persuasion-dojo-backend  Started`.

- [ ] **Step 5: Wait for healthcheck and verify**

Run:
```bash
# Give it up to 60s to go healthy
for i in $(seq 1 12); do
  status=$(docker inspect --format '{{.State.Health.Status}}' persuasion-dojo-backend 2>/dev/null)
  echo "attempt $i: $status"
  [ "$status" = "healthy" ] && break
  sleep 5
done
curl -fsS http://localhost:8000/health
echo
```
Expected: status transitions to `healthy`; curl returns `{"status":"ok"}`.

- [ ] **Step 6: Verify non-root user inside the running container**

Run:
```bash
docker compose exec backend id
```
Expected: `uid=10001(app) gid=10001(app) groups=10001(app)`.

- [ ] **Step 7: Verify SQLite volume persistence**

Create a row via the API, recreate the container, and confirm the row survives:

```bash
# 1. Create a session (writes to SQLite)
curl -fsS -X POST http://localhost:8000/sessions \
  -H 'Content-Type: application/json' \
  -d '{}' | tee /tmp/pd-session.json
SESSION_ID=$(python3 -c 'import json,sys; print(json.load(open("/tmp/pd-session.json"))["id"])')
echo "created session: $SESSION_ID"

# 2. Recreate the container (volume should survive)
docker compose down
docker compose up -d
for i in $(seq 1 12); do
  status=$(docker inspect --format '{{.State.Health.Status}}' persuasion-dojo-backend 2>/dev/null)
  [ "$status" = "healthy" ] && break
  sleep 5
done

# 3. Fetch the session back
curl -fsS "http://localhost:8000/sessions/$SESSION_ID"
echo
```
Expected: the GET returns the same session id and does not 404. If `POST /sessions` requires a non-empty body, inspect `backend/main.py` around line 597 for the request schema and adjust the JSON payload. The acceptance criterion is "a row written before `down` is still readable after `up`" — use whatever valid body the endpoint accepts.

- [ ] **Step 8: Confirm volume is a named Docker volume, not a bind mount**

Run:
```bash
docker volume ls | grep persuasion-dojo-data
docker volume inspect persuasion-dojo-data --format '{{.Mountpoint}}'
```
Expected: the volume is listed; mountpoint is under `/var/lib/docker/volumes/...` (Docker-managed), not a host path.

- [ ] **Step 9: Bring the stack down (data preserved)**

Run:
```bash
docker compose down
docker volume ls | grep persuasion-dojo-data
```
Expected: `down` succeeds; volume still present in `docker volume ls`.

- [ ] **Step 10: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose.yml for backend service

One service (backend) on :8000, loads env from .env, persists SQLite
via the persuasion-dojo-data named volume, healthchecks /health with
the Python stdlib (no curl in slim image)."
```

---

## Task 5: Document the Docker workflow in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (append a Docker subsection under the existing "Commands" block)

- [ ] **Step 1: Read the current Commands block**

Run:
```bash
grep -n "^## Commands" CLAUDE.md
grep -n "^## " CLAUDE.md | head -5
```
Identify the line range of the `## Commands` fenced block so the new block is inserted immediately after it.

- [ ] **Step 2: Append Docker commands after the existing Commands block**

Edit `CLAUDE.md`. Immediately after the closing ```` ``` ```` of the existing Commands code block, add:

````markdown

### Docker (backend only)

```bash
cp .env.example .env           # first-time setup, then fill in API keys
docker compose up -d --build   # build image and start backend on :8000
docker compose logs -f backend # tail logs
curl localhost:8000/health     # smoke test
docker compose down            # stop; SQLite data persists in the named volume
```

The Docker image is backend-only. The Swift ScreenCaptureKit binary and
Electron overlay continue to run on the host Mac — live audio capture
is not available inside the container. See
`docs/superpowers/specs/2026-04-19-dockerize-backend-design.md`.
````

- [ ] **Step 3: Verify the addition renders correctly**

Run:
```bash
grep -n "### Docker (backend only)" CLAUDE.md
```
Expected: one match.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document Docker workflow in CLAUDE.md"
```

---

## Task 6: Final verification

- [ ] **Step 1: Clean build from scratch**

Run:
```bash
docker compose down -v                    # remove volume to prove fresh-build path
docker image rm persuasion-dojo-backend:latest 2>/dev/null || true
docker compose up -d --build
for i in $(seq 1 12); do
  status=$(docker inspect --format '{{.State.Health.Status}}' persuasion-dojo-backend 2>/dev/null)
  echo "attempt $i: $status"
  [ "$status" = "healthy" ] && break
  sleep 5
done
curl -fsS http://localhost:8000/health
echo
```
Expected: container healthy, `/health` returns 200.

- [ ] **Step 2: Record image size for the PR description**

Run:
```bash
docker image inspect persuasion-dojo-backend:latest --format '{{.Size}}' \
  | awk '{printf "%.1f MB\n", $1/1024/1024}'
```
Note the result — include it in the PR description.

- [ ] **Step 3: Tear down**

Run:
```bash
docker compose down
```

- [ ] **Step 4: Confirm clean git state**

Run:
```bash
git status
git log --oneline feat/dockerize-backend ^main
```
Expected: working tree clean; six commits on the branch total — the pre-existing spec commit (`d370c41`) plus five new commits from this plan (`.dockerignore`, `.env.example`, `Dockerfile`, `docker-compose.yml`, `CLAUDE.md` docs).

---

## Out of scope (reminders, do not implement here)

- No changes to `backend/audio.py` or the Swift binary. Audio still uses the named pipe on host.
- No live-audio bridging into the container.
- No dev-target Dockerfile stage, no `--reload`, no bind-mounted source.
- No CI/test-runner container.
- No multi-arch build.

These are tracked as follow-ups in the spec's "Out-of-scope follow-ups" section.
