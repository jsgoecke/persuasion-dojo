---
title: Changelog Highlights
description: The most significant recent releases, with pointers into the module notes.
tags: [changelog]
type: concept
related:
  - "[[Roadmap and TODOs]]"
  - "[[Key Constraints and Decisions]]"
updated: 2026-04-19
---

# Changelog Highlights

Selected releases — see `CHANGELOG.md` at the repo root for the full history.

## v0.11.2.0 — 2026-04-11 — Turn tracker

Vocative-bootstrapped turn tracker: extracts name mentions ("Thanks Greg", "Sarah, what do you think?") from utterances and links them to the next speaker via turn adjacency. Zero API cost, ~0.1ms per utterance. Includes a +0.10 confidence boost when vocative evidence agrees with the LLM speaker mapping, a combined-boost cap so non-LLM signals can't auto-lock identity, 3-turn lookahead, ambiguous-first-name detection, third-party reference filter, and a kill switch via `TURN_TRACKER_ENABLED`. See [[Backend - turn_tracker]].

## v0.11.1.0 — 2026-04-11 — Voiceprints

WeSpeaker ECAPA-TDNN 256-dim voiceprint extraction with a custom numpy fbank (no torchaudio dep). Cosine similarity >0.7 against a stored centroid adds +0.15 to resolver confidence (capped below lock). Centroids persist via EMA at session end for cross-session voice memory. Separate mic and system audio ring buffers (5 min rolling + 30s pinned intro) so only counterpart voices feed the matcher. Tap-to-edit participant pills and a gold "?" badge when confidence <0.7 ship alongside. See [[Backend - speaker_embeddings]].

## v0.11.0.0 — 2026-04-10 — Cache-rank-rotate coaching

Coaching engine switches from from-scratch generation to bullet selection + Haiku personalization — eliminates refusals and cuts latency. Bullet store runs the ACE lifecycle (Selector / Curator / Reflector), thumbs up/down feedback doubles auto-score weight, layer diversity boost prevents coaching tunnel vision, and 132 seed tips warm-start the store. Speaker resolver Phase 1 ships at the same time: 15s cadence, fuzzy matching (0.85), cross-session speaker memory, flip-flop guard, Deepgram nova-2 → nova-3. See [[ACE Loop]].

## v0.10.2.0 — 2026-04-09 — Opening prompt + cadence tuning

Opening coaching prompt at session start with personalized welcome (user archetype, roster pairing advice, learned bullets). Self-layer coaching now fires on user utterances, not just counterpart turns. General cadence floor drops from 30s → 15s. ELM-triggered prompts remain counterpart-only. Session-end debrief fallback prevents overlay hang if backend crashes during scoring.

## v0.10.1.0 — 2026-04-08 — Per-person coaching + calendar auto-seed

Coaching prompts now name the counterpart and tailor advice to the archetype pairing ("Sarah is an Inquisitor, lead with data"). Google Calendar auto-seed populates attendees at session start when a meeting is happening now or within 15 minutes. User archetype is inferred from speech patterns and persists via profile cache. Echo filter (word overlap vs recent mic transcripts) prevents the user's own voice from creating false counterpart utterances.

## v0.10.0.0 — 2026-04-05 — Hybrid transcription

Sessions now fall back to local Moonshine when Deepgram is unavailable. Three modes: `auto` (default, cloud then local), `cloud`, `local`. Deepgram pre-session health check; mid-session failover replays the last ~5s from a ring buffer so no context is lost. Exponential backoff with jitter, max reconnects raised 5 → 8. Transcriber status events flow to the overlay. See [[Transcription Pipeline]].

## v0.9.2.0 / v0.9.1.0 — 2026-04-05 — Profiler sparse-signal fixes

Profiles no longer show "Unknown" when one axis has data and the other is empty — partial classifications ("Logic-leaning", "Advocacy-leaning") render in muted colors until more data arrives. Profiler switches to AND-based neutral band logic with a tighter ±10 band so sparse real-speech regex signals converge. Delete-meeting bug fixed (ORM cascade replaced with explicit Core DML).

## v0.9.0.0 — 2026-03-31 — Situational flexibility

Distribution-based archetype profiles (mean + variance per axis) grounded in Whole Trait Theory. **Flexibility Score** operationalizes TRACOM SOCIAL STYLE Versatility. **CAPS if-then signatures** map archetype-in-context using Mischel & Shoda. **Bayesian Knowledge Tracing** replaces frequency-decay badges across 5 coaching skill keys. **Thompson Sampling** for bullet selection. Per-participant convergence. Welford M2 for numerically stable variance. See [[Flexibility Score and CAPS]] and [[Bayesian Knowledge Tracing]].
