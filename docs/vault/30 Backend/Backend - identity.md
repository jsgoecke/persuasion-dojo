---
title: Backend - identity
description: Speaker name validation and fuzzy matching — the gatekeeper that keeps technical terms from becoming Participant rows.
tags: [module, lang/python, layer/identity]
type: module
module_path: backend/identity.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - speaker_resolver]]"
  - "[[Backend - transcript_parser]]"
  - "[[Backend - models]]"
updated: 2026-04-19
---

# backend/identity.py

Identity resolution helpers for speaker names. Combines fuzzy matching
(0.85 threshold) with a plausibility gate so that words like "Action",
"Summary", or other transcript noise never get persisted as a
`Participant`.

## Public surface
- `is_plausible_speaker_name()` — gatekeeper
- `fuzzy_match()` — similarity ≥0.85
- `resolve_participant()` — DB-aware lookup
- `_BLOCKLIST_WORDS` — known non-names

## Imports
[[Backend - models]]

## Imported by
[[Backend - speaker_resolver]], [[Backend - transcript_parser]]

## Tests
Exercised via `tests/test_transcript_parser.py` and
`tests/test_speaker_resolver.py`.
