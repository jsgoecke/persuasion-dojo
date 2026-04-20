---
title: Backend - transcription
description: Deepgram streaming ASR client with speaker diarization, reconnect, and a ring buffer for failover replay.
tags: [module, lang/python, layer/transport]
type: module
module_path: backend/transcription.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - transcriber_protocol]]"
  - "[[Backend - hybrid_transcription]]"
  - "[[Backend - moonshine_transcription]]"
  - "[[Transcription Pipeline]]"
updated: 2026-04-19
---

# backend/transcription.py

Deepgram streaming ASR client. Maintains the Deepgram WebSocket, handles
reconnect, emits diarized utterances, and keeps a ~5s ring buffer so that
the hybrid transcriber can replay audio to a local fallback if Deepgram
fails mid-call.

## Public surface
- `DeepgramTranscriber` — implements [[Backend - transcriber_protocol]]
- `connect()` / `send_audio()` / `disconnect()` — lifecycle
- `deepgram_health_check()` — used by [[Backend - hybrid_transcription]]
- Internal ring buffer (~5 seconds) for failover replay

## Imports
[[Backend - transcriber_protocol]]

## Imported by
[[Backend - hybrid_transcription]], [[Backend - main]]

## Tests
`tests/test_transcription.py`, `tests/test_hybrid_transcription.py`
