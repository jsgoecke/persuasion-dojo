---
title: Backend Module Graph
description: Complete dependency graph of the 32 Python backend modules plus a ranked list of the most central nodes.
tags: [architecture, module, lang/python]
type: moc
related:
  - "[[System Overview]]"
  - "[[Backend - main]]"
  - "[[Backend - models]]"
updated: 2026-04-19
---

# Backend Module Graph

Every edge below is a real Python `import`. Obsidian Graph view reproduces this automatically from the `[[wikilinks]]` on each module note.

```mermaid
graph LR
    main["[[Backend - main|main.py]]"]
    models["[[Backend - models|models.py]]"]
    database["[[Backend - database|database.py]]"]
    audio["[[Backend - audio|audio.py]]"]
    audio_tcp["[[Backend - audio_tcp_server|audio_tcp_server.py]]"]
    transcription["[[Backend - transcription|transcription.py]]"]
    moonshine["[[Backend - moonshine_transcription|moonshine_transcription.py]]"]
    hybrid["[[Backend - hybrid_transcription|hybrid_transcription.py]]"]
    protocol["[[Backend - transcriber_protocol|transcriber_protocol.py]]"]
    profiler["[[Backend - profiler|profiler.py]]"]
    elm["[[Backend - elm_detector|elm_detector.py]]"]
    coach["[[Backend - coaching_engine|coaching_engine.py]]"]
    bullets["[[Backend - coaching_bullets|coaching_bullets.py]]"]
    memory["[[Backend - coaching_memory|coaching_memory.py]]"]
    scoring["[[Backend - scoring|scoring.py]]"]
    signals["[[Backend - signals|signals.py]]"]
    resolver["[[Backend - speaker_resolver|speaker_resolver.py]]"]
    turn["[[Backend - turn_tracker|turn_tracker.py]]"]
    embed["[[Backend - speaker_embeddings|speaker_embeddings.py]]"]
    parser["[[Backend - transcript_parser|transcript_parser.py]]"]
    identity["[[Backend - identity|identity.py]]"]
    self_a["[[Backend - self_assessment|self_assessment.py]]"]
    preseed["[[Backend - pre_seeding|pre_seeding.py]]"]
    finger["[[Backend - fingerprint|fingerprint.py]]"]
    sparring["[[Backend - sparring|sparring.py]]"]
    cal["[[Backend - calendar_service|calendar_service.py]]"]
    linkedin["[[Backend - linkedin|linkedin.py]]"]
    retro["[[Backend - retro_import|retro_import.py]]"]
    team["[[Backend - team_sync|team_sync.py]]"]
    seed["[[Backend - seed_tips|seed_tips.py]]"]

    main --> models
    main --> database
    main --> audio
    main --> hybrid
    main --> profiler
    main --> elm
    main --> coach
    main --> bullets
    main --> scoring
    main --> sparring
    main --> cal
    main --> team
    audio --> audio_tcp
    hybrid --> transcription
    hybrid --> moonshine
    hybrid --> protocol
    transcription --> protocol
    moonshine --> protocol
    models --> database
    models --> self_a
    models --> scoring
    coach --> models
    coach --> bullets
    coach --> memory
    coach --> elm
    coach --> profiler
    bullets --> models
    memory --> models
    profiler --> models
    profiler --> self_a
    profiler --> preseed
    scoring --> models
    scoring --> signals
    scoring --> self_a
    elm --> models
    resolver --> identity
    resolver --> embed
    resolver --> turn
    finger --> models
    sparring --> preseed
    self_a --> preseed
    parser --> identity
    identity --> models
    seed --> bullets
    seed --> database
    seed --> models
    retro --> protocol
    linkedin --> preseed
```

## Most central modules

Ranked by total in-degree + out-degree:

1. **[[Backend - models|models.py]]** — imported by 12+ modules. The ORM + data layer is the hub.
2. **[[Backend - main|main.py]]** — imports 13+ modules. Orchestrates every session.
3. **[[Backend - coaching_engine|coaching_engine.py]]** — imports 5, imported by main. Decision-maker.
4. **[[Backend - profiler|profiler.py]]** — imports 3, imported by 2. Behavioural signal source.
5. **[[Backend - scoring|scoring.py]]** — imports 3, imported by 2. Pure-function scoring layer.
6. **[[Backend - self_assessment|self_assessment.py]]** — imported by 5. Archetype mapping.
7. **[[Backend - transcriber_protocol|transcriber_protocol.py]]** — imported by 3 transcribers. Interface.

## Layer grouping

- **Transport** — audio, audio_tcp_server, transcription, moonshine_transcription, hybrid_transcription, transcriber_protocol.
- **Identity** — identity, speaker_resolver, turn_tracker, speaker_embeddings.
- **Behavior** — profiler, elm_detector, signals.
- **Profile** — models, self_assessment, pre_seeding, fingerprint.
- **Coaching** — coaching_engine, coaching_bullets, coaching_memory, scoring, seed_tips.
- **Orchestration** — main, database, calendar_service, team_sync, sparring, retro_import, transcript_parser, linkedin.
