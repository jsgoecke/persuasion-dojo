---
title: Backend - linkedin
description: LinkedIn public profile scraper using OpenGraph meta and JSON-LD — no auth required, used as input to pre-seeding.
tags: [module, lang/python, layer/orchestration]
type: module
module_path: backend/linkedin.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - pre_seeding]]"
updated: 2026-04-19
---

# backend/linkedin.py

LinkedIn public profile scraper. Pulls what the public web already shows
— OpenGraph meta tags and JSON-LD — and returns a normalized bio string
that [[Backend - pre_seeding]] can classify. No authentication required.

## Public surface
- `fetch_linkedin_profile()` → `str`
- `is_linkedin_url()` — URL validator
- `_MetaParser` — HTML extraction helper

## Imports
(`httpx`, stdlib `html.parser`)

## Imported by
[[Backend - main]], [[Backend - pre_seeding]]

## Tests
`tests/test_linkedin.py`
