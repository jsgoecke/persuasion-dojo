---
title: Backend - retro_import
description: Retroactive audio-file processor using Deepgram REST (not streaming) — WAV/MP3/M4A in, utterances with timing out.
tags: [module, lang/python, layer/orchestration]
type: module
module_path: backend/retro_import.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - transcriber_protocol]]"
  - "[[Backend - transcript_parser]]"
  - "[[Transcription Pipeline]]"
updated: 2026-04-19
---

# backend/retro_import.py

Retroactive import path for finished recordings. Uses Deepgram's REST API
(not the streaming WebSocket) to transcribe uploaded WAV/MP3/M4A files
and emit the same utterance shape as the live path, so downstream
scoring and coaching can run over past meetings.

## Public surface
- `RetroImporter`
- `process_file()` → `int` utterance count
- `on_utterance()` / `on_progress()` — callbacks
- Channel-level diarization fallback when speaker labels are missing

## Imports
[[Backend - transcriber_protocol]]

## Imported by
[[Backend - main]]

## Tests
`tests/test_retro_import.py`
