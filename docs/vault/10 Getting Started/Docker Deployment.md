---
title: Docker Deployment
description: Run the backend in a Docker container — image spec, port bindings, volumes, healthcheck, and the single-instance constraint.
tags: [guide, getting-started, deploy]
type: guide
related:
  - "[[Environment Variables]]"
  - "[[TCP Transport]]"
  - "[[Backend - database]]"
updated: 2026-04-19
---

# Docker Deployment (backend only)

The Swift binary and Electron overlay remain on the host. Only the Python backend runs in a container.

## Quick start

```bash
cp .env.example .env        # first-time setup; fill in API keys
docker compose up -d --build
docker compose logs -f backend
curl localhost:8000/health  # smoke test
docker compose down         # stop; SQLite data persists in the named volume
```

## Image spec (`Dockerfile`)

- **Base:** `python:3.12-slim` pinned by digest for reproducible builds.
- **OS deps:** `build-essential`, `libsndfile1` (for numpy/scipy audio ops).
- **User:** non-root `app` (uid/gid 10001).
- **`DATABASE_URL`:** overridden to `sqlite+aiosqlite:////app/data/db/persuasion_dojo.db`.
- **CMD:** `uvicorn backend.main:app --host 0.0.0.0 --port 8000`.

## Compose spec (`docker-compose.yml`)

```yaml
services:
  backend:
    build: .
    container_name: persuasion-dojo-backend
    ports:
      - "8000:8000"
      - "127.0.0.1:9090:9090"
    env_file: .env
    environment:
      AUDIO_TCP_HOST: "0.0.0.0"
    volumes:
      - persuasion-dojo-data:/app/data/db
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "3"
volumes:
  persuasion-dojo-data:
```

## Ports

| host | container | use |
|------|-----------|-----|
| `:8000` | `:8000` | FastAPI (HTTP + WebSocket) |
| `127.0.0.1:9090` | `:9090` | [[TCP Transport]] audio ingest (loopback only on host) |

The host Swift binary dials `127.0.0.1:9090`; the kernel routes the connection into the container, where the [[Backend - audio_tcp_server|AudioTcpServer]] is bound to `0.0.0.0:9090` via `AUDIO_TCP_HOST`.

## Single-instance constraint

`container_name: persuasion-dojo-backend` is fixed. Attempting `docker compose up` while an instance is already running fails with a name-conflict error. Run `docker compose down` first.

## Data persistence

SQLite files (`persuasion_dojo.db`, `-wal`, `-shm`) live inside the named volume `persuasion-dojo-data`, mounted at `/app/data/db`. See [[Backend - database]] for WAL semantics.

## Health check

The healthcheck hits `/health`. Fails after 3 consecutive misses; 20s grace period at startup.

## Next

→ [[TCP Transport]]
→ [[Troubleshooting]]
