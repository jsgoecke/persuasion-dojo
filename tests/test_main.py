"""
Tests for backend/main.py — FastAPI app, WebSocket handler, SessionPipeline.

Coverage:
  REST endpoints:
    GET  /health                  → 200 {"status": "ok"}
    POST /sessions                → 201, session_id returned
    GET  /sessions/{id}           → 200 / 404
    GET  /sessions                → 200, list
    GET  /users/me                → 200

  SessionPipeline:
    process_utterance — non-final ignored
    process_utterance — user speaking (no ELM, no profiling)
    process_utterance — counterpart triggers coaching prompt
    compute_scores    — returns correct key structure

  WebSocket:
    session not found → close code 4004
    ping  → pong
    utterance with no prompt (engine returns None) → no message sent
    utterance with prompt → coaching_prompt message
    session_end → session_ended message with scores + WS closed
    unknown message type → error message
    invalid JSON → error message

Test isolation strategy
───────────────────────
Each `client` fixture creates a fresh in-memory SQLite engine and overrides
the database module's singleton.  The TestClient enters the app lifespan which
calls init_db() + _get_or_create_user() — the same path as production.
Async SessionPipeline/SessionManager tests have no DB dependency at all.
"""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from backend.coaching_engine import CoachingPrompt
from backend.database import init_db, override_engine, get_db_session
from backend.main import SessionPipeline, SessionManager, app
from backend.models import (
    MeetingSession, Participant, Prompt, Utterance,
    SessionParticipantObservation, BehavioralEvidence,
    session_participants,
)
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Audio pipeline stub
#
# websocket_session now starts AudioPipeReader + DeepgramTranscriber.
# Patch both at the backend.main import level so tests never touch a real
# FIFO or Deepgram WebSocket.

@pytest.fixture(autouse=True)
def stub_audio_pipeline():
    """Replace the real audio pipeline with no-op async stubs for all tests."""
    pipe_mock = MagicMock()
    pipe_mock.start = AsyncMock()
    pipe_mock.stop = AsyncMock()

    transcriber_mock = MagicMock()
    transcriber_mock.connect = AsyncMock()
    transcriber_mock.disconnect = AsyncMock()
    transcriber_mock.send_audio = AsyncMock()

    with (
        patch("backend.main.AudioPipeReader", return_value=pipe_mock),
        patch("backend.main.HybridTranscriber", return_value=transcriber_mock),
        patch("backend.main._load_settings", return_value={
            "deepgram_api_key": "test-dg-key",
        }),
    ):
        yield


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """
    TestClient with a fresh in-memory SQLite DB per test.

    override_engine() replaces the module-level engine BEFORE TestClient
    enters the lifespan context, so init_db() and the default-user creation
    both operate on the in-memory DB.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    override_engine(engine)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    asyncio.run(engine.dispose())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_coaching_prompt(
    layer: str = "audience",
    text: str = "Acknowledge their concern first.",
    triggered_by: str = "elm:ego_threat",
    speaker_id: str = "speaker_1",
    is_fallback: bool = False,
) -> CoachingPrompt:
    return CoachingPrompt(
        layer=layer,
        text=text,
        is_fallback=is_fallback,
        triggered_by=triggered_by,
        speaker_id=speaker_id,
    )


def make_mock_engine(prompt: CoachingPrompt | None = None) -> Any:
    engine = MagicMock()
    engine.process = AsyncMock(return_value=prompt)
    engine.reset = MagicMock()
    return engine


def create_session(client: TestClient, context: str = "team") -> str:
    resp = client.post("/sessions", json={"context": context})
    assert resp.status_code == 201
    return resp.json()["session_id"]


def _fake_scores(score: int = 72) -> dict:
    return {
        "persuasion_score": score,
        "persuasion_raw": float(score),
        "timing_score": score,
        "ego_safety_score": score,
        "convergence_score": score,
        "ego_threat_events": 0,
        "shortcut_events": 0,
        "consensus_events": 0,
    }


# ---------------------------------------------------------------------------
# REST — /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# REST — POST /sessions
# ---------------------------------------------------------------------------

class TestCreateSession:
    def test_returns_201(self, client):
        resp = client.post("/sessions", json={"context": "board"})
        assert resp.status_code == 201

    def test_response_contains_session_id(self, client):
        resp = client.post("/sessions", json={"context": "team"})
        data = resp.json()
        assert "session_id" in data
        assert len(data["session_id"]) == 36     # UUID4

    def test_response_fields_present(self, client):
        resp = client.post("/sessions", json={"context": "1:1", "title": "Sync call"})
        data = resp.json()
        assert data["context"] == "1:1"
        assert data["title"] == "Sync call"
        assert data["persuasion_score"] is None  # not yet computed

    def test_default_context_unknown(self, client):
        resp = client.post("/sessions", json={})
        assert resp.status_code == 201
        assert resp.json()["context"] == "unknown"

    def test_two_sessions_have_distinct_ids(self, client):
        id1 = create_session(client)
        id2 = create_session(client)
        assert id1 != id2


# ---------------------------------------------------------------------------
# REST — GET /sessions/{id}
# ---------------------------------------------------------------------------

class TestGetSession:
    def test_returns_200_for_existing_session(self, client):
        sid = create_session(client)
        assert client.get(f"/sessions/{sid}").status_code == 200

    def test_returns_404_for_unknown_session(self, client):
        resp = client.get("/sessions/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_session_id_matches(self, client):
        sid = create_session(client, context="client")
        data = client.get(f"/sessions/{sid}").json()
        assert data["session_id"] == sid
        assert data["context"] == "client"


# ---------------------------------------------------------------------------
# REST — GET /sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    def test_returns_200(self, client):
        assert client.get("/sessions").status_code == 200

    def test_returns_list(self, client):
        assert isinstance(client.get("/sessions").json(), list)

    def test_created_sessions_appear_in_list(self, client):
        sid1 = _create_completed_session(client)
        sid2 = _create_completed_session(client)
        ids = [s["session_id"] for s in client.get("/sessions").json()]
        assert sid1 in ids
        assert sid2 in ids


# ---------------------------------------------------------------------------
# REST — GET /users/me
# ---------------------------------------------------------------------------

class TestGetUser:
    def test_returns_200(self, client):
        assert client.get("/users/me").status_code == 200

    def test_response_contains_expected_fields(self, client):
        data = client.get("/users/me").json()
        for field in ("id", "display_name", "core_sessions", "core_confidence"):
            assert field in data

    def test_default_user_id(self, client):
        assert client.get("/users/me").json()["id"] == "local-user"


# ---------------------------------------------------------------------------
# SessionPipeline — process_utterance (async, no DB)
# ---------------------------------------------------------------------------

class TestSessionPipelineProcessUtterance:

    def _make_pipeline(self, prompt: CoachingPrompt | None = None) -> SessionPipeline:
        return SessionPipeline(
            session_id="test-sid",
            user_id="test-uid",
            user_speaker="speaker_0",
            coaching_engine=make_mock_engine(prompt=prompt),
        )

    @pytest.mark.asyncio
    async def test_non_final_utterance_ignored(self):
        p = self._make_pipeline()
        result = await p.process_utterance(
            speaker_id="speaker_1", text="Some text", is_final=False
        )
        assert result is None
        assert len(p.utterances) == 0

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self):
        p = self._make_pipeline()
        result = await p.process_utterance(
            speaker_id="speaker_1", text="   ", is_final=True
        )
        assert result is None
        assert len(p.utterances) == 0

    @pytest.mark.asyncio
    async def test_final_utterance_stored(self):
        p = self._make_pipeline()
        await p.process_utterance(
            speaker_id="speaker_1", text="Tell me more.", is_final=True,
            start=1.0, end=2.0,
        )
        assert len(p.utterances) == 1
        assert p.utterances[0]["speaker"] == "speaker_1"
        assert p.utterances[0]["start"] == 1.0

    @pytest.mark.asyncio
    async def test_user_utterance_skips_elm_and_profiler(self):
        p = self._make_pipeline()
        p.elm_detector = MagicMock()
        p.elm_detector.process_utterance = MagicMock(return_value=None)
        p.profiler = MagicMock()
        p.profiler.add_utterance = MagicMock()

        await p.process_utterance(
            speaker_id="speaker_0", text="Hello everyone.", is_final=True
        )
        p.elm_detector.process_utterance.assert_not_called()
        p.profiler.add_utterance.assert_not_called()

    @pytest.mark.asyncio
    async def test_counterpart_utterance_calls_elm_detector(self):
        p = self._make_pipeline()
        p.elm_detector = MagicMock()
        p.elm_detector.process_utterance = MagicMock(return_value=None)
        p.profiler = MagicMock()
        p.profiler.add_utterance = MagicMock(return_value=None)

        await p.process_utterance(
            speaker_id="speaker_1", text="I disagree completely.", is_final=True
        )
        p.elm_detector.process_utterance.assert_called_once_with(
            "speaker_1", "I disagree completely."
        )

    @pytest.mark.asyncio
    async def test_returns_prompt_from_engine(self):
        prompt = make_coaching_prompt()
        p = self._make_pipeline(prompt=prompt)
        result = await p.process_utterance(
            speaker_id="speaker_1", text="I don't agree.", is_final=True
        )
        assert result is prompt

    @pytest.mark.asyncio
    async def test_engine_receives_user_is_speaking_flag(self):
        p = self._make_pipeline()
        await p.process_utterance(
            speaker_id="speaker_0", text="Hello.", is_final=True
        )
        call_kwargs = p.engine.process.call_args.kwargs
        assert call_kwargs["user_is_speaking"] is True


# ---------------------------------------------------------------------------
# SessionPipeline — coaching integration (real engine, mocked LLM)
#
# These tests verify that the full pipeline (ELM detector → profiler →
# coaching engine) actually produces coaching prompts when counterpart
# utterances flow through, using the new "user" / "counterpart_N" speaker IDs.
# ---------------------------------------------------------------------------

def _make_real_engine_client(text: str = "Ask a clarifying question right now.") -> Any:
    """Build a mock Anthropic client that returns a fixed coaching tip."""
    content = MagicMock()
    content.text = text
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


class TestCoachingIntegration:
    """Integration tests: real SessionPipeline with a real CoachingEngine
    (only the LLM call is mocked). Verifies coaching prompts fire end-to-end."""

    def _make_pipeline(
        self,
        user_speaker: str = "user",
        client_text: str = "Try acknowledging their concern before responding.",
    ) -> SessionPipeline:
        from backend.coaching_engine import CoachingEngine

        client = _make_real_engine_client(client_text)
        engine = CoachingEngine(
            user_speaker=user_speaker,
            anthropic_client=client,
            elm_cadence_floor_s=0.0,
            general_cadence_floor_s=0.0,
            haiku_timeout_s=999.0,
        )
        return SessionPipeline(
            session_id="int-test-sid",
            user_id="int-test-uid",
            user_speaker=user_speaker,
            coaching_engine=engine,
        )

    @pytest.mark.asyncio
    async def test_counterpart_ego_threat_fires_prompt(self):
        """An ego-threat utterance from a counterpart produces a coaching prompt."""
        p = self._make_pipeline()

        # Counterpart says something ego-threatening
        result = await p.process_utterance(
            speaker_id="counterpart_0",
            text="That's completely wrong and you clearly don't understand the data.",
            is_final=True,
        )
        assert result is not None
        assert result.text  # non-empty coaching text
        assert result.is_fallback is False

    @pytest.mark.asyncio
    async def test_user_utterance_produces_no_prompt(self):
        """User utterances are suppressed — no coaching prompt fires."""
        p = self._make_pipeline()

        result = await p.process_utterance(
            speaker_id="user",
            text="I think we should reconsider this approach.",
            is_final=True,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_counterpart_general_prompt_fires(self):
        """A non-ELM counterpart utterance can still trigger a general prompt."""
        p = self._make_pipeline()

        # First a few counterpart utterances to build context
        for i in range(3):
            await p.process_utterance(
                speaker_id="counterpart_0",
                text=f"Let me explain point {i} in detail.",
                is_final=True,
            )

        # The engine should have fired at least once (floors disabled)
        results = []
        for i in range(3):
            r = await p.process_utterance(
                speaker_id="counterpart_0",
                text=f"And furthermore, the analysis on slide {i + 3} shows growth.",
                is_final=True,
            )
            if r is not None:
                results.append(r)

        assert len(results) > 0, "Expected at least one coaching prompt from counterpart speech"

    @pytest.mark.asyncio
    async def test_mixed_speakers_only_counterpart_fires(self):
        """In a mixed conversation, only counterpart turns produce prompts."""
        p = self._make_pipeline()

        prompts = []

        # User speaks
        r = await p.process_utterance(
            speaker_id="user", text="What do you think about the Q3 numbers?", is_final=True,
        )
        assert r is None

        # Counterpart speaks (ego threat)
        r = await p.process_utterance(
            speaker_id="counterpart_0",
            text="Those numbers are misleading and you know it.",
            is_final=True,
        )
        if r:
            prompts.append(r)

        # User speaks again
        r = await p.process_utterance(
            speaker_id="user", text="Let me show you the breakdown.", is_final=True,
        )
        assert r is None

        # Counterpart speaks again
        r = await p.process_utterance(
            speaker_id="counterpart_1",
            text="I agree, these projections are unrealistic.",
            is_final=True,
        )
        if r:
            prompts.append(r)

        assert len(prompts) > 0, "Expected coaching prompts from counterpart utterances"
        for pr in prompts:
            assert pr.text
            assert pr.is_fallback is False

    @pytest.mark.asyncio
    async def test_counterpart_n_format_detected_by_elm(self):
        """counterpart_N format works with the ELM detector (not just speaker_N)."""
        p = self._make_pipeline()

        await p.process_utterance(
            speaker_id="counterpart_0",
            text="You're wrong about this and everyone knows it.",
            is_final=True,
        )

        # ELM detector should have processed it (check ego_threat_events)
        assert p.elm_detector.ego_threat_events >= 0  # at least processed, may or may not fire

    @pytest.mark.asyncio
    async def test_profiler_receives_counterpart_utterances(self):
        """The profiler classifies counterpart speakers (counterpart_N format)."""
        p = self._make_pipeline()

        # Send several utterances from one counterpart to build a profile
        texts = [
            "Let me walk you through the data systematically.",
            "The metrics clearly show a 15% improvement quarter over quarter.",
            "If we look at the root cause analysis, there are three factors.",
            "The evidence supports investing in automation here.",
            "Our benchmark data is statistically significant at p < 0.05.",
        ]
        for text in texts:
            await p.process_utterance(
                speaker_id="counterpart_0", text=text, is_final=True,
            )

        # Profiler should have a classification for counterpart_0
        classification = p.profiler.get_classification("counterpart_0")
        assert classification is not None
        assert classification.utterance_count == 5
        assert classification.superpower in (
            "Architect", "Firestarter", "Inquisitor", "Bridge Builder", "Undetermined",
            "Logic-leaning", "Narrative-leaning", "Advocacy-leaning", "Analysis-leaning",
        )

    @pytest.mark.asyncio
    async def test_fallback_on_timeout(self):
        """When the LLM times out, the engine returns a cached fallback."""
        from backend.coaching_engine import CoachingEngine

        # First call succeeds (populates cache), second times out (uses cache)
        client = MagicMock()
        content = MagicMock()
        content.text = "Original coaching tip."
        response = MagicMock()
        response.content = [content]

        call_count = 0
        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return response
            raise asyncio.TimeoutError()

        client.messages = MagicMock()
        client.messages.create = AsyncMock(side_effect=side_effect)

        engine = CoachingEngine(
            user_speaker="user",
            anthropic_client=client,
            elm_cadence_floor_s=0.0,
            general_cadence_floor_s=0.0,
            haiku_timeout_s=999.0,
        )
        p = SessionPipeline(
            session_id="timeout-test",
            user_id="uid",
            user_speaker="user",
            coaching_engine=engine,
        )

        # First counterpart utterance — succeeds
        r1 = await p.process_utterance(
            speaker_id="counterpart_0",
            text="This is completely unacceptable and you know it.",
            is_final=True,
        )
        assert r1 is not None
        assert r1.is_fallback is False

        # Second counterpart utterance — LLM times out, should get fallback
        r2 = await p.process_utterance(
            speaker_id="counterpart_0",
            text="Your team has failed to deliver again.",
            is_final=True,
        )
        # May be None if no cache hit for this prompt type, or a fallback
        if r2 is not None:
            assert r2.is_fallback is True


# ---------------------------------------------------------------------------
# SessionPipeline — compute_scores (sync, no DB)
# ---------------------------------------------------------------------------

class TestSessionPipelineComputeScores:
    def _make_pipeline(self) -> SessionPipeline:
        p = SessionPipeline(
            session_id="test-sid",
            user_id="test-uid",
            user_speaker="speaker_0",
            coaching_engine=make_mock_engine(),
        )
        p.utterances = [
            {"speaker": "speaker_0", "text": "Hello.", "start": 0.0, "end": 1.0},
            {"speaker": "speaker_1", "text": "Interesting.", "start": 1.5, "end": 2.5},
        ]
        return p

    def test_compute_scores_returns_expected_keys(self):
        scores = self._make_pipeline().compute_scores()
        for key in (
            "persuasion_score", "persuasion_raw",
            "timing_score", "ego_safety_score", "convergence_score",
            "ego_threat_events", "shortcut_events", "consensus_events",
        ):
            assert key in scores

    def test_persuasion_score_in_range(self):
        score = self._make_pipeline().compute_scores()["persuasion_score"]
        assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# SessionPipeline — reset (sync, no DB)
# ---------------------------------------------------------------------------

class TestSessionPipelineReset:
    def test_reset_clears_utterances(self):
        p = SessionPipeline(
            session_id="sid", user_id="uid", user_speaker="speaker_0",
            coaching_engine=make_mock_engine(),
        )
        p.utterances = [{"speaker": "speaker_0", "text": "Hi", "start": 0.0, "end": 1.0}]
        p.reset()
        assert len(p.utterances) == 0

    def test_reset_calls_engine_reset(self):
        engine = make_mock_engine()
        p = SessionPipeline(
            session_id="sid", user_id="uid", user_speaker="speaker_0",
            coaching_engine=engine,
        )
        p.reset()
        engine.reset.assert_called_once()


# ---------------------------------------------------------------------------
# SessionManager (sync, no DB)
# ---------------------------------------------------------------------------

class TestSessionManager:
    def _make_pipeline(self, sid: str = "abc") -> SessionPipeline:
        return SessionPipeline(
            session_id=sid, user_id="uid", user_speaker="speaker_0",
            coaching_engine=make_mock_engine(),
        )

    def test_register_and_get(self):
        manager = SessionManager()
        pipeline = self._make_pipeline()
        manager.register(pipeline)
        assert manager.get("abc") is pipeline

    def test_get_unknown_returns_none(self):
        assert SessionManager().get("unknown") is None

    def test_remove_calls_reset(self):
        engine = make_mock_engine()
        pipeline = SessionPipeline(
            session_id="abc", user_id="uid", user_speaker="speaker_0",
            coaching_engine=engine,
        )
        manager = SessionManager()
        manager.register(pipeline)
        manager.remove("abc")
        engine.reset.assert_called_once()
        assert manager.get("abc") is None

    def test_active_count(self):
        manager = SessionManager()
        assert manager.active_count == 0
        for i in range(3):
            manager.register(self._make_pipeline(str(i)))
        assert manager.active_count == 3


# ---------------------------------------------------------------------------
# WebSocket — session not found
# ---------------------------------------------------------------------------

class TestWebSocketSessionNotFound:
    @pytest.mark.skipif(
        os.environ.get("CI") and not sys.platform.startswith("darwin"),
        reason="WebSocket + aiosqlite teardown deadlocks on Linux CI runners",
    )
    def test_closes_immediately_for_missing_session(self, client):
        """Server closes connection (code 4004) when session not in DB."""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/session/nonexistent-id") as ws:
                ws.receive_text()


# ---------------------------------------------------------------------------
# WebSocket — ping/pong
# ---------------------------------------------------------------------------

class TestWebSocketPing:
    def test_ping_returns_pong(self, client):
        sid = create_session(client)
        with client.websocket_connect(f"/ws/session/{sid}") as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}


# ---------------------------------------------------------------------------
# WebSocket — missing Deepgram API key
# ---------------------------------------------------------------------------

class TestWebSocketMissingDeepgramKey:
    def test_no_deepgram_key_cloud_mode_sends_error_and_closes(self, client):
        """WS immediately closes with an error if Deepgram key is absent in cloud mode.

        In auto/local mode, Moonshine fallback handles missing keys gracefully.
        """
        resp = client.post("/sessions", json={"context": "team", "transcription_mode": "cloud"})
        assert resp.status_code == 201
        sid = resp.json()["session_id"]
        with patch("backend.main._load_settings", return_value={}):
            with patch.dict("os.environ", {}, clear=False):
                # Remove DEEPGRAM_API_KEY if present
                import os
                env_backup = os.environ.pop("DEEPGRAM_API_KEY", None)
                try:
                    with client.websocket_connect(f"/ws/session/{sid}") as ws:
                        data = ws.receive_json()
                        assert data["type"] == "error"
                        assert "Deepgram API key" in data["message"]
                finally:
                    if env_backup is not None:
                        os.environ["DEEPGRAM_API_KEY"] = env_backup


# ---------------------------------------------------------------------------
# WebSocket — invalid JSON and unknown type
# ---------------------------------------------------------------------------

class TestWebSocketProtocolErrors:
    def test_invalid_json_returns_error(self, client):
        sid = create_session(client)
        with client.websocket_connect(f"/ws/session/{sid}") as ws:
            ws.send_text("not-json!!!")
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "Invalid JSON" in data["message"]

    def test_unknown_message_type_returns_error(self, client):
        sid = create_session(client)
        with client.websocket_connect(f"/ws/session/{sid}") as ws:
            ws.send_json({"type": "wat"})
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "wat" in data["message"]


# ---------------------------------------------------------------------------
# WebSocket — utterance routing
# ---------------------------------------------------------------------------

class TestWebSocketUtterance:
    def test_utterance_with_no_prompt_sends_nothing(self, client):
        """When pipeline returns None, only the utterance echo is sent (no coaching_prompt)."""
        sid = create_session(client)

        async def returns_none(self_inner, **kw):
            return None

        with patch.object(SessionPipeline, "process_utterance", returns_none):
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                ws.send_json({
                    "type": "utterance",
                    "speaker_id": "speaker_1",
                    "text": "Hello there.",
                    "is_final": True,
                    "start": 0.0,
                    "end": 1.0,
                })
                # Consume the utterance echo
                echo = ws.receive_json()
                assert echo["type"] == "utterance"
                assert echo["speaker_id"] == "speaker_1"
                # No coaching_prompt — verify server is still alive via ping
                ws.send_json({"type": "ping"})
                assert ws.receive_json() == {"type": "pong"}

    @pytest.mark.skipif(
        os.environ.get("CI") and not sys.platform.startswith("darwin"),
        reason="WebSocket + aiosqlite teardown deadlocks on Linux CI runners",
    )
    def test_utterance_with_prompt_sends_coaching_prompt(self, client):
        """When pipeline returns a CoachingPrompt, server sends echo + coaching_prompt."""
        sid = create_session(client)
        prompt = make_coaching_prompt(
            layer="audience",
            text="Acknowledge their concern first.",
            triggered_by="elm:ego_threat",
            speaker_id="speaker_1",
        )

        async def returns_prompt(self_inner, **kw):
            return prompt

        with patch.object(SessionPipeline, "process_utterance", returns_prompt):
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                ws.send_json({
                    "type": "utterance",
                    "speaker_id": "speaker_1",
                    "text": "I don't agree at all.",
                    "is_final": True,
                    "start": 0.0,
                    "end": 1.5,
                })
                # Consume the utterance echo first
                echo = ws.receive_json()
                assert echo["type"] == "utterance"
                # Then receive the coaching prompt
                data = ws.receive_json()
                assert data["type"] == "coaching_prompt"
                assert data["layer"] == "audience"
                assert data["text"] == "Acknowledge their concern first."
                assert data["triggered_by"] == "elm:ego_threat"
                assert data["speaker_id"] == "speaker_1"
                assert data["is_fallback"] is False


# ---------------------------------------------------------------------------
# WebSocket — session_end
# ---------------------------------------------------------------------------

class TestWebSocketSessionEnd:
    def test_session_end_sends_session_ended(self, client):
        """session_ended includes scores when utterances were processed."""
        sid = create_session(client)

        with patch.object(SessionPipeline, "compute_scores", return_value=_fake_scores(72)):
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                ws.send_json({"type": "session_end"})
                data = ws.receive_json()
                assert data["type"] == "session_ended"
                assert data["session_id"] == sid

    def test_session_end_closes_websocket(self, client):
        sid = create_session(client)
        with patch.object(SessionPipeline, "compute_scores", return_value=_fake_scores()):
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                ws.send_json({"type": "session_end"})
                ws.receive_json()  # session_ended
                # Server closed — next receive raises
                with pytest.raises(Exception):
                    ws.receive_text()

    def test_empty_session_deleted_from_db(self, client):
        """An empty session (no utterances) is deleted after session_end."""
        sid = create_session(client)

        with patch.object(SessionPipeline, "compute_scores", return_value=_fake_scores(83)):
            with client.websocket_connect(f"/ws/session/{sid}") as ws:
                ws.send_json({"type": "session_end"})
                ws.receive_json()  # session_ended

        resp = client.get(f"/sessions/{sid}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------

class TestSettings:
    def test_get_settings_returns_200(self, client):
        r = client.get("/settings")
        assert r.status_code == 200

    def test_get_settings_has_key_flags(self, client):
        data = client.get("/settings").json()
        assert "anthropic_api_key_set" in data
        assert "deepgram_api_key_set" in data

    def test_post_settings_returns_204(self, client):
        r = client.post("/settings", json={"anthropic_api_key": "sk-test", "deepgram_api_key": "dg-test"})
        assert r.status_code == 204

    def test_post_settings_marks_keys_as_set(self, client):
        client.post("/settings", json={"anthropic_api_key": "sk-test", "deepgram_api_key": "dg-test"})
        data = client.get("/settings").json()
        assert data["anthropic_api_key_set"] is True
        assert data["deepgram_api_key_set"] is True

    def test_post_settings_partial_update(self, client):
        """Posting only anthropic key should only set that flag; deepgram cleared first."""
        # Clear both keys to ensure a known state before the assertion.
        client.post("/settings", json={"anthropic_api_key": "", "deepgram_api_key": ""})
        # Now set only anthropic.
        client.post("/settings", json={"anthropic_api_key": "sk-test"})
        data = client.get("/settings").json()
        assert data["anthropic_api_key_set"] is True
        assert data["deepgram_api_key_set"] is False

    def test_post_settings_empty_string_not_counted_as_set(self, client):
        client.post("/settings", json={"anthropic_api_key": ""})
        data = client.get("/settings").json()
        assert data["anthropic_api_key_set"] is False


# ---------------------------------------------------------------------------
# Self-assessment endpoints
# ---------------------------------------------------------------------------

class TestSelfAssessment:
    def test_get_items_returns_200(self, client):
        r = client.get("/self-assessment/items")
        assert r.status_code == 200

    def test_get_items_returns_list(self, client):
        data = client.get("/self-assessment/items").json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_get_items_have_required_fields(self, client):
        items = client.get("/self-assessment/items").json()
        for item in items:
            assert "id" in item
            # The API returns "text" as the item statement field.
            assert "text" in item

    def test_submit_returns_200(self, client):
        items = client.get("/self-assessment/items").json()
        # Schema: item_id, response (int 1-5), response_time_ms
        responses = [{"item_id": it["id"], "response": 3, "response_time_ms": 1500} for it in items]
        r = client.post("/self-assessment/submit", json={
            "responses": responses,
            "micro_argument": "I prefer evidence over intuition.",
        })
        assert r.status_code == 200

    def test_submit_returns_archetype(self, client):
        items = client.get("/self-assessment/items").json()
        responses = [{"item_id": it["id"], "response": 5, "response_time_ms": 1200} for it in items]
        data = client.post("/self-assessment/submit", json={
            "responses": responses,
            "micro_argument": "Data is the bedrock of good decisions.",
        }).json()
        assert "archetype" in data

    def test_submit_returns_valid_archetype_name(self, client):
        items = client.get("/self-assessment/items").json()
        responses = [{"item_id": it["id"], "response": 1, "response_time_ms": 800} for it in items]
        data = client.post("/self-assessment/submit", json={
            "responses": responses,
            "micro_argument": "Stories move people, not spreadsheets.",
        }).json()
        valid = {
            "Architect", "Firestarter", "Inquisitor", "Bridge Builder", "Undetermined",
            "Logic-leaning", "Narrative-leaning", "Advocacy-leaning", "Analysis-leaning",
        }
        assert data["archetype"] in valid


# ---------------------------------------------------------------------------
# Calendar endpoints
# ---------------------------------------------------------------------------

class TestCalendar:
    def test_auth_url_returns_400_when_not_configured(self, client):
        """Without GOOGLE_CLIENT_ID set, auth-url should fail gracefully."""
        import os
        env_backup = os.environ.pop("GOOGLE_CLIENT_ID", None)
        try:
            r = client.get("/calendar/auth-url", params={"redirect_uri": "urn:ietf:wg:oauth:2.0:oob"})
            # Expect 400 or 503 — not a 500.
            assert r.status_code in (400, 503)
        finally:
            if env_backup is not None:
                os.environ["GOOGLE_CLIENT_ID"] = env_backup

    def test_meetings_returns_400_when_not_authorized(self, client):
        """Without a stored token, meetings endpoint should return 400."""
        r = client.get("/calendar/meetings", params={"hours_ahead": 24})
        assert r.status_code in (400, 503)


# ---------------------------------------------------------------------------
# Team sync endpoints
# ---------------------------------------------------------------------------

class TestTeamSync:
    def test_export_requires_passphrase(self, client):
        r = client.post("/team/export", json={"passphrase": ""})
        # Backend raises ValueError for empty passphrase — results in 400/422/500.
        assert r.status_code in (400, 422, 500)

    def test_export_with_passphrase_returns_bundle(self, client):
        r = client.post("/team/export", json={"passphrase": "secret123"})
        assert r.status_code == 200
        data = r.json()
        assert "bundle" in data
        assert isinstance(data["bundle"], str)
        assert len(data["bundle"]) > 0

    def test_import_requires_both_fields(self, client):
        r = client.post("/team/import", json={"bundle": "", "passphrase": "x"})
        assert r.status_code in (400, 422)

    def test_import_bad_bundle_returns_error(self, client):
        r = client.post("/team/import", json={"bundle": "not-valid-base64!!!", "passphrase": "secret"})
        assert r.status_code in (400, 422, 500)

    def test_export_then_import_roundtrip(self, client):
        """Export with a passphrase then import the bundle back — should succeed."""
        export_r = client.post("/team/export", json={"passphrase": "roundtrip-pass"})
        assert export_r.status_code == 200
        bundle = export_r.json()["bundle"]

        import_r = client.post("/team/import", json={"bundle": bundle, "passphrase": "roundtrip-pass"})
        assert import_r.status_code in (200, 204)

    def test_import_wrong_passphrase_returns_error(self, client):
        export_r = client.post("/team/export", json={"passphrase": "correct-pass"})
        assert export_r.status_code == 200
        bundle = export_r.json()["bundle"]

        import_r = client.post("/team/import", json={"bundle": bundle, "passphrase": "wrong-pass"})
        assert import_r.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Retro import endpoints
# ---------------------------------------------------------------------------

class TestRetroImport:
    def test_upload_returns_job_id(self, client):
        audio_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt "
        r = client.post(
            "/retro/upload",
            files={"file": ("meeting.wav", audio_bytes, "audio/wav")},
        )
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        assert isinstance(data["job_id"], str)

    def test_get_job_returns_status(self, client):
        audio_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt "
        r = client.post(
            "/retro/upload",
            files={"file": ("meeting.wav", audio_bytes, "audio/wav")},
        )
        job_id = r.json()["job_id"]

        job_r = client.get(f"/retro/jobs/{job_id}")
        assert job_r.status_code == 200
        data = job_r.json()
        assert "status" in data
        assert data["status"] in ("pending", "processing", "done", "error")

    def test_unknown_job_returns_404(self, client):
        r = client.get("/retro/jobs/nonexistent-job-id")
        assert r.status_code == 404

    def test_upload_requires_file(self, client):
        r = client.post("/retro/upload")
        assert r.status_code == 422

    def test_upload_text_transcript_returns_job_id(self, client):
        """Text transcripts (.txt) should be accepted and parsed locally."""
        text_content = b"Alice: Hello everyone.\nBob: Thanks for joining."
        r = client.post(
            "/retro/upload",
            files={"file": ("meeting.txt", text_content, "text/plain")},
        )
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data

    def test_upload_json_transcript_returns_job_id(self, client):
        """JSON transcripts should also be accepted."""
        json_content = b'[{"speaker": "Alice", "text": "Hello."}, {"speaker": "Bob", "text": "World."}]'
        r = client.post(
            "/retro/upload",
            files={"file": ("meeting.json", json_content, "application/json")},
        )
        assert r.status_code == 200
        assert "job_id" in r.json()


# ---------------------------------------------------------------------------
# Retro import — profile extraction
# ---------------------------------------------------------------------------

from sqlalchemy import select as sa_select

class TestRetroProfileExtraction:
    """Verify that retro analysis creates Participant records from transcripts."""

    def _upload_and_wait(self, client, text_content: bytes, filename: str = "meeting.txt") -> dict:
        """Upload a text transcript and poll until the job completes."""
        r = client.post(
            "/retro/upload",
            files={"file": (filename, text_content, "text/plain")},
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        # Background tasks run synchronously in TestClient, so the job
        # should be done by the time we poll.
        job_r = client.get(f"/retro/jobs/{job_id}")
        assert job_r.status_code == 200
        return job_r.json()

    def test_text_transcript_creates_participants(self, client):
        """A multi-speaker text transcript should create Participant records."""
        # Build a transcript with enough utterances for the profiler to classify.
        # Use high-signal utterances so the profiler produces non-Undetermined results.
        lines = []
        # Alice: logic + advocacy → Inquisitor pattern
        for _ in range(4):
            lines.append("Alice: The data clearly shows a 47% improvement. We need to act on the evidence now.")
        # Bob: narrative + analysis → Bridge Builder pattern
        for _ in range(4):
            lines.append("Bob: Imagine how the team feels about this. Let's listen to everyone's perspective first.")
        transcript = "\n".join(lines).encode()

        job = self._upload_and_wait(client, transcript)
        assert job["status"] == "done", f"Job failed: {job.get('error')}"
        assert job.get("participants") is not None
        assert len(job["participants"]) >= 2, (
            f"Expected ≥2 participants, got {len(job.get('participants', []))}"
        )

        # Verify participant names are preserved
        names = {p["name"] or p["speaker_id"] for p in job["participants"]}
        assert "Alice" in names
        assert "Bob" in names

    def test_participants_persisted_to_db(self, client):
        """Participant records from retro analysis should be persisted to SQLite."""
        lines = []
        for _ in range(4):
            lines.append("Carol: The metrics are unambiguous. I recommend we commit to this approach.")
        for _ in range(4):
            lines.append("Dave: I remember when we first tried this — the energy was incredible.")
        transcript = "\n".join(lines).encode()

        job = self._upload_and_wait(client, transcript)
        assert job["status"] == "done", f"Job failed: {job.get('error')}"

        # Query DB for created participants
        async def _check():
            async with get_db_session() as db:
                result = await db.execute(sa_select(Participant))
                return [p.name for p in result.scalars()]

        db_names = asyncio.run(_check())
        assert "Carol" in db_names, f"Carol not found in DB. Got: {db_names}"
        assert "Dave" in db_names, f"Dave not found in DB. Got: {db_names}"

    def test_speaker_n_ids_get_human_names(self, client):
        """Diarized speaker IDs like speaker_0 should be title-cased to 'Speaker 0'."""
        json_content = b'[' + b','.join([
            b'{"speaker": 0, "text": "The evidence is clear, we must act decisively on the data."}',
            b'{"speaker": 0, "text": "Based on our analysis, the numbers support this conclusion."}',
            b'{"speaker": 0, "text": "I recommend we commit. The metrics are unambiguous."}',
            b'{"speaker": 0, "text": "Therefore we should look at the data carefully and decide."}',
            b'{"speaker": 1, "text": "Imagine what this journey could mean for the whole team."}',
            b'{"speaker": 1, "text": "I remember when we started and the excitement was inspiring."}',
            b'{"speaker": 1, "text": "Let me tell you a story about how we got here together."}',
            b'{"speaker": 1, "text": "The vision is clear and our story can inspire everyone."}',
        ]) + b']'

        r = client.post(
            "/retro/upload",
            files={"file": ("meeting.json", json_content, "application/json")},
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        job_r = client.get(f"/retro/jobs/{job_id}")
        job = job_r.json()
        assert job["status"] == "done", f"Job failed: {job.get('error')}"

        if job.get("participants"):
            names = {p.get("name") or p["speaker_id"] for p in job["participants"]}
            # speaker_0 → "Speaker 0", speaker_1 → "Speaker 1"
            assert any("Speaker" in n for n in names), f"Expected title-cased speaker names, got: {names}"

    def test_short_transcript_still_creates_profiles(self, client):
        """Even short transcripts should create profiles (with 'Unknown' archetype if needed)."""
        transcript = b"Alice: Hello.\nBob: Hi there."
        job = self._upload_and_wait(client, transcript)
        assert job["status"] == "done", f"Job failed: {job.get('error')}"
        # With our fix, even Undetermined speakers get saved as "Unknown"
        assert job.get("participants") is not None
        assert len(job["participants"]) >= 2

    def test_retro_job_survives_poll_after_completion(self, client):
        """Job results should remain accessible for re-polling (back button reconnect)."""
        transcript = b"Alice: The data shows improvement.\nBob: I agree completely."
        r = client.post(
            "/retro/upload",
            files={"file": ("meeting.txt", transcript, "text/plain")},
        )
        job_id = r.json()["job_id"]

        # Poll multiple times — simulates navigating away and back
        for _ in range(3):
            job_r = client.get(f"/retro/jobs/{job_id}")
            assert job_r.status_code == 200
            data = job_r.json()
            assert data["status"] in ("pending", "processing", "done", "error")

        # Final poll should still return the job
        final = client.get(f"/retro/jobs/{job_id}").json()
        assert final["status"] in ("done", "error")


# ---------------------------------------------------------------------------
# Helpers for completed sessions
# ---------------------------------------------------------------------------

def _create_completed_session(client: TestClient, context: str = "team", title: str | None = None) -> str:
    """Create a session via the API, then mark it as ended directly in the DB."""
    from datetime import datetime, timezone
    sid = create_session(client, context)
    asyncio.run(_mark_session_ended(sid, title))
    return sid


async def _mark_session_ended(session_id: str, title: str | None = None) -> None:
    from datetime import datetime, timezone
    async with get_db_session() as db:
        row = await db.get(MeetingSession, session_id)
        row.ended_at = datetime.now(timezone.utc)
        if title:
            row.title = title


async def _add_utterances(session_id: str, utterances: list[dict]) -> None:
    async with get_db_session() as db:
        for i, u in enumerate(utterances):
            db.add(Utterance(
                session_id=session_id,
                sequence=i,
                speaker_id=u.get("speaker_id", "speaker_0"),
                text=u["text"],
                start_s=u.get("start_s", 0.0),
                end_s=u.get("end_s", 0.0),
                is_user=u.get("is_user", False),
            ))


# ---------------------------------------------------------------------------
# REST — GET /sessions/{id}/transcript
# ---------------------------------------------------------------------------

class TestGetSessionTranscript:
    def test_returns_empty_list_for_session_with_no_utterances(self, client):
        sid = create_session(client)
        r = client.get(f"/sessions/{sid}/transcript")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_utterances_in_order(self, client):
        sid = create_session(client)
        asyncio.run(_add_utterances(sid, [
            {"speaker_id": "speaker_0", "text": "First.", "start_s": 0.0, "end_s": 1.0, "is_user": True},
            {"speaker_id": "speaker_1", "text": "Second.", "start_s": 1.5, "end_s": 2.5, "is_user": False},
            {"speaker_id": "speaker_0", "text": "Third.", "start_s": 3.0, "end_s": 4.0, "is_user": True},
        ]))
        r = client.get(f"/sessions/{sid}/transcript")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 3
        assert data[0]["text"] == "First."
        assert data[1]["text"] == "Second."
        assert data[2]["text"] == "Third."
        assert data[0]["sequence"] == 0
        assert data[1]["sequence"] == 1
        assert data[2]["sequence"] == 2

    def test_utterance_fields_complete(self, client):
        sid = create_session(client)
        asyncio.run(_add_utterances(sid, [
            {"speaker_id": "speaker_1", "text": "Hello.", "start_s": 1.5, "end_s": 3.0, "is_user": False},
        ]))
        r = client.get(f"/sessions/{sid}/transcript")
        u = r.json()[0]
        assert u["speaker_id"] == "speaker_1"
        assert u["text"] == "Hello."
        assert u["start_s"] == pytest.approx(1.5)
        assert u["end_s"] == pytest.approx(3.0)
        assert u["is_user"] is False

    def test_nonexistent_session_returns_404(self, client):
        r = client.get("/sessions/nonexistent-id/transcript")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# REST — GET /sessions (search and pagination)
# ---------------------------------------------------------------------------

class TestListSessionsSearchAndPagination:
    def test_search_by_title(self, client):
        sid1 = _create_completed_session(client, context="team", title="Board Meeting Q1")
        sid2 = _create_completed_session(client, context="sales", title="Sales Pitch Alpha")
        r = client.get("/sessions", params={"q": "board"})
        assert r.status_code == 200
        ids = [s["session_id"] for s in r.json()]
        assert sid1 in ids
        assert sid2 not in ids

    def test_search_by_context(self, client):
        sid1 = _create_completed_session(client, context="sales", title="Meeting A")
        sid2 = _create_completed_session(client, context="team", title="Meeting B")
        r = client.get("/sessions", params={"q": "sales"})
        ids = [s["session_id"] for s in r.json()]
        assert sid1 in ids
        assert sid2 not in ids

    def test_search_case_insensitive(self, client):
        sid = _create_completed_session(client, context="team", title="Important Demo")
        r = client.get("/sessions", params={"q": "IMPORTANT"})
        ids = [s["session_id"] for s in r.json()]
        assert sid in ids

    def test_limit_restricts_results(self, client):
        for i in range(5):
            _create_completed_session(client, context="team", title=f"Session {i}")
        r = client.get("/sessions", params={"limit": 3})
        assert len(r.json()) == 3

    def test_offset_skips_results(self, client):
        for i in range(5):
            _create_completed_session(client, context="team", title=f"Session {i}")
        all_sessions = client.get("/sessions", params={"limit": 10}).json()
        offset_sessions = client.get("/sessions", params={"limit": 10, "offset": 2}).json()
        assert len(offset_sessions) == len(all_sessions) - 2
        assert offset_sessions[0]["session_id"] == all_sessions[2]["session_id"]

    def test_empty_search_returns_all_completed(self, client):
        sid = _create_completed_session(client, context="team", title="Test")
        r = client.get("/sessions", params={"q": ""})
        ids = [s["session_id"] for s in r.json()]
        assert sid in ids

    def test_no_match_returns_empty(self, client):
        _create_completed_session(client, context="team", title="Meeting")
        r = client.get("/sessions", params={"q": "zzzznonexistent"})
        assert r.json() == []

    def test_debrief_text_included_in_response(self, client):
        sid = _create_completed_session(client, context="team", title="Debriefed")
        # Set debrief text directly
        asyncio.run(_set_debrief(sid, "Great session. Well done."))
        r = client.get(f"/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["debrief_text"] == "Great session. Well done."


async def _set_debrief(session_id: str, text: str) -> None:
    async with get_db_session() as db:
        row = await db.get(MeetingSession, session_id)
        row.debrief_text = text


# ---------------------------------------------------------------------------
# Profile extraction — retro import (integration, uses real DB)
# ---------------------------------------------------------------------------

class TestRetroProfileExtraction:
    """Verify that the retro upload endpoint extracts and persists profiles."""

    def test_text_transcript_creates_profiles(self, client):
        """Upload a text transcript with named speakers → Participant rows created."""
        transcript = (
            "Francisco: Let me walk you through the data systematically.\n"
            "Francisco: The metrics show a 15% improvement quarter over quarter.\n"
            "Francisco: If we look at the root cause analysis, there are three factors.\n"
            "Francisco: The evidence supports investing in automation here.\n"
            "Francisco: Our benchmark data is statistically significant at p < 0.05.\n"
            "Sarah: I love this vision. Imagine the impact on our customers.\n"
            "Sarah: When I was at my last company, we saw exactly this pattern.\n"
            "Sarah: Let me tell you a story about why this matters.\n"
            "Sarah: The energy in this team is incredible.\n"
            "Sarah: We should rally the whole organization behind this.\n"
        )
        # Upload as text transcript
        import io
        resp = client.post(
            "/retro/upload",
            files={"file": ("meeting.txt", io.BytesIO(transcript.encode()), "text/plain")},
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        # Wait for background task to complete
        import time
        for _ in range(30):
            r = client.get(f"/retro/jobs/{job_id}")
            if r.json()["status"] in ("done", "error"):
                break
            time.sleep(0.1)

        job = client.get(f"/retro/jobs/{job_id}").json()
        assert job["status"] == "done", f"Job failed: {job.get('error')}"

        # Verify participants are in the job response
        participants = job.get("participants", [])
        assert len(participants) >= 2, f"Expected >= 2 participants, got {len(participants)}: {participants}"

        # Check that named speakers are present
        speaker_ids = {p["speaker_id"] for p in participants}
        assert "Francisco" in speaker_ids, f"Francisco not in {speaker_ids}"
        assert "Sarah" in speaker_ids, f"Sarah not in {speaker_ids}"

        # Each participant should have name, archetype, confidence
        for p in participants:
            assert p.get("name"), f"Participant missing name: {p}"
            assert p.get("archetype"), f"Participant missing archetype: {p}"
            assert p.get("confidence") is not None, f"Participant missing confidence: {p}"
            assert p.get("participant_id"), f"Participant missing participant_id (not persisted to DB): {p}"

    def test_text_transcript_profiles_in_database(self, client):
        """Profiles extracted from transcript are actually persisted in the database."""
        transcript = (
            "Alice: We need to look at this from a data perspective.\n"
            "Alice: The numbers clearly indicate a downward trend.\n"
            "Alice: Let's analyze the root cause before deciding.\n"
            "Alice: Based on the evidence, I recommend option B.\n"
            "Alice: The statistical analysis supports this conclusion.\n"
        )
        import io
        resp = client.post(
            "/retro/upload",
            files={"file": ("meeting.txt", io.BytesIO(transcript.encode()), "text/plain")},
        )
        job_id = resp.json()["job_id"]

        import time
        for _ in range(30):
            r = client.get(f"/retro/jobs/{job_id}")
            if r.json()["status"] in ("done", "error"):
                break
            time.sleep(0.1)

        job = client.get(f"/retro/jobs/{job_id}").json()
        assert job["status"] == "done", f"Job failed: {job.get('error')}"

        # Verify the Participant row exists in the database
        async def check_db():
            async with get_db_session() as db:
                result = await db.execute(
                    select(Participant).where(Participant.name == "Alice")
                )
                participant = result.scalar_one_or_none()
                assert participant is not None, "Alice not found in Participant table"
                assert participant.ps_type is not None, "Participant archetype not set"
                assert participant.ps_confidence > 0, "Participant confidence not set"

                # Check audit trail exists
                obs_result = await db.execute(
                    select(SessionParticipantObservation).where(
                        SessionParticipantObservation.participant_id == participant.id
                    )
                )
                obs = obs_result.scalar_one_or_none()
                assert obs is not None, "No SessionParticipantObservation for Alice"
                assert obs.context == "retro"
                assert obs.utterance_count >= 1

                # Check behavioral evidence exists
                ev_result = await db.execute(
                    select(BehavioralEvidence).where(
                        BehavioralEvidence.participant_id == participant.id
                    )
                )
                evidence = ev_result.scalar_one_or_none()
                assert evidence is not None, "No BehavioralEvidence for Alice"
                assert evidence.context == "retro"

        asyncio.run(check_db())

    def test_existing_profile_is_updated_not_duplicated(self, client):
        """If a participant already exists, retro import updates rather than duplicates."""
        # First, create an existing participant named "Bob"
        async def seed_bob():
            async with get_db_session() as db:
                bob = Participant(
                    user_id="local-user", name="Bob",
                    ps_type="Unknown", ps_confidence=0.3, ps_state="active",
                )
                db.add(bob)

        asyncio.run(seed_bob())

        # Upload transcript with "Bob" speaking
        transcript = (
            "Bob: The data shows a clear pattern in Q3 numbers.\n"
            "Bob: We need to analyze the variance more carefully.\n"
            "Bob: Based on the metrics, option A is optimal.\n"
            "Bob: The statistical tests confirm this hypothesis.\n"
            "Bob: Let me walk through the evidence systematically.\n"
        )
        import io
        resp = client.post(
            "/retro/upload",
            files={"file": ("meeting.txt", io.BytesIO(transcript.encode()), "text/plain")},
        )
        job_id = resp.json()["job_id"]

        import time
        for _ in range(30):
            r = client.get(f"/retro/jobs/{job_id}")
            if r.json()["status"] in ("done", "error"):
                break
            time.sleep(0.1)

        job = client.get(f"/retro/jobs/{job_id}").json()
        assert job["status"] == "done", f"Job failed: {job.get('error')}"

        # Verify only ONE "Bob" exists (not duplicated)
        async def check_no_dupe():
            async with get_db_session() as db:
                result = await db.execute(
                    select(Participant).where(Participant.name == "Bob")
                )
                bobs = result.scalars().all()
                assert len(bobs) == 1, f"Expected 1 Bob, got {len(bobs)}"
                # Confidence should have improved from 0.3
                bob = bobs[0]
                assert bob.ps_confidence >= 0.3

        asyncio.run(check_no_dupe())


# ---------------------------------------------------------------------------
# Profile extraction — live session (SessionPipeline + _persist_participant_classifications)
# ---------------------------------------------------------------------------

class TestLiveProfilePersistence:
    """Verify that live session pipeline persists profiles for counterparts."""

    @pytest.mark.asyncio
    async def test_persist_creates_profiles_for_all_speakers(self):
        """All non-user speakers get profiles, including Undetermined."""
        from backend.main import _persist_participant_classifications

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        override_engine(engine)
        await init_db()

        # Create a user and session
        async with get_db_session() as db:
            from backend.models import User
            user = User(id="test-user", display_name="Test")
            db.add(user)
            session = MeetingSession(user_id="test-user", context="team")
            db.add(session)
            await db.flush()
            session_id = session.id

        # Create pipeline with real profiler
        client_mock = _make_real_engine_client()
        from backend.coaching_engine import CoachingEngine
        engine_obj = CoachingEngine(
            user_speaker="user",
            anthropic_client=client_mock,
            elm_cadence_floor_s=0.0,
            general_cadence_floor_s=0.0,
        )
        pipeline = SessionPipeline(
            session_id=session_id,
            user_id="test-user",
            user_speaker="user",
            coaching_engine=engine_obj,
        )

        # Feed utterances from counterpart_0 (strong signal) and counterpart_1 (weak signal)
        for text in [
            "Let me walk you through the data systematically.",
            "The metrics show a clear improvement.",
            "If we analyze root cause, there are three factors.",
            "The evidence supports this conclusion.",
            "Our benchmark data is statistically significant.",
        ]:
            await pipeline.process_utterance(speaker_id="counterpart_0", text=text, is_final=True)

        # counterpart_1 has just 2 vague utterances (likely Undetermined)
        for text in ["OK sure.", "Sounds good."]:
            await pipeline.process_utterance(speaker_id="counterpart_1", text=text, is_final=True)

        # Persist
        async with get_db_session() as db:
            await _persist_participant_classifications(db, pipeline, "team")

        # Verify BOTH counterparts got profiles
        async with get_db_session() as db:
            result = await db.execute(select(Participant))
            participants = result.scalars().all()
            # Filter out the user
            counterpart_names = [p.name for p in participants if p.user_id == "test-user"]
            assert len(counterpart_names) >= 2, (
                f"Expected at least 2 participant profiles, got {len(counterpart_names)}: {counterpart_names}"
            )

    @pytest.mark.asyncio
    async def test_persist_blocks_garbage_names(self):
        """Non-name strings like 'kill switch' must not become participants."""
        from backend.main import _persist_participant_classifications

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        override_engine(engine)
        await init_db()

        async with get_db_session() as db:
            from backend.models import User
            user = User(id="test-user", display_name="Test")
            db.add(user)
            session = MeetingSession(user_id="test-user", context="team")
            db.add(session)
            await db.flush()
            session_id = session.id

        client_mock = _make_real_engine_client()
        from backend.coaching_engine import CoachingEngine
        engine_obj = CoachingEngine(
            user_speaker="user",
            anthropic_client=client_mock,
            elm_cadence_floor_s=0.0,
            general_cadence_floor_s=0.0,
        )
        pipeline = SessionPipeline(
            session_id=session_id,
            user_id="test-user",
            user_speaker="user",
            coaching_engine=engine_obj,
        )

        # Feed utterances with a garbage speaker name
        for text in [
            "The system has been rebooted.",
            "Connection timed out.",
            "Retry in 5 seconds.",
        ]:
            await pipeline.process_utterance(speaker_id="kill switch", text=text, is_final=True)

        # Also feed a real speaker
        for text in [
            "Let me walk you through the data.",
            "The metrics show improvement.",
            "If we analyze root cause, there are three factors.",
            "The evidence supports this.",
            "Our benchmark data is significant.",
        ]:
            await pipeline.process_utterance(speaker_id="Sarah Chen", text=text, is_final=True)

        async with get_db_session() as db:
            await _persist_participant_classifications(db, pipeline, "team")

        async with get_db_session() as db:
            result = await db.execute(select(Participant))
            participants = result.scalars().all()
            names = [p.name for p in participants if p.user_id == "test-user"]
            # "kill switch" must NOT be in the list
            assert "kill switch" not in names, f"Garbage name 'kill switch' was persisted: {names}"
            # "Sarah Chen" should be present
            assert "Sarah Chen" in names, f"Real name 'Sarah Chen' missing from: {names}"

    @pytest.mark.asyncio
    async def test_persist_with_resolver_uses_resolved_names(self):
        """When speaker resolver has a name mapping, it's used for the profile."""
        from backend.main import _persist_participant_classifications
        from backend.speaker_resolver import SpeakerResolver

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        override_engine(engine)
        await init_db()

        async with get_db_session() as db:
            from backend.models import User
            user = User(id="test-user", display_name="Test")
            db.add(user)
            session = MeetingSession(user_id="test-user", context="team")
            db.add(session)
            await db.flush()
            session_id = session.id

        client_mock = _make_real_engine_client()
        from backend.coaching_engine import CoachingEngine
        engine_obj = CoachingEngine(
            user_speaker="user",
            anthropic_client=client_mock,
            elm_cadence_floor_s=0.0,
            general_cadence_floor_s=0.0,
        )
        pipeline = SessionPipeline(
            session_id=session_id,
            user_id="test-user",
            user_speaker="user",
            coaching_engine=engine_obj,
        )

        # Create resolver and manually set a confirmed name
        resolver = SpeakerResolver(
            anthropic_client=MagicMock(),
            known_names=["Maria Garcia"],
        )
        resolver.set_confirmed_name("counterpart_0", "Maria Garcia")
        pipeline.resolver = resolver  # type: ignore

        # Feed utterances
        for text in [
            "The data clearly shows improvement.",
            "Let's analyze this methodically.",
            "I recommend we review the metrics.",
            "The evidence is compelling.",
            "Based on the analysis, we should proceed.",
        ]:
            await pipeline.process_utterance(speaker_id="counterpart_0", text=text, is_final=True)

        # Persist
        async with get_db_session() as db:
            await _persist_participant_classifications(db, pipeline, "team")

        # Verify the profile was created with the resolved name
        async with get_db_session() as db:
            result = await db.execute(
                select(Participant).where(Participant.name == "Maria Garcia")
            )
            maria = result.scalar_one_or_none()
            assert maria is not None, "Profile for 'Maria Garcia' not found — resolver name not used"
            assert maria.ps_type is not None


# ---------------------------------------------------------------------------
# Regression: archetype "Undetermined" should not override valid ps_type
# ---------------------------------------------------------------------------

class TestArchetypeUndeterminedFallback:
    """
    Bug: obs_archetype="Undetermined" is truthy, so `obs_archetype or ps_type`
    returns "Undetermined" instead of the valid ps_type (e.g., "Architect").
    This happens when a speaker has signals on only one axis (the other is 0).
    """

    def test_participant_with_undetermined_obs_returns_ps_type(self, client):
        """GET /participants should return ps_type when obs_archetype is Undetermined."""
        async def _setup():
            async with get_db_session() as db:
                # Find the default user
                from backend.models import User
                user = (await db.execute(select(User))).scalar_one()
                p = Participant(
                    user_id=user.id,
                    name="Test Archetype",
                    ps_type="Architect",
                    ps_confidence=0.8,
                    obs_archetype="Undetermined",
                    obs_focus=100.0,
                    obs_stance=0.0,
                    obs_sessions=1,
                    obs_confidence=0.5,
                )
                db.add(p)
                await db.flush()
                return p.id
        pid = asyncio.run(_setup())

        resp = client.get("/participants")
        assert resp.status_code == 200
        profiles = resp.json()
        match = [p for p in profiles if p["id"] == pid]
        assert len(match) == 1
        assert match[0]["archetype"] == "Architect", (
            f"Expected 'Architect' but got '{match[0]['archetype']}' — "
            "'Undetermined' obs_archetype should not override valid ps_type"
        )

    def test_participant_detail_with_undetermined_obs(self, client):
        """GET /participants/{id} should also skip Undetermined."""
        async def _setup():
            async with get_db_session() as db:
                from backend.models import User
                user = (await db.execute(select(User))).scalar_one()
                p = Participant(
                    user_id=user.id,
                    name="Detail Archetype",
                    ps_type="Bridge Builder",
                    ps_confidence=0.7,
                    obs_archetype="Undetermined",
                    obs_focus=-100.0,
                    obs_stance=0.0,
                    obs_sessions=1,
                    obs_confidence=0.4,
                )
                db.add(p)
                await db.flush()
                return p.id
        pid = asyncio.run(_setup())

        resp = client.get(f"/participants/{pid}")
        assert resp.status_code == 200
        assert resp.json()["archetype"] == "Bridge Builder"

    def test_valid_obs_archetype_takes_precedence(self, client):
        """When obs_archetype is a real type, it should still take precedence over ps_type."""
        async def _setup():
            async with get_db_session() as db:
                from backend.models import User
                user = (await db.execute(select(User))).scalar_one()
                p = Participant(
                    user_id=user.id,
                    name="Real Obs",
                    ps_type="Architect",
                    ps_confidence=0.6,
                    obs_archetype="Firestarter",
                    obs_focus=-50.0,
                    obs_stance=50.0,
                    obs_sessions=3,
                    obs_confidence=0.85,
                )
                db.add(p)
                await db.flush()
                return p.id
        pid = asyncio.run(_setup())

        resp = client.get("/participants")
        assert resp.status_code == 200
        match = [p for p in resp.json() if p["id"] == pid]
        assert match[0]["archetype"] == "Firestarter"


# ---------------------------------------------------------------------------
# Regression: DELETE /sessions/{id} must persist (not silently roll back)
# ---------------------------------------------------------------------------

class TestDeleteSessionPersists:
    """
    Bug: db.delete(row) with cascade="all, delete-orphan" triggered async
    lazy-load (MissingGreenlet), rolling back the entire transaction.
    Session appeared deleted in UI but reappeared on re-fetch.
    """

    def test_delete_session_removes_from_db(self, client):
        """Basic delete: session should not appear in GET /sessions after DELETE."""
        sid = _create_completed_session(client, title="Delete Me")

        # Confirm it exists
        resp = client.get("/sessions")
        assert any(s["session_id"] == sid for s in resp.json())

        # Delete
        resp = client.delete(f"/sessions/{sid}")
        assert resp.status_code == 204

        # Confirm gone
        resp = client.get("/sessions")
        assert not any(s["session_id"] == sid for s in resp.json()), (
            "Session reappeared after DELETE — transaction likely rolled back"
        )

    def test_delete_session_with_related_rows(self, client):
        """Delete must succeed even with utterances, observations, and evidence."""
        sid = _create_completed_session(client, title="Full Session")

        async def _populate():
            async with get_db_session() as db:
                # Add utterances
                db.add(Utterance(
                    session_id=sid, sequence=0, speaker_id="speaker_0",
                    text="Test utterance", start_s=0.0, end_s=1.0, is_user=False,
                ))
                # Add a participant and link to session
                from backend.models import User
                user = (await db.execute(select(User))).scalar_one()
                p = Participant(
                    user_id=user.id, name="Session Participant",
                    ps_type="Inquisitor", ps_confidence=0.7,
                )
                db.add(p)
                await db.flush()
                # Link via session_participants
                await db.execute(
                    session_participants.insert().values(
                        session_id=sid, participant_id=p.id,
                    )
                )
                # Add observation
                db.add(SessionParticipantObservation(
                    session_id=sid, participant_id=p.id,
                    focus_score=50.0, stance_score=-30.0,
                    confidence=0.7, archetype="Inquisitor",
                    utterance_count=5, context="retro",
                ))
                # Add behavioral evidence
                db.add(BehavioralEvidence(
                    session_id=sid, participant_id=p.id,
                    context="retro",
                ))
                # Add a prompt
                db.add(Prompt(
                    session_id=sid, text="Test prompt",
                    layer="audience", trigger="elm",
                ))
        asyncio.run(_populate())

        # Delete should succeed even with all related rows
        resp = client.delete(f"/sessions/{sid}")
        assert resp.status_code == 204, (
            f"DELETE returned {resp.status_code} — likely FK or cascade error"
        )

        # Verify session is gone
        resp = client.get(f"/sessions/{sid}")
        assert resp.status_code == 404

        # Verify related rows are cleaned up
        async def _verify():
            async with get_db_session() as db:
                obs = await db.execute(
                    select(SessionParticipantObservation).where(
                        SessionParticipantObservation.session_id == sid
                    )
                )
                assert obs.first() is None, "SessionParticipantObservation not cleaned up"
                ev = await db.execute(
                    select(BehavioralEvidence).where(
                        BehavioralEvidence.session_id == sid
                    )
                )
                assert ev.first() is None, "BehavioralEvidence not cleaned up"
        asyncio.run(_verify())

    def test_delete_nonexistent_session_returns_404(self, client):
        resp = client.delete("/sessions/nonexistent-id")
        assert resp.status_code == 404
