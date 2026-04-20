---
title: Backend - calendar_service
description: Google Calendar OAuth and meeting polling — attendee emails plus Zoom/Meet/Teams URL extraction for auto-seeding participants.
tags: [module, lang/python, layer/orchestration]
type: module
module_path: backend/calendar_service.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - pre_seeding]]"
  - "[[Backend - speaker_resolver]]"
updated: 2026-04-19
---

# backend/calendar_service.py

Google Calendar integration. Standard 3-legged OAuth, automatic token
refresh, and meeting polling that surfaces attendee emails plus extracted
Zoom / Google Meet / Microsoft Teams join URLs so the app can auto-seed
participants before a meeting starts.

## Public surface
- `CalendarService`
- `get_auth_url()` → `str`
- `exchange_code()` — OAuth code → tokens
- `get_upcoming_meetings()` → `list[Meeting]`
- `Meeting` — dataclass (attendees, platform, join URL)

## Imports
(`google-auth`, `google-api-python-client`)

## Imported by
[[Backend - main]]

## Tests
`tests/test_calendar_service.py`, `tests/test_calendar_auto_seed.py`
