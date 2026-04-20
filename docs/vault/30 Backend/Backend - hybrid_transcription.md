---
title: Backend - hybrid_transcription
description: Failover orchestrator — uses Deepgram first with health checks, falls back to Moonshine and replays the ring buffer on failover.
tags: [module, lang/python, layer/transport]
type: module
module_path: backend/hybrid_transcription.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - transcription]]"
  - "[[Backend - moonshine_transcription]]"
  - "[[Backend - transcriber_protocol]]"
  - "[[Transcription Pipeline]]"
updated: 2026-04-19
---

# backend/hybrid_transcription.py

Failover orchestrator that prefers Deepgram but transparently swaps to
Moonshine when cloud ASR is degraded. On failover it replays the Deepgram
ring buffer into the local model so no words are lost.

## Public surface
- `HybridTranscriber` — implements [[Backend - transcriber_protocol]]
- Mode: `cloud` / `local` / `auto`
- Health-check driven failover + ring-buffer replay

## Imports
[[Backend - transcription]], [[Backend - moonshine_transcription]], [[Backend - transcriber_protocol]]

## Imported by
[[Backend - main]]

## Tests
`tests/test_hybrid_transcription.py`
