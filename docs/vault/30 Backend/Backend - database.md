---
title: Backend - database
description: SQLAlchemy async engine + session factory, WAL-mode SQLite, auto-migration.
tags: [module, lang/python, layer/data]
type: module
module_path: backend/database.py
related:
  - "[[Backend - models]]"
  - "[[Data Model]]"
  - "[[Troubleshooting]]"
updated: 2026-04-19
---

# backend/database.py

Async SQLite via aiosqlite. WAL mode for concurrent reads; single-file embedded DB; auto-migration on startup.

## Public surface

- `AsyncEngine` — module singleton configured via `DATABASE_URL` (+ `DATABASE_ECHO`).
- `get_db_session()` — async context manager, auto-commit/rollback.
- `init_db()` — create tables + add new columns idempotently.
- `override_engine()` / `drop_all_tables()` — test fixtures.

## Imports

[[Backend - models]].

## Tests

`tests/test_database.py`.
