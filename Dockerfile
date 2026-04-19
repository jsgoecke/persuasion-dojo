# syntax=docker/dockerfile:1.7
FROM python:3.12-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286

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

# Non-root runtime user (explicit gid so id output matches uid)
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid 10001 --home /app --shell /usr/sbin/nologin app

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
