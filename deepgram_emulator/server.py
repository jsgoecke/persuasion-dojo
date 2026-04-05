"""
Deepgram API emulator — FastAPI server with WebSocket + REST endpoints.

Replays pre-recorded fixture data. No real transcription.
Validates Authorization header (any Token value accepted).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from deepgram_emulator.fixtures import Fixture, get_default_fixture, load_fixtures

logger = logging.getLogger(__name__)


def build_app(fixtures_dir: str | None = None) -> FastAPI:
    """Build the FastAPI app with loaded fixtures."""
    app = FastAPI(title="Deepgram Emulator")
    fixtures = load_fixtures(fixtures_dir)
    default = get_default_fixture(fixtures)

    def _resolve_fixture(scenario: str | None) -> Fixture:
        if scenario and scenario in fixtures:
            return fixtures[scenario]
        if default:
            return default
        raise HTTPException(status_code=500, detail="No fixtures loaded")

    def _check_auth(authorization: str | None) -> None:
        if not authorization or not authorization.startswith("Token "):
            raise HTTPException(status_code=401, detail="Unauthorized")

    # ── REST endpoint ──────────────────────────────────────────────────────

    @app.post("/v1/listen")
    async def listen_rest(
        request: Request,
        authorization: str | None = Header(None),
        scenario: str | None = Query(None),
        # Accept and ignore all Deepgram query params
        model: str | None = Query(None),
        diarize: str | None = Query(None),
        punctuate: str | None = Query(None),
        utterances: str | None = Query(None),
        utt_split: str | None = Query(None),
        sample_rate: str | None = Query(None),
        language: str | None = Query(None),
        smart_format: str | None = Query(None),
    ) -> dict[str, Any]:
        _check_auth(authorization)
        fixture = _resolve_fixture(scenario)
        # Consume body (audio bytes) but don't process
        await request.body()
        return fixture.rest_response

    # ── WebSocket endpoint ─────────────────────────────────────────────────

    @app.websocket("/v1/listen")
    async def listen_ws(
        ws: WebSocket,
        scenario: str | None = Query(None),
        # Accept and ignore all Deepgram query params
        model: str | None = Query(None),
        diarize: str | None = Query(None),
        punctuate: str | None = Query(None),
        smart_format: str | None = Query(None),
        interim_results: str | None = Query(None),
        utterance_end_ms: str | None = Query(None),
        endpointing: str | None = Query(None),
        vad_events: str | None = Query(None),
        no_delay: str | None = Query(None),
        encoding: str | None = Query(None),
        sample_rate: str | None = Query(None),
        channels: str | None = Query(None),
        language: str | None = Query(None),
    ) -> None:
        # Check auth from headers (WebSocket headers arrive before accept)
        auth = ws.headers.get("authorization", "")
        if not auth.startswith("Token "):
            await ws.close(code=4001, reason="Unauthorized")
            return

        await ws.accept()

        fixture = _resolve_fixture(scenario)
        events = fixture.streaming_events

        # Send events in background after first audio arrives
        audio_received = asyncio.Event()
        events_sent = asyncio.Event()

        async def send_events() -> None:
            await audio_received.wait()
            for event in events:
                try:
                    await ws.send_text(json.dumps(event))
                    await asyncio.sleep(0.05)
                except Exception:
                    break
            events_sent.set()

        sender = asyncio.create_task(send_events())

        try:
            while True:
                data = await ws.receive()
                if data.get("type") == "websocket.disconnect":
                    break

                raw = data.get("bytes") or data.get("text")
                if raw is None:
                    continue

                # Binary = audio frame
                if isinstance(raw, bytes):
                    audio_received.set()
                    continue

                # Text = JSON control message
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_type = msg.get("type", "")
                if msg_type == "KeepAlive":
                    continue
                elif msg_type == "Finalize":
                    # Flush remaining events immediately
                    audio_received.set()
                    continue
                elif msg_type == "CloseStream":
                    audio_received.set()
                    await events_sent.wait()
                    break
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            try:
                await ws.close()
            except Exception:
                pass

    return app


class DeepgramEmulator:
    """Manages the emulator server lifecycle for use in tests."""

    def __init__(self, fixtures_dir: str | None = None):
        self._fixtures_dir = fixtures_dir
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._port: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self._port}"

    def start(self, port: int = 0) -> str:
        """Start the emulator in a background thread. Returns the base URL."""
        import socket
        import time

        # Bind a socket first to get a free port reliably
        if port == 0:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            self._port = sock.getsockname()[1]
            sock.close()
        else:
            self._port = port

        app = build_app(self._fixtures_dir)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self._port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._server.serve())

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

        # Wait for server to actually start accepting connections
        for _ in range(50):
            time.sleep(0.1)
            if self._server.started:
                break

        return self.base_url

    def stop(self) -> None:
        """Shutdown the emulator."""
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
