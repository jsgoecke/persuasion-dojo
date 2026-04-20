---
title: Python Tests
description: Backend pytest suite — fixtures, markers, commands, and per-file purpose.
tags: [testing, lang/python]
type: guide
related:
  - "[[LLM Evals]]"
  - "[[CI Pipeline]]"
  - "[[Frontend Tests]]"
updated: 2026-04-19
---

# Python Tests

Roughly **1,500 tests across ~40 test files**; a full run takes about
**55 seconds**. `pyproject.toml` sets
`addopts = ["-m", "not voiceprint"]` so voiceprint-specific tests are
skipped by default (CI enables them explicitly).

## Fixtures

Defined in `tests/conftest.py`:

- `db_engine`, `db_session` — async SQLite (aiosqlite) with WAL mode
- `fake_anthropic_client`, `fake_sync_anthropic_client` — Claude API stubs
- `make_utterance`, `make_user`, `make_context_profile`,
  `make_observation`, `make_bullet` — factory fixtures
- `deepgram_emulator`, `deepgram_connect_fn`, `deepgram_post_fn` —
  Deepgram WebSocket + REST doubles

## Markers

| Marker | Meaning |
|---|---|
| `integration` | Live-API integration (skipped in default run) |
| `slow` | Takes > a few seconds |
| `eval` | LLM eval — real API calls, paid |
| `voiceprint` | Speaker embedding tests — skipped by default |

## Commands

```bash
pytest                                           # default (excludes voiceprint)
pytest --cov=backend --cov-report=term-missing   # coverage
pytest -m eval                                   # LLM evals (paid)
pytest -m integration                            # live-API integration
pytest tests/test_coaching_engine.py -v          # selective
```

## Tests by file

| File | Focus |
|---|---|
| `test_main.py` | FastAPI REST + WebSocket integration (100+ tests) |
| `test_profiler.py` | Superpower classification, 5-utterance window, carry-forward |
| `test_elm_detector.py` | Ego threat, shortcut mode, consensus protection |
| `test_scoring.py` | Persuasion/Growth scores, pure functions |
| `test_coaching_engine.py` | Cadence floor, priority queue, fallback path |
| `test_coaching_bullets.py` | Bullet store lifecycle (ACE) |
| `test_coaching_memory.py` | Per-person coaching memory |
| `test_coaching_quality.py` | Refusal detection, layer boost, feedback (49) |
| `test_transcription.py` | Deepgram reconnect, `is_final` handling |
| `test_transcriber_protocol.py` | Protocol contract, swappable backends |
| `test_moonshine_transcription.py` | Local Moonshine ASR fallback |
| `test_hybrid_transcription.py` | Cloud + local failover logic |
| `test_audio.py` | Named-pipe reader basics |
| `test_audio_buffer.py` | Ring buffer correctness |
| `test_audio_capture.py` | Swift capture boundary |
| `test_audio_lifecycle.py` | Multi-session, pipe cleanup, watchdog (44) |
| `test_audio_tcp_server.py` | Loopback TCP server (port 9090) |
| `test_audio_tcp_integration.py` | Swift→Python TCP end-to-end |
| `test_fifo_streaming.py` | FIFO streaming invariants |
| `test_ace_convergence.py` | ACE loop convergence over simulated sessions |
| `test_linkedin.py` | LinkedIn scraper URL + HTML extraction |
| `test_retro_import.py` | 9 transcript formats (VTT/SRT/Teams/Meet/Zoom/MD/JSON) |
| `test_transcript_parser.py` | Parser primitives |
| `test_speaker_resolver.py` | LLM speaker resolution, WS notifs, boost (45) |
| `test_speaker_embeddings.py` | Voiceprint (marker-gated) |
| `test_turn_tracker.py` | Vocative extraction, turn linking, cold start (28) |
| `test_signals.py` | Signal primitives |
| `test_phase1_signal_chain.py` | Echo filter, per-person coaching (50) |
| `test_self_assessment.py` | Self-assessment scoring |
| `test_sparring.py` | AI sparring partner loop |
| `test_pre_seeding.py` | Pre-seed classifier |
| `test_calendar_service.py` | Token refresh, participant matching |
| `test_calendar_auto_seed.py` | Auto-seed at session start |
| `test_team_sync.py` | Export/import, malformed JSON validation |
| `test_database.py` | Write, read, disk-full simulation |
| `test_models.py` | ORM model invariants |
| `test_bkt.py` | BKT convergence, skill opportunities, adversarial inputs |
| `test_fingerprint.py` | Device/user fingerprinting |
| `test_profile_benchmark.py` | Profiler benchmarks |
| `test_pipeline_e2e.py` | Full pipeline (excluded in CI) |

Related: [[LLM Evals]], [[CI Pipeline]], [[Coaching Engine Architecture]].
