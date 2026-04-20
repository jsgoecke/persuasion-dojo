---
title: Backend - transcriber_protocol
description: Transcriber Protocol interface for drop-in replaceable transcribers (Deepgram, Moonshine, hybrid, retro).
tags: [module, lang/python, layer/transport]
type: module
module_path: backend/transcriber_protocol.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - transcription]]"
  - "[[Backend - moonshine_transcription]]"
  - "[[Backend - hybrid_transcription]]"
  - "[[Backend - retro_import]]"
  - "[[Transcription Pipeline]]"
updated: 2026-04-19
---

# backend/transcriber_protocol.py

Defines the `Transcriber` Protocol — the seam that lets real-time,
on-device, hybrid, and retroactive transcribers all plug into the same
downstream pipeline without code changes.

## Public surface
- `Transcriber` — Protocol
- `UtteranceCallback`, `ErrorCallback`, `StatusCallback` — type aliases

## Imports
(stdlib `typing`)

## Imported by
[[Backend - transcription]], [[Backend - moonshine_transcription]],
[[Backend - hybrid_transcription]], [[Backend - retro_import]]

## Tests
Exercised indirectly by every transcriber test (`test_transcription.py`,
`test_moonshine_transcription.py`, `test_hybrid_transcription.py`,
`test_retro_import.py`).
