# Audio TCP Transport — Design Spec

**Date:** 2026-04-19
**Branch:** `feat/audio-tcp-transport`
**Predecessor PR:** jsgoecke/persuasion-dojo#1 (dockerize backend)

## Goal

Replace the named-pipe (FIFO) audio transport between the Swift ScreenCaptureKit
binary and the Python backend with a loopback TCP transport. Unblocks live audio
from the host Swift binary into a Docker-containerized backend, and removes the
host-filesystem coupling that FIFOs impose.

## Non-goals

- Remote audio over the network. Transport binds to `127.0.0.1` only.
- Authentication, TLS, or cross-machine streaming. Loopback trust boundary.
- Supporting both FIFO and TCP in parallel. FIFO code is deleted in this PR.
- Re-building the Docker image to run Swift. Swift stays on the host.

## Decisions

| # | Decision | Rationale |
| - | -------- | --------- |
| 1 | Backend is the TCP server; Swift is the client | Container exposes a port; Swift can crash/restart and reconnect without coordinating with the long-lived backend. |
| 2 | One port, two connections (system + mic) | Minimal Docker port surface; raw PCM on each socket — no per-frame framing overhead. |
| 3 | 2-byte handshake per connection: magic `0xAD` + stream tag (`0x01`=system, `0x02`=mic) | Distinguishes streams without a length-prefix framing protocol. Magic byte catches unrelated traffic early. |
| 4 | Delete FIFO code entirely — no dual transport | YAGNI. Replaces `PipeWriter` / `AudioPipeReader` with TCP equivalents; FIFO code is not load-bearing elsewhere. |
| 5 | Loopback-only, no auth | Matches current FIFO posture (no auth). Any local process can already read the FIFOs. |
| 6 | Port is env-configurable (`AUDIO_TCP_PORT`), default `9090` | Avoids collision; tiny cost. |
| 7 | Swift hardcodes `127.0.0.1`, reads port from `AUDIO_BACKEND_PORT` env | No remote-backend story in V1. Electron forwards `AUDIO_TCP_PORT` (or the default `9090`) through as `AUDIO_BACKEND_PORT` when spawning the child. |
| 8 | Docker compose publishes `127.0.0.1:9090:9090` | Host Swift dials loopback; container listener accepts. No `host.docker.internal` indirection. |
| 9 | Silence watchdog behavior and WS `swift_restart_needed` event preserved | Existing Electron supervision keeps working unchanged. |

## Architecture

```
 Host (macOS)                             Backend (host or Docker)
 ┌────────────────────┐                   ┌──────────────────────────┐
 │ AudioCapture.swift │                   │ AudioTcpServer           │
 │                    │  TCP 127.0.0.1    │  (asyncio.start_server)  │
 │  TcpStreamWriter ──┼──────────────────►│ ├─► system conn demux    │
 │   ├─ system conn   │  first byte:      │ └─► mic    conn demux    │
 │   └─ mic    conn   │   0x01 system     │           ↓              │
 │                    │   0x02 mic        │   HybridTranscriber x 2  │
 └────────────────────┘                   └──────────────────────────┘
```

- Listener is created in FastAPI `lifespan` (process-scoped, not session-scoped).
- Two TCP connections per audio source; each carries a single stream for its
  lifetime. No multiplexing within a connection.
- Sessions register two `AudioTcpReader` instances (one per stream tag) with the
  server. If the matching connection is already parked, pending bytes drain to
  the new reader.
- Session end unregisters the readers and closes their half of the socket;
  Swift sees EPIPE and reconnects, where it parks until the next session.

## Wire protocol

**Handshake (client → server, exactly 2 bytes, once per connection):**

| Offset | Field       | Value                                  |
| ------ | ----------- | -------------------------------------- |
| 0      | Magic       | `0xAD`                                 |
| 1      | Stream tag  | `0x01` = system, `0x02` = mic          |

**Validation:**
- Wrong magic → server closes the socket and logs a warning.
- Duplicate stream tag for the active or pending slot → server closes the newer
  connection (defends against double-spawn races).

**Payload (client → server, raw PCM after handshake):**
- 16 kHz mono, `Int16` little-endian.
- No length prefix, no markers. Identical bytes to the current FIFO contents.
- Reader pulls with `recv(chunk_size=4096)` in a loop.

**Server → client:** nothing. Unidirectional.

**Close semantics:** TCP FIN / RST on either side. Server converts a zero-length
`recv()` to a silence-watchdog reset; Swift catches `EPIPE` and reconnects after
500 ms.

## Components

### Swift

- **New:** `swift/AudioCapture/Sources/AudioCapture/TcpStreamWriter.swift`
  - Same public API as the removed `PipeWriter`: `init(streamTag:)`, `start()`,
    `write(_ data: Data)`, `stop()`.
  - Opens `Socket(AF_INET, SOCK_STREAM)` to `127.0.0.1:<port>`.
  - Sends 2-byte handshake on connect; streams PCM.
  - On write failure / disconnect, closes fd, backs off 500 ms, reopens.
- **Deleted:** `swift/AudioCapture/Sources/AudioCapture/PipeWriter.swift`.
- **Modified:** `swift/AudioCapture/Sources/AudioCapture/main.swift`
  - Remove `mkfifo` + `unlink` plumbing.
  - Construct two `TcpStreamWriter` instances with distinct stream tags.
  - Read `AUDIO_BACKEND_PORT` from env; default `9090`.

### Backend

- **New:** `backend/audio_tcp_server.py`
  - `AudioTcpServer` class. Owns the `asyncio.start_server` listener on
    `127.0.0.1:<AUDIO_TCP_PORT>`.
  - Methods: `start()`, `stop()`, `register(stream_tag, queue)`,
    `unregister(stream_tag)`.
  - Handles per-connection handshake, routes payload bytes to the registered
    queue, parks connections (up to 30 s) when no reader is registered.
- **Modified:** `backend/audio.py`
  - `AudioPipeReader` replaced by `AudioTcpReader` with identical public
    surface: `start()`, `stop()`, `on_audio_chunk`, `on_silence_timeout`,
    `is_running`, `last_audio_time`.
  - Internally registers with the shared `AudioTcpServer` and awaits chunks
    via an `asyncio.Queue`.
  - Silence watchdog unchanged (5 s default).
  - All FIFO constants, `_ensure_pipe`, `_cleanup_pipe`, and `mkfifo` code
    are deleted.
- **Modified:** `backend/main.py`
  - Add FastAPI `lifespan` that starts/stops `AudioTcpServer`.
  - Swap `AudioPipeReader` → `AudioTcpReader` at every instantiation.
  - Plumb `AUDIO_TCP_PORT` env var.

### Docker / config

- **Modified:** `docker-compose.yml` — add `ports: - "127.0.0.1:9090:9090"`.
- **Modified:** `.env.example` — document `AUDIO_TCP_PORT`.
- **Modified:** `CLAUDE.md` — update the "Docker (backend only)" section: live
  audio now works from the host Swift binary with the container backend.

### Electron

- **Modified:** `frontend/overlay/src/main/index.ts` (`spawnCapture` at ~line
  107) — forward `AUDIO_BACKEND_PORT` env into the spawned child. Sourced from
  Electron's own env (same value the user sets for the backend's
  `AUDIO_TCP_PORT`; default `9090` on both sides).

## Error handling

| Scenario | Behavior |
| --- | --- |
| Backend not running at Swift start | `connect()` → ECONNREFUSED → retry every 500 ms. Mixer ring buffer absorbs in the meantime (same as today's "no reader" state). |
| Swift crashes mid-session | Server `recv()` returns 0 → silence watchdog fires after 5 s → WS emits `swift_restart_needed` → Electron respawns Swift → new handshake → session resumes. |
| Second Swift with same stream tag | Server closes the newer connection; logs warning. |
| Malformed handshake (wrong magic, unknown tag, truncated read) | Server closes; warning log. |
| `AUDIO_TCP_PORT` in use at startup | `asyncio.start_server` raises `OSError(EADDRINUSE)`; `lifespan` propagates; process exits with clear log. |
| No session registered when Swift connects | Server parks the socket in `pending[stream_tag]` for up to 30 s, then closes. When a session registers, parked bytes drain to its queue first. |
| Session ends while Swift connected | `AudioTcpReader.stop()` unregisters its queue and closes its socket half; Swift reconnects and parks until the next session. |
| Docker: host Swift → container backend | Works via compose's loopback-published port. |
| IPv4 / IPv6 | Both sides bind/dial `127.0.0.1` only. No IPv6. |

## Testing

- **Unit — `backend/audio_tcp_server.py`**
  - Valid handshake routes bytes to the registered queue.
  - Wrong magic closes socket.
  - Unknown stream tag closes socket.
  - Truncated handshake (1 byte then close) closes socket.
  - Duplicate stream tag with active reader → newer connection closed.
  - Pending park-and-drain: connection arrives before reader registers; bytes
    received during parking are delivered once reader registers.
  - Park timeout: connection closed after 30 s if no reader registers.
- **Unit — `backend/audio.py` (`AudioTcpReader`)**
  - Port the 44 tests in `tests/test_audio_lifecycle.py` — silence watchdog
    fires after inactivity; `stop()` is idempotent; multi-session lifecycle;
    session-end signaling. The transport changes; the contract does not.
- **Integration — `tests/test_audio_tcp_integration.py`**
  - Spin up `AudioTcpServer` on an ephemeral port, open an `asyncio` TCP client,
    send handshake + PCM, assert `on_audio_chunk` fires with the right bytes.
- **Swift — `swift test`**
  - New target: `TcpStreamWriter` connects to a Python listener on an ephemeral
    port; assert handshake bytes and subsequent payload match.
- **E2E (manual, documented)**
  - `docker compose up -d --build`, spawn Swift on host, connect WS, confirm
    `audio_level` and `final_transcript` messages flow.

## Migration & rollback

- **Migration:** single PR. No flags, no dual path. Merge deletes FIFO code and
  adds TCP code atomically.
- **Rollback:** `git revert` the merge commit. FIFO code returns verbatim.

## Out of scope / follow-ups

- Audio port auth / TLS (only becomes relevant when we ship a remote-backend
  deployment).
- Port discovery or mDNS (hardcoded + env-configurable is sufficient).
- Containerizing the Swift binary (blocked on macOS host requirement).
