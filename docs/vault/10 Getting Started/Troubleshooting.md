---
title: Troubleshooting
description: Common failure modes and their root causes — Screen Recording permission, silent audio pipe, Haiku timeouts, SQLite WAL files.
tags: [guide, runbook]
type: runbook
related:
  - "[[Audio Lifecycle and Supervision]]"
  - "[[Running the Swift Binary]]"
  - "[[Coaching Engine Architecture]]"
  - "[[Backend - database]]"
updated: 2026-04-19
---

# Troubleshooting

## Audio stopped / no transcription

**Symptom:** overlay shows no utterances; no audio-level meter.

**Most common cause:** Screen Recording permission was silently revoked by macOS because the app bundle signature changed (update, dev rebuild, etc.).

**Fix:**
1. System Settings → Privacy & Security → Screen Recording.
2. Remove **Persuasion Dojo** (or the standalone `AudioCapture` binary).
3. Restart the overlay; macOS re-prompts.
4. Re-grant permission.

The [[Audio Lifecycle and Supervision|silence watchdog]] detects 5+ seconds without audio and emits `swift_restart_needed` to the overlay automatically.

## "Cached ↻" badge stuck on prompts

**Cause:** Haiku is timing out (>1.5s budget) and [[Coaching Engine Architecture|the coaching engine]] is falling back to the last cached prompt.

**Fix:** check Anthropic API latency from your network; verify `ANTHROPIC_API_KEY`; temporarily switch the transcriber backend in settings to rule out CPU contention from [[Backend - moonshine_transcription|Moonshine]].

## Huge `.db-wal` file

**Cause:** SQLite runs in **WAL mode** (`backend/database.py::_set_wal_mode`). The `-wal` sidecar file grows during active sessions and shrinks only on checkpoint.

**Fix:** stop the backend and re-open — SQLite auto-checkpoints on clean shutdown. If the app crashed, recovery runs on next startup.

```bash
sqlite3 persuasion_dojo.db "PRAGMA journal_mode;"   # should return 'wal'
```

## Docker: `Bind for 0.0.0.0:8000 failed: port is already allocated`

Another container or local process is using 8000. Either stop it or override the port mapping in `docker-compose.override.yml`.

## `AUDIO_TCP_PORT must be an integer 0–65535`

FastAPI startup parses `AUDIO_TCP_PORT` strictly. Any non-numeric value or out-of-range number aborts startup with a clear message. See [[Environment Variables]].

## Tests hang on `test_audio_tcp_*`

Usually a previous run didn't release the ephemeral port. Restart the terminal session. Tests use `_pick_port()` to choose a fresh port, so collisions are rare.

## "Not notarized" dialog on first launch

The release build passed code-signing but not notarization. This is usually a CI secret misconfiguration. See [[Release Pipeline]] — check `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` are set.
