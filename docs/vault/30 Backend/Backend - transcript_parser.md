---
title: Backend - transcript_parser
description: Multi-format transcript parser — Markdown bold, Otter.ai/Zoom, simple colon — with auto-detection, producing speaker-labeled utterances.
tags: [module, lang/python, layer/orchestration]
type: module
module_path: backend/transcript_parser.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - identity]]"
  - "[[Backend - retro_import]]"
updated: 2026-04-19
---

# backend/transcript_parser.py

Parses pasted or imported text transcripts into speaker-labeled
utterances. Supports three formats with auto-detection: Markdown with
bold speaker names, Otter.ai / Zoom-style headers, and simple
`Name: text` lines.

## Public surface
- `parse_transcript()` → `list[dict]`
- `TranscriptFormat` — enum
- `_detect_format()` — auto-detector
- `_parse_timestamp()` — normalizes mixed timestamp styles

## Imports
[[Backend - identity]]

## Imported by
[[Backend - retro_import]], [[Backend - main]]

## Tests
`tests/test_transcript_parser.py`
