---
title: Audio Pipeline
description: How audio flows from the meeting app through ScreenCaptureKit, the Swift binary, the TCP transport, and into the Python backend.
tags: [architecture, layer/audio]
type: concept
related:
  - "[[AudioCapture Binary]]"
  - "[[TCP Transport]]"
  - "[[Audio Lifecycle and Supervision]]"
  - "[[Backend - audio_tcp_server]]"
  - "[[Backend - audio]]"
  - "[[Transcription Pipeline]]"
updated: 2026-04-19
---

# Audio Pipeline

## Sequence diagram

```mermaid
sequenceDiagram
    participant Meeting as Zoom/Teams/Meet
    participant SCK as ScreenCaptureKit
    participant Mic as Microphone
    participant Swift as Swift AudioCapture
    participant Mixer as AudioMixer
    participant TCP as Loopback TCP
    participant Server as AudioTcpServer
    participant Reader as AudioTcpReader
    participant Transcriber as Deepgram / Moonshine

    Meeting->>SCK: system audio (Float32, variable rate)
    Mic->>Swift: hardware audio (AVAudioEngine)
    Swift->>Swift: downmix → mono Float32
    Swift->>Swift: resample → 16 kHz
    Swift->>Swift: convert → Int16 LE
    Swift->>Mixer: addScreenAudio(chunk)
    Swift->>Mixer: addMicAudio(chunk)
    loop every 10ms
        Mixer->>Mixer: flush ≤160 samples per stream
    end
    Swift->>TCP: handshake (0xAD + tag)
    Swift->>TCP: raw PCM stream
    TCP->>Server: handshake validated, tag → queue
    Server->>Reader: queue.put(chunk)
    Reader->>Transcriber: on_audio_chunk(chunk)
    Note over Reader: silence >5s → emit swift_restart_needed
```

## Stages

1. **Capture** — `SCShareableContent` gives system audio; `AVAudioEngine` taps the mic.
2. **Normalize** — the Swift binary downmixes multi-channel Float32 to mono, resamples to 16 kHz, and converts to Int16 LE. See [[AudioCapture Binary]].
3. **Mix & rate-control** — per-stream buffers in `AudioMixer`, flushed every 10 ms with a 1× realtime budget (~32 KB/s per stream).
4. **Transport** — two TCP connections (system tag `0x01`, mic tag `0x02`) to `127.0.0.1:AUDIO_TCP_PORT`. No length prefix — raw PCM after the 2-byte handshake. See [[TCP Transport]].
5. **Ingest** — the [[Backend - audio_tcp_server|AudioTcpServer]] validates the handshake, routes bytes to a per-tag `asyncio.Queue`, and parks connections for up to 30s if no reader is registered yet.
6. **Read** — the [[Backend - audio|AudioTcpReader]] drains the queue, forwards to `on_audio_chunk(chunk)`, and runs a [[Audio Lifecycle and Supervision|silence watchdog]].
7. **Transcription** — see [[Transcription Pipeline]].

## Why TCP instead of named pipes

Named pipes couple the Swift binary to the host filesystem, which breaks when the backend runs in Docker. Switching to loopback TCP means the host Swift can target either a host-native or a container-hosted backend transparently — the Docker compose file publishes `127.0.0.1:9090:9090`. Full rationale in `docs/superpowers/specs/2026-04-19-audio-tcp-transport-design.md` and the [[Design Docs Index]].

## Two parallel transcribers

The system audio stream has **diarization ON** (speaker labels `counterpart_0`, `counterpart_1`, …). The mic stream has diarization OFF — all utterances are labeled `user`.

An **echo filter** in the session pipeline suppresses system-side utterances whose word-set overlaps ≥60% with the last 10 mic utterances, preventing your own voice from being counted twice.

## Reference

- Source: `swift/AudioCapture/Sources/AudioCaptureCore/*`, `backend/audio.py`, `backend/audio_tcp_server.py`.
- Tests: `tests/test_audio*.py` — see [[Python Tests]].
- Spec: `docs/superpowers/specs/2026-04-19-audio-tcp-transport-design.md`.
