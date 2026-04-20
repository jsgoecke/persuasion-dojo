---
title: Backend - audio
description: Async TCP audio reader that drains Swift audio, detects silence timeouts, and forwards chunks to the transcriber.
tags: [module, lang/python, layer/transport]
type: module
module_path: backend/audio.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - audio_tcp_server]]"
  - "[[Backend - transcription]]"
  - "[[Audio Pipeline]]"
  - "[[Audio Lifecycle and Supervision]]"
updated: 2026-04-19
---

# backend/audio.py

Async TCP audio reader. Drains Swift audio via TCP, detects silence timeouts,
and forwards chunks to the transcriber. Complements the raw TCP listener in
[[Backend - audio_tcp_server]] by adding supervision, buffering, and
callback fan-out to the transcription pipeline.

## Public surface
- `AudioTcpReader` — owns reader task lifecycle
- `start()` — begin draining the TCP queue
- `stop()` — shutdown and cleanup
- `on_audio_chunk` — callback for forwarding PCM to transcriber
- `on_silence_timeout` — callback fired when stream goes quiet past threshold

## Imports
[[Backend - audio_tcp_server]]

## Imported by
[[Backend - main]]

## Tests
`tests/test_audio.py`, `tests/test_audio_lifecycle.py`, `tests/test_audio_buffer.py`
