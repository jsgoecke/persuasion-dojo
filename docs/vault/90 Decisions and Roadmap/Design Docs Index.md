---
title: Design Docs Index
description: One-paragraph pointer to every design doc, plan, and spec with status.
tags: [decision, index]
type: concept
related:
  - "[[Key Constraints and Decisions]]"
  - "[[Roadmap and TODOs]]"
  - "[[TCP Transport]]"
  - "[[Docker Deployment]]"
  - "[[Flexibility Score and CAPS]]"
  - "[[Bayesian Knowledge Tracing]]"
  - "[[Backend - speaker_resolver]]"
updated: 2026-04-19
---

# Design Docs Index

All design artifacts live under `docs/designs/` (CEO-level platform plans) and `docs/superpowers/plans|specs/` (superpowers-managed implementation plans and specs). Status is one of **shipped**, **in progress**, **planned**, or **research**.

## docs/designs/

### persuasion-dojo-platform.md — CEO vision and MVP scope

**Status:** shipped (2026-03-25). The foundational product + architecture document. Includes the overlay UX spec, first-run wizard storyboard, and 9 architecture notes that everything else references. Read this first if you're new to the codebase.

### situational-flexibility-architecture.md — Distribution-based profiles

**Status:** shipped v0.9.0.0 (2026-03-31). Replaces point-archetype profiles with density distributions (Whole Trait Theory), adds Flexibility Score (TRACOM Versatility), CAPS if-then signatures (Mischel & Shoda), Bayesian Knowledge Tracing for 5 skill keys, and Thompson Sampling for bullet selection. 32 new tests shipped with the feature. Known gap: Phase 3C session-end BKT integration is still open — see [[Roadmap and TODOs]]. Cross-links: [[Flexibility Score and CAPS]], [[Bayesian Knowledge Tracing]].

### speaker-identification-research.md — Research + Phase 1 plan

**Status:** research complete; Phase 1–2 shipped (2026-04-11). Compares Granola / Otter / Fireflies / Recall approaches, enumerates 10 targeted fixes for `SpeakerResolver`, and lays out the voiceprint roadmap. Implemented in v0.11.0.0 (resolver fixes) and v0.11.1.0 (voiceprints). See [[Backend - speaker_resolver]] and [[Backend - speaker_embeddings]].

## docs/superpowers/specs/

### 2026-04-19-audio-tcp-transport-design.md

**Status:** design complete; implementation in progress on `feat/audio-tcp-transport`. Replaces the Swift → Python FIFO audio transport with loopback TCP so the backend can run inside a container while the Swift ScreenCaptureKit binary stays on the host. Binds `0.0.0.0` inside the container via `AUDIO_TCP_HOST` and exposes port 9090 (override with `AUDIO_TCP_PORT`). Cross-links: [[TCP Transport]], [[Audio Lifecycle and Supervision]].

### 2026-04-19-dockerize-backend-design.md

**Status:** design complete; implementation in progress on `feat/dockerize-backend` (and partially merged into the TCP transport branch). Production-focused image: non-root user, health check endpoint, named volume for SQLite persistence, single-instance enforcement via fixed `container_name`, split `requirements.txt` / `requirements-dev.txt` so the image stays lean. Cross-links: [[Docker Deployment]].

## docs/superpowers/plans/

### 2026-04-19-audio-tcp-transport.md

Implementation plan for the TCP transport spec above — sequence of Swift client changes, Python listener changes, Electron overlay env-var plumbing, and test updates.

### 2026-04-19-dockerize-backend.md

Six-task implementation plan for the Dockerize spec: Dockerfile, docker-compose, requirements split, health check wiring, CI voiceprint-deps split, and documentation updates.
