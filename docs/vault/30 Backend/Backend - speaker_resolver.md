---
title: Backend - speaker_resolver
description: LLM-based background resolver that maps diarized speaker labels to real names using calendar roster, transcript context, and voiceprints.
tags: [module, lang/python, layer/identity]
type: module
module_path: backend/speaker_resolver.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - identity]]"
  - "[[Backend - speaker_embeddings]]"
  - "[[Backend - turn_tracker]]"
  - "[[Backend - calendar_service]]"
updated: 2026-04-19
---

# backend/speaker_resolver.py

Background asyncio task (runs ~every 5s) that resolves Deepgram's
diarized speaker labels to real names. Combines the calendar roster,
transcript context, the vocative turn-tracker signal, and (optionally)
voiceprint similarity. Fuzzy name-match threshold is 0.85.

## Public surface
- `SpeakerResolver` — background resolver
- `start()` / `stop()` — lifecycle
- `resolve(speaker_id)` — force resolution
- `add_utterance()` — feed new text

## Imports
[[Backend - identity]], [[Backend - speaker_embeddings]], [[Backend - turn_tracker]]

## Imported by
[[Backend - main]]

## Tests
`tests/test_speaker_resolver.py`
