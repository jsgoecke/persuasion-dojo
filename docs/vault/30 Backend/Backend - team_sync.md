---
title: Backend - team_sync
description: Team Intelligence export/import using AES-256-GCM with scrypt KDF — fresh salt and nonce per export, passphrase-protected JSON.
tags: [module, lang/python, layer/orchestration]
type: module
module_path: backend/team_sync.py
related:
  - "[[Backend Module Graph]]"
  - "[[Backend - models]]"
updated: 2026-04-19
---

# backend/team_sync.py

Team Intelligence export and import. Encrypts participant records with
AES-256-GCM, derives the key from a passphrase using scrypt (N=2^17), and
emits a fresh salt and nonce on every export so shared files are safe to
hand off between teammates.

## Public surface
- `TeamSync`
- `export_participants()` — encrypted JSON bytes
- `import_participants()` → `list[ParticipantRecord]`
- `ParticipantRecord` — portable row

## Imports
(`cryptography` — AES-GCM + scrypt)

## Imported by
[[Backend - main]]

## Tests
`tests/test_team_sync.py`
