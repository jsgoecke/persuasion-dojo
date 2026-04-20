---
title: Roadmap and TODOs
description: What's gated, what's queued, and what's explicitly deferred to V2.
tags: [roadmap]
type: concept
related:
  - "[[Key Constraints and Decisions]]"
  - "[[Design Docs Index]]"
  - "[[Changelog Highlights]]"
updated: 2026-04-19
---

# Roadmap and TODOs

Categorized summary of `TODOS.md`. Only the open items below still need work.

## P0 — Gates (all complete)

- **Convergence validation spike** — `scripts/convergence_spike.py` hit 3/3 signals correct on a real Granola transcript (100% agreement, gate was ≥75%). `scoring.py` is unblocked. See [[Scoring Engine]].
- **Design system** — `DESIGN.md` shipped 2026-03-25 with typography, color, spacing, motion, and enforcement rules in `CLAUDE.md`. Frontend implementation unblocked.
- **Pre-seed accuracy gate** — 5/5 Sailplane team members classified correctly (100%, gate was ≥70%). `pre_seeding.py` unblocked.

## P1 — Situational flexibility follow-ups (in progress)

Phase 3C of the situational flexibility plan was designed but not fully wired. Open items:

- **Wire `convergence:uptake` skill to BKT** — `classify_skill_opportunity()` doesn't currently emit observations for this key, so P(know) stays pinned at the 0.1 prior. Convergence signals come from `signals.py`; route them into BKT at session end.
- **BKT session-end integration** — `SkillMastery` model and the pure functions (`classify_skill_opportunity`, `bkt_update`) exist but nothing calls them from `main.py`. Without this the `skill_mastery` table stays empty across sessions.
- **BKT in non-ELM bullet selection** — `relevance_score()` in `coaching_bullets.py` only applies BKT weighting when `bullet.elm_state` is set, leaving 3 of 5 skill keys (`pairing:archetype_match`, `timing:talk_ratio`, `convergence:uptake`) unable to influence selection. Map bullets to skill keys beyond just `elm_state`.
- **Debrief UI for flexibility data** — Flexibility Score, CAPS signatures, and per-participant convergence are computed and stored but invisible to users. Needs debrief panels.

See [[Flexibility Score and CAPS]] and [[Bayesian Knowledge Tracing]].

## P1 — Speaker identification follow-ups

Completed:

- Manual speaker tagging UX (tap-to-edit popover, "?" confidence badge, hysteresis deadband, confidence-based prompt suppression).
- Adaptive resolver scheduling (10s intro / 15s default / 60s all-locked; skip when no new utterances).
- Voiceprint extraction + storage + confidence boost (see [[Backend - speaker_embeddings]]).

Open:

- **Deepgram `multichannel=true` investigation** — we already capture mic and system audio separately via ScreenCaptureKit; does dual-channel Deepgram improve diarization vs the current mixed stream? Outside Phase 1 blast radius; deferred.
- **Nova-3 validation on real SCK audio** — benchmarks are on clean recordings, not SCK mixed audio. Confirm the nova-2 → nova-3 upgrade doesn't regress before shipping to users.

## P2 — V2 scope (deferred)

Explicitly out of V1:

- Google Calendar push webhooks (design done; needs publicly reachable URL, gated on a cloud backend).
- Apple MDM profile signing (build script + unsigned `.mobileconfig` are ready; corporate rollout is V2 — see [[Scripts and MDM]]).
- Windows support (ScreenCaptureKit is macOS-only; Windows needs a different capture API).
- App Store distribution (Apple audio-capture review too slow for MVP).
- Voice/audio playback in Persuasion Replay (V1 is text + visual timeline; audio scrubbing is V2).
- LinkedIn auto-fetch beyond the current public-profile scraper in `backend/linkedin.py`.
- SOC 2 / enterprise security review (before enterprise sales).
- Zoom cloud recording import (requires Zoom OAuth).
- Otter transcript import (third-party dependency + copyright risk).
- Team Intelligence cloud backend (V1 uses AES-256 encrypted SQLite file sync).
- Team participant conflict resolution UI; team analytics dashboard (both gated on individual validation).
