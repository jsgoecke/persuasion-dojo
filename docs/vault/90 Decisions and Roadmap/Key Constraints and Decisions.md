---
title: Key Constraints and Decisions
description: The immovable technical and product constraints that shape Persuasion Dojo's architecture.
tags: [decision, overview]
type: decision
related:
  - "[[System Overview]]"
  - "[[Persuasion Score]]"
  - "[[Cadence Rules]]"
  - "[[Audio Lifecycle and Supervision]]"
updated: 2026-04-19
---

# Key Constraints and Decisions

These are the gates and rules the project commits to. Any change here needs explicit approval — they determine what is even buildable.

## Audio and capture

- **ScreenCaptureKit** (macOS 12.3+) is the capture surface. Hard gate: diarization ≥85% on SCK-captured mixed audio *before* committing architecture. Fallback: BlackHole stereo split.
- **Transcription latency:** <500ms Deepgram `is_final` → coaching trigger; <2s total speech-to-display.
- **Build order (non-negotiable):** dev-sign Swift binary → ScreenCaptureKit PoC on SCK audio (not clean recordings) → full notarization CI → distribution.

## Privacy and data

- Participant profiles stay on device in SQLite. Transcript text is sent to Claude — this must be disclosed in the first-run wizard.
- Team Intelligence exports are AES-256 encrypted JSON; import requires the passphrase.
- Corporate MDM may block Screen Recording permission. V1 targets personal Mac users; enterprise MDM is V2.

## Convergence and scoring

- **Scoring pre-build gate:** annotate 5–10 real transcripts and verify signal agreement ≥75% before writing `scoring.py`. See [[Scoring Engine]].
- **Pre-seed accuracy gate:** classify ≥70% of 5 known profiles correctly before `pre_seeding.py` ships.
- If either gate fails, fallback is to replace the Persuasion Score with a plain Session Summary.

## Coaching cadence

- ELM-triggered prompts: **10s floor**, counterpart utterances only.
- General prompts (self / group): **15s floor**, fires on both user and counterpart utterances so self-coaching (e.g. "you've been advocating too long") works.

## Resilience

- **Haiku timeout:** 1.5s. On timeout, the cached bullet fires and the overlay shows a subtle `↻ cached` fallback badge.
- **SCK permission check:** at session start, not just first-run. Bundle signature change on update can silently revoke permission.
- **Swift supervision:** Python tracks the last audio timestamp. Silence >5s ⇒ restart signal to Electron ⇒ Electron respawns the Swift binary. See [[Audio Lifecycle and Supervision]].
- **Profile cache flush:** write-back on confidence delta >0.05 AND every 30s (crash-safe). Not "or session end."

## Sparring

- Target <3s total round-trip (user turn → AI opponent → coaching prompt). Opponent response is streamed.

## Persuasion Score disclosure

- The score is a **heuristic index**. Weights (Timing 30% / Ego Safety 30% / Convergence 40%) are calibrated by user feedback over time, not empirically derived. This must be disclosed in the UI. See [[Persuasion Score]].
