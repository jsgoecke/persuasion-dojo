---
title: Backend - speaker_embeddings
description: WeSpeaker ECAPA-TDNN voiceprint extractor (256-dim) — on-device, optional, boosts speaker resolver confidence.
tags: [module, lang/python, layer/identity]
type: module
module_path: backend/speaker_embeddings.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - speaker_resolver]]"
updated: 2026-04-19
---

# backend/speaker_embeddings.py

On-device voiceprint extraction using WeSpeaker's ECAPA-TDNN model
(256-dim embeddings). Optional — guarded by a dependency availability
check — and when present raises the confidence of
[[Backend - speaker_resolver]] for recurring participants.

## Public surface
- `VoiceprintExtractor`
- `available()` — dep check
- `extract_embedding()` → 256-dim vector
- `_pcm_to_fbank` — internal filterbank helper
- Minimum segment length: 5.0 seconds

## Imports
(`torch`, `wespeaker` — optional)

## Imported by
[[Backend - speaker_resolver]]

## Tests
`tests/test_speaker_embeddings.py`
