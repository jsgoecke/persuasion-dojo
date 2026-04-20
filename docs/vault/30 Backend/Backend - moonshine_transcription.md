---
title: Backend - moonshine_transcription
description: Local on-device ASR transcriber using Moonshine v2 — no cloud, used as fallback when Deepgram is unavailable.
tags: [module, lang/python, layer/transport]
type: module
module_path: backend/moonshine_transcription.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - transcriber_protocol]]"
  - "[[Backend - hybrid_transcription]]"
  - "[[Transcription Pipeline]]"
updated: 2026-04-19
---

# backend/moonshine_transcription.py

Local on-device ASR using Moonshine v2. Runs entirely on the host machine
with no cloud dependency, and is the fallback path for
[[Backend - hybrid_transcription]] when Deepgram is degraded.

## Public surface
- `MoonshineTranscriber` — implements [[Backend - transcriber_protocol]]
- Lazy model load on first use
- Float32 PCM conversion from the raw Swift audio stream

## Imports
[[Backend - transcriber_protocol]]

## Imported by
[[Backend - hybrid_transcription]]

## Tests
`tests/test_moonshine_transcription.py`, `tests/test_hybrid_transcription.py`
