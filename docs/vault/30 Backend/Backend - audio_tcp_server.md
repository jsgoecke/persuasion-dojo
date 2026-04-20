---
title: Backend - audio_tcp_server
description: TCP listener that accepts two Swift audio streams (system and mic) with a magic-byte handshake and routes bytes to registered queues.
tags: [module, lang/python, layer/transport]
type: module
module_path: backend/audio_tcp_server.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - audio]]"
  - "[[TCP Transport]]"
  - "[[Audio Pipeline]]"
updated: 2026-04-19
---

# backend/audio_tcp_server.py

TCP listener for the two Swift audio streams (system audio + microphone).
Validates the handshake (magic byte `0xAD` + stream tag), then routes raw
PCM bytes to the registered asyncio queue for that tag. Pure transport — no
decoding or transcription logic.

## Public surface
- `AudioTcpServer` — the listener
- `start()` / `stop()` — lifecycle
- `register(stream_tag) -> asyncio.Queue` — claim a stream tag
- `HANDSHAKE_MAGIC` — `0xAD`
- `STREAM_TAG_SYSTEM`, `STREAM_TAG_MIC` — stream identifiers

## Imports
(stdlib only — `asyncio`, `logging`)

## Imported by
[[Backend - audio]], [[Backend - main]]

## Tests
`tests/test_audio.py`, `tests/test_audio_tcp_server.py`, `tests/test_audio_tcp_integration.py`
