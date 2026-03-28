"""
FastAPI application — HTTP endpoints + WebSocket coaching server.

Architecture overview
─────────────────────
                      ┌──────────────────────────────────────┐
  Electron overlay    │  WebSocket /ws/session/{session_id}  │
  ──────────────────► │                                      │
  utterance msgs      │  SessionPipeline                     │
                      │    ├─ ELMDetector                    │
  ◄──────────────────  │    ├─ ParticipantProfiler            │
  coaching_prompt msgs│    ├─ UserBehaviorObserver           │
                      │    └─ CoachingEngine → Haiku         │
                      └──────────────────────────────────────┘
                                      │
                              SQLite (WAL mode)
                              via get_db_session()

Session lifecycle
──────────────────
1.  POST /sessions            → create MeetingSession row → returns session_id
2.  WS  /ws/session/{id}      → connect and stream utterances
3.  WS  msg type="session_end"→ score computed, session row updated, WS closed
4.  GET /sessions/{id}        → read session summary (debrief)

WebSocket message protocol
───────────────────────────
Client → server:
    {"type": "utterance", "speaker_id": "speaker_0", "text": "...",
     "is_final": true, "start": 12.3, "end": 14.1}
    {"type": "ping"}
    {"type": "session_end"}

Server → client:
    {"type": "coaching_prompt", "layer": "audience", "text": "...",
     "is_fallback": false, "triggered_by": "elm:ego_threat", "speaker_id": "speaker_1"}
    {"type": "pong"}
    {"type": "session_ended", "session_id": "...", "persuasion_score": 72,
     "growth_delta": null}
    {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv

load_dotenv()
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

from anthropic import AsyncAnthropic
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import anthropic as _anthropic

from backend.audio import AudioPipeReader
from backend.calendar_service import CalendarService, WatchChannel
from backend.coaching_engine import CoachingEngine, CoachingPrompt
from backend.coaching_memory import update_playbook  # legacy fallback, kept for compatibility
from backend.database import get_db_session, init_db
from backend.elm_detector import ELMDetector
from backend.models import (
    BehavioralEvidence,
    CoachingEffectiveness,
    ContextProfile,
    MeetingSession,
    Participant,
    ParticipantContextProfile,
    Prompt,
    SessionParticipantObservation,
    SkillBadge,
    Utterance,
    User,
    VALID_CONTEXTS,
    apply_participant_observation,
    apply_session_observation,
    get_profile_snapshot,
    ProfileSnapshot,
)
from backend.pre_seeding import classify as _preseed_classify
from backend.profiler import ParticipantProfiler, UserBehaviorObserver
from backend.scoring import (
    BADGE_METADATA,
    compute_growth_score,
    compute_persuasion_score,
    compute_prompt_effectiveness,
    compute_skill_badges,
    update_coaching_effectiveness,
)
from backend.self_assessment import (
    ITEMS as _ASSESSMENT_ITEMS,
    AssessmentResponse,
    build_result as _sa_build_result,
    classify_micro_argument as _sa_classify_micro,
    score_responses as _sa_score,
)
from backend.sparring import SparringSession
from backend.team_sync import TeamSync as _TeamSync, ParticipantRecord
from backend.transcription import DeepgramTranscriber


# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------

_DEFAULT_USER_ID = "local-user"        # single-user V1 app
_DEFAULT_USER_SPEAKER = "speaker_0"    # Deepgram diarization convention

# In-memory sparring sessions (no DB persistence — text-only practice mode).
_sparring_sessions: dict[str, SparringSession] = {}


# ---------------------------------------------------------------------------
# SessionPipeline — all per-session stateful objects in one place
# ---------------------------------------------------------------------------

class SessionPipeline:
    """
    Encapsulates the full real-time coaching pipeline for one session.

    Instantiated once per WebSocket connection and discarded at session end.
    The CoachingEngine can be injected for testing (defaults to live Haiku).
    """

    def __init__(
        self,
        session_id: str,
        user_id: str,
        user_speaker: str = _DEFAULT_USER_SPEAKER,
        coaching_engine: CoachingEngine | None = None,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.user_speaker = user_speaker

        self.elm_detector = ELMDetector(user_speaker=user_speaker)
        self.profiler = ParticipantProfiler()
        self.observer = UserBehaviorObserver(user_speaker=user_speaker)
        self.engine = coaching_engine or CoachingEngine(
            user_speaker=user_speaker,
            anthropic_client=AsyncAnthropic(),
            user_id=user_id,
        )

        # Full utterance list for end-of-session scoring
        # Each entry: {"speaker": str, "text": str, "start": float, "end": float}
        self.utterances: list[dict[str, Any]] = []

        # User's profile snapshot — loaded at session start for coaching context
        self.user_profile: ProfileSnapshot | None = None

    async def process_utterance(
        self,
        speaker_id: str,
        text: str,
        is_final: bool = True,
        start: float = 0.0,
        end: float = 0.0,
    ) -> CoachingPrompt | None:
        """
        Run one utterance through the coaching pipeline.

        Non-final utterances (Deepgram interim results) are ignored.
        Returns a CoachingPrompt when the engine decides to fire one, else None.
        """
        if not is_final or not text.strip():
            return None

        self.utterances.append(
            {"speaker": speaker_id, "text": text, "start": start, "end": end}
        )

        user_is_speaking = speaker_id == self.user_speaker
        elm_event = None
        participant_profile = None

        if not user_is_speaking:
            elm_event = self.elm_detector.process_utterance(speaker_id, text)
            participant_profile = self.profiler.add_utterance(speaker_id, text)

        self.observer.add_utterance(speaker_id, text)

        # Pass the last 10 utterances so the coaching engine has conversation
        # context (not just the current utterance).
        recent = self.utterances[-10:] if self.utterances else []

        return await self.engine.process(
            elm_event=elm_event,
            participant_profile=participant_profile,
            user_profile=self.user_profile,
            user_is_speaking=user_is_speaking,
            recent_transcript=recent,
        )

    def compute_scores(self) -> dict[str, Any]:
        """
        Compute Persuasion Score at session end.

        Returns a dict suitable for serialising into the session_ended message
        and persisting to the MeetingSession row.
        """
        result = compute_persuasion_score(
            self.utterances,
            self.user_speaker,
            ego_threat_events=self.elm_detector.ego_threat_events,
        )
        return {
            "persuasion_score": result.score,
            "persuasion_raw": result.raw,
            "timing_score": result.timing.score,
            "ego_safety_score": result.ego_safety.score,
            "convergence_score": result.convergence.score,
            "ego_threat_events": self.elm_detector.ego_threat_events,
            "shortcut_events": self.elm_detector.shortcut_events,
            "consensus_events": self.elm_detector.consensus_events,
        }

    def reset(self) -> None:
        """Release all session state. Call after the session ends."""
        self.elm_detector.reset()
        self.profiler.reset()
        self.observer.reset()
        self.engine.reset()
        self.utterances.clear()


# ---------------------------------------------------------------------------
# SessionManager — tracks active WebSocket sessions
# ---------------------------------------------------------------------------

class SessionManager:
    """Simple in-memory registry of active SessionPipeline instances."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionPipeline] = {}

    def register(self, pipeline: SessionPipeline) -> None:
        self._sessions[pipeline.session_id] = pipeline

    def get(self, session_id: str) -> SessionPipeline | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        pipeline = self._sessions.pop(session_id, None)
        if pipeline:
            pipeline.reset()

    @property
    def active_count(self) -> int:
        return len(self._sessions)


_session_manager = SessionManager()


def _get_calendar_service() -> CalendarService | None:
    """Return a CalendarService if Google OAuth credentials are configured."""
    settings = _load_settings()
    client_id = settings.get("google_client_id") or os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = settings.get("google_client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return CalendarService(client_id=client_id, client_secret=client_secret)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_create_user(db: AsyncSession) -> User:
    """Return the default local user, creating it if needed."""
    result = await db.execute(select(User).where(User.id == _DEFAULT_USER_ID))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(id=_DEFAULT_USER_ID, display_name="Local User")
        db.add(user)
        await db.flush()
    return user


async def _prior_scores(db: AsyncSession, user_id: str) -> list[int]:
    """Return recent Persuasion Scores for growth delta computation."""
    rows = await db.execute(
        select(MeetingSession.persuasion_score)
        .where(
            MeetingSession.user_id == user_id,
            MeetingSession.persuasion_score.is_not(None),
        )
        .order_by(MeetingSession.started_at.desc())
        .limit(10)
    )
    return [r for (r,) in rows if r is not None]


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class _ParticipantInfo(BaseModel):
    name: str
    archetype: str  # "Architect" | "Firestarter" | "Inquisitor" | "Bridge Builder"


class CreateSessionRequest(BaseModel):
    context: str = Field(default="unknown", description="Meeting context type")
    title: str | None = Field(default=None, description="Meeting title (from calendar)")
    user_speaker: str = Field(
        default=_DEFAULT_USER_SPEAKER,
        description="Diarization speaker ID for the coached user",
    )
    user_archetype: str | None = Field(default=None, description="User's Communicator Superpower")
    participants: list[_ParticipantInfo] = Field(default_factory=list, description="Known meeting participants")


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    context: str
    title: str | None
    persuasion_score: int | None
    started_at: str
    debrief_text: str | None = None


class WatchRequest(BaseModel):
    webhook_url: str = Field(description="Public HTTPS URL for Google to POST notifications to")
    expiration_seconds: int = Field(
        default=604_800,
        ge=3600,
        le=604_800,
        description="Requested channel lifetime (1 h – 7 days). Google caps at 604 800 s.",
    )


class WatchResponse(BaseModel):
    channel_id: str
    resource_id: str
    expires_at: float
    needs_renewal: bool


# ---------------------------------------------------------------------------
# App + lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the database on startup."""
    await init_db()
    async with get_db_session() as db:
        await _get_or_create_user(db)
    yield


app = FastAPI(
    title="Persuasion Dojo",
    description="Real-time conversation coaching API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Electron overlay connects from file://
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# In-memory coaching context attached at session creation, read by the WS handler.
_session_coaching_context: dict[str, dict] = {}


@app.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(body: CreateSessionRequest) -> SessionResponse:
    """Create a new MeetingSession and return its ID."""
    async with get_db_session() as db:
        user = await _get_or_create_user(db)
        session = MeetingSession(
            user_id=user.id,
            context=body.context,
            title=body.title,
            coaching_context=body.context,
        )
        db.add(session)
        await db.flush()

        # Stash archetype info for the WebSocket handler to pick up.
        _session_coaching_context[session.id] = {
            "user_archetype": body.user_archetype,
            "participants": [
                {"name": p.name, "archetype": p.archetype}
                for p in body.participants
            ],
        }

        return SessionResponse(
            session_id=session.id,
            user_id=user.id,
            context=session.context,
            title=session.title,
            persuasion_score=session.persuasion_score,
            started_at=session.started_at.isoformat(),
        )


@app.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str) -> SessionResponse:
    async with get_db_session() as db:
        row = await db.get(MeetingSession, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionResponse(
            session_id=row.id,
            user_id=row.user_id,
            context=row.context,
            title=row.title,
            persuasion_score=row.persuasion_score,
            started_at=row.started_at.isoformat(),
            debrief_text=row.debrief_text,
        )


@app.get("/sessions/{session_id}/transcript")
async def get_session_transcript(session_id: str) -> list[dict]:
    """Return the stored utterances for a session in chronological order."""
    async with get_db_session() as db:
        row = await db.get(MeetingSession, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        result = await db.execute(
            select(Utterance)
            .where(Utterance.session_id == session_id)
            .order_by(Utterance.sequence)
        )
        return [
            {
                "sequence": u.sequence,
                "speaker_id": u.speaker_id,
                "text": u.text,
                "start_s": u.start_s,
                "end_s": u.end_s,
                "is_user": u.is_user,
            }
            for u in result.scalars()
        ]


@app.get("/sessions")
async def list_sessions(
    limit: int = 20,
    offset: int = 0,
    q: str | None = None,
) -> list[SessionResponse]:
    """
    List completed sessions newest-first.

    limit:  max results (default 20, frontend uses 5 for recents)
    offset: skip N sessions for pagination
    q:      optional search term matched against title and context (case-insensitive)
    """
    async with get_db_session() as db:
        stmt = (
            select(MeetingSession)
            .where(MeetingSession.ended_at.isnot(None))
            .order_by(MeetingSession.started_at.desc())
        )
        if q:
            term = f"%{q.lower()}%"
            from sqlalchemy import func
            stmt = stmt.where(
                func.lower(MeetingSession.title).like(term)
                | func.lower(MeetingSession.context).like(term)
            )
        stmt = stmt.offset(offset).limit(limit)
        rows = await db.execute(stmt)
        return [
            SessionResponse(
                session_id=r.id,
                user_id=r.user_id,
                context=r.context,
                title=r.title,
                persuasion_score=r.persuasion_score,
                started_at=r.started_at.isoformat(),
                debrief_text=r.debrief_text,
            )
            for (r,) in rows
        ]


@app.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    async with get_db_session() as db:
        row = await db.get(MeetingSession, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        # Explicitly delete related rows (SQLite FK cascade not guaranteed)
        from backend.models import session_participants
        await db.execute(
            session_participants.delete().where(session_participants.c.session_id == session_id)
        )
        await db.execute(
            delete(Utterance).where(Utterance.session_id == session_id)
        )
        await db.execute(
            delete(Prompt).where(Prompt.session_id == session_id)
        )
        await db.delete(row)


@app.get("/calendar/watch")
async def get_watch_status() -> dict[str, Any]:
    """Return the current push-watch channel status."""
    svc = _get_calendar_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Google Calendar not configured")
    ch = svc.active_watch
    if ch is None:
        return {"active": False}
    return {
        "active": ch.is_active,
        "channel_id": ch.channel_id,
        "resource_id": ch.resource_id,
        "expires_at": ch.expires_at,
        "needs_renewal": ch.needs_renewal,
    }


@app.post("/calendar/watch", response_model=WatchResponse, status_code=201)
async def register_watch(body: WatchRequest) -> WatchResponse:
    """
    Register a Google Calendar push-notification channel.

    Google will POST to ``webhook_url`` whenever the user's primary calendar
    changes.  Requires the cloud backend (V2) — the webhook URL must be
    publicly reachable.
    """
    svc = _get_calendar_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Google Calendar not configured")
    try:
        channel = await svc.register_push_watch(
            webhook_url=body.webhook_url,
            expiration_seconds=body.expiration_seconds,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return WatchResponse(
        channel_id=channel.channel_id,
        resource_id=channel.resource_id,
        expires_at=channel.expires_at,
        needs_renewal=channel.needs_renewal,
    )


@app.delete("/calendar/watch", status_code=204)
async def stop_watch() -> None:
    """Stop the currently registered push-notification channel."""
    svc = _get_calendar_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Google Calendar not configured")
    try:
        await svc.stop_push_watch()
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post("/calendar/webhook", status_code=200)
async def calendar_webhook(request: Request) -> dict[str, str]:
    """
    Receive Google Calendar push notifications.

    Google POSTs here whenever the user's primary calendar changes.
    The initial ``sync`` message (``X-Goog-Resource-State: sync``) must be
    acknowledged immediately with 200 OK.  Subsequent ``exists`` messages
    indicate at least one event was added / changed / deleted.

    Google requires a response within a few seconds or it considers the
    delivery failed and retries with exponential back-off.
    """
    import logging
    channel_id = request.headers.get("X-Goog-Channel-ID", "")
    resource_state = request.headers.get("X-Goog-Resource-State", "")

    if resource_state == "sync":
        # Initial handshake — acknowledge and return.
        return {"status": "ok"}

    if resource_state == "exists":
        # Calendar changed.  Log so upstream code knows a re-poll is needed.
        # V2 (cloud backend) will trigger an async re-poll here.
        logging.getLogger(__name__).info(
            "calendar/webhook: change notification channel=%s", channel_id
        )

    return {"status": "ok"}


@app.get("/users/me")
async def get_user() -> dict[str, Any]:
    async with get_db_session() as db:
        user = await _get_or_create_user(db)
        return {
            "id": user.id,
            "display_name": user.display_name,
            "core_sessions": user.core_sessions,
            "core_confidence": user.core_confidence,
            "sa_archetype": user.sa_archetype,
        }


@app.put("/users/me")
async def update_user(body: dict[str, Any]) -> dict[str, Any]:
    async with get_db_session() as db:
        user = await _get_or_create_user(db)
        if "display_name" in body:
            name = str(body["display_name"]).strip()
            if name:
                user.display_name = name
        await db.commit()
        return {
            "id": user.id,
            "display_name": user.display_name,
        }


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

class CreateSparringRequest(BaseModel):
    user_archetype: str = Field(description="Communicator Superpower of the user being coached")
    opponent_archetype: str = Field(description="Communicator Superpower the AI opponent plays")
    scenario: str = Field(description="Short description of the meeting context / goal")
    max_turns: int = Field(default=10, ge=2, le=20)


class PreSeedRequest(BaseModel):
    text: str = Field(description="Free text: description, email, bio, or meeting notes")
    name: str | None = Field(default=None, description="Participant name (for display only)")


class PreSeedResponse(BaseModel):
    type: str | None
    confidence: float
    state: str
    reasoning: str
    participant_id: str | None = None


@app.post("/sparring/sessions", status_code=201)
async def create_sparring_session(body: CreateSparringRequest) -> dict[str, str]:
    """Create an in-memory sparring session and return its ID."""
    session = SparringSession(
        user_archetype=body.user_archetype,  # type: ignore[arg-type]
        opponent_archetype=body.opponent_archetype,  # type: ignore[arg-type]
        scenario=body.scenario,
        max_turns=body.max_turns,
    )
    session_id = str(uuid.uuid4())
    _sparring_sessions[session_id] = session
    return {"session_id": session_id}


@app.websocket("/ws/sparring/{session_id}")
async def sparring_ws(ws: WebSocket, session_id: str) -> None:
    """
    WebSocket for one AI sparring session.

    Client → server:
        {"type": "user_turn", "text": "..."}
        {"type": "end"}

    Server → client (streamed):
        {"type": "sparring_turn", "role": "user"|"opponent"|"coaching",
         "text": "...", "turn_number": N, "is_final": bool, "coaching_tip": "..."}
        {"type": "sparring_ended", "turns": N}
    """
    session = _sparring_sessions.get(session_id)
    if session is None:
        await ws.close(code=4004, reason="Sparring session not found")
        return

    await ws.accept()

    # Stream the opponent's opening statement before waiting for user input.
    try:
        async for turn in await session.intro():
            await ws.send_json({
                "type": "sparring_turn",
                "role": turn.role,
                "text": turn.text,
                "turn_number": turn.turn_number,
                "is_final": turn.is_final,
                "coaching_tip": turn.coaching_tip,
            })
    except Exception as exc:
        await ws.send_json({"type": "error", "message": f"Failed to open session: {exc}"})
        _sparring_sessions.pop(session_id, None)
        return

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            if msg.get("type") == "end":
                session.end()
                await ws.send_json({"type": "sparring_ended", "turns": session.turn_count})
                break

            if msg.get("type") == "user_turn":
                user_text = str(msg.get("text", "")).strip()
                if not user_text:
                    continue
                try:
                    gen = await session.send(user_text)
                    async for turn in gen:
                        await ws.send_json({
                            "type": "sparring_turn",
                            "role": turn.role,
                            "text": turn.text,
                            "turn_number": turn.turn_number,
                            "is_final": turn.is_final,
                            "coaching_tip": turn.coaching_tip,
                        })
                except Exception as exc:
                    await ws.send_json({"type": "error", "message": str(exc)})
                    break
                if session.is_ended:
                    await ws.send_json({"type": "sparring_ended", "turns": session.turn_count})
                    break

    except WebSocketDisconnect:
        pass
    finally:
        _sparring_sessions.pop(session_id, None)


class TextCoachRequest(BaseModel):
    text: str = Field(description="Draft text to receive coaching on (LinkedIn post, email, Slack message, etc.)")
    context: str = Field(default="", description="Optional context about the audience or goal")


class TextCoachResponse(BaseModel):
    tips: list[str]
    overall: str


_TEXT_COACH_SYSTEM = """\
You are an expert persuasion coach. The user will give you a draft text they intend \
to share (e.g. a LinkedIn post, email, Slack message). Analyze it through the lens of \
the Communicator Superpower framework (Architect, Firestarter, Inquisitor, Bridge Builder).

Return a JSON object with two fields:
- "tips": an array of 3–5 short, verb-first coaching tips (≤20 words each) on how to \
  make the text more persuasive. Each tip should be a positive action (what TO do).
- "overall": a 1-2 sentence assessment of the text's persuasion strengths and one key \
  area to improve.

Output ONLY valid JSON, no markdown fences or preamble.
"""


@app.post("/coach/text", response_model=TextCoachResponse)
async def coach_text(body: TextCoachRequest) -> TextCoachResponse:
    """Analyze a draft text and return persuasion coaching tips."""
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=422, detail="text must be non-empty")

    user_msg = body.text.strip()
    if body.context.strip():
        user_msg = f"Context: {body.context.strip()}\n\nDraft:\n{user_msg}"
    else:
        user_msg = f"Draft:\n{user_msg}"

    try:
        api_key = _load_settings().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        client = AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=_TEXT_COACH_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        import json as _json
        result = _json.loads(response.content[0].text)
        return TextCoachResponse(
            tips=result.get("tips", []),
            overall=result.get("overall", ""),
        )
    except (_anthropic.AuthenticationError, KeyError):
        raise HTTPException(status_code=503, detail="Anthropic API key is invalid or not configured")
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to generate coaching feedback")


# ── Participant profile endpoints ─────────────────────────────────────────


class ParticipantResponse(BaseModel):
    id: str
    name: str | None
    notes: str | None
    archetype: str | None  # best-known archetype (obs or pre-seed)
    confidence: float | None
    reasoning: str | None
    sessions_observed: int
    focus_score: float | None
    stance_score: float | None
    created_at: str
    updated_at: str


class ParticipantDetailResponse(ParticipantResponse):
    observations: list[dict[str, Any]]  # per-session audit trail


class ParticipantUpdateRequest(BaseModel):
    name: str | None = None
    notes: str | None = None


@app.get("/participants", response_model=list[ParticipantResponse])
async def list_participants() -> list[ParticipantResponse]:
    """List all saved participant profiles for the current user."""
    async with get_db_session() as db:
        rows = await db.execute(
            select(Participant)
            .where(Participant.user_id == _DEFAULT_USER_ID)
            .order_by(Participant.updated_at.desc())
        )
        return [
            ParticipantResponse(
                id=p.id,
                name=p.name,
                notes=p.notes,
                archetype=p.obs_archetype or p.ps_type,
                confidence=p.obs_confidence if p.obs_confidence is not None else p.ps_confidence,
                reasoning=p.ps_reasoning,
                sessions_observed=p.obs_sessions,
                focus_score=p.obs_focus,
                stance_score=p.obs_stance,
                created_at=p.created_at.isoformat(),
                updated_at=p.updated_at.isoformat(),
            )
            for (p,) in rows
        ]


@app.get("/participants/{participant_id}", response_model=ParticipantDetailResponse)
async def get_participant(participant_id: str) -> ParticipantDetailResponse:
    """Get a single participant profile with observation history."""
    async with get_db_session() as db:
        p = await db.get(Participant, participant_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Participant not found")

        obs_rows = await db.execute(
            select(SessionParticipantObservation)
            .where(SessionParticipantObservation.participant_id == participant_id)
            .order_by(SessionParticipantObservation.id.desc())
        )
        observations = [
            {
                "session_id": o.session_id,
                "archetype": o.archetype,
                "focus_score": o.focus_score,
                "stance_score": o.stance_score,
                "confidence": o.confidence,
                "utterance_count": o.utterance_count,
                "context": o.context,
            }
            for (o,) in obs_rows
        ]

        return ParticipantDetailResponse(
            id=p.id,
            name=p.name,
            notes=p.notes,
            archetype=p.obs_archetype or p.ps_type,
            confidence=p.obs_confidence if p.obs_confidence is not None else p.ps_confidence,
            reasoning=p.ps_reasoning,
            sessions_observed=p.obs_sessions,
            focus_score=p.obs_focus,
            stance_score=p.obs_stance,
            created_at=p.created_at.isoformat(),
            updated_at=p.updated_at.isoformat(),
            observations=observations,
        )


@app.put("/participants/{participant_id}", response_model=ParticipantResponse)
async def update_participant(participant_id: str, body: ParticipantUpdateRequest) -> ParticipantResponse:
    """Update a participant's name or notes."""
    async with get_db_session() as db:
        p = await db.get(Participant, participant_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Participant not found")

        if body.name is not None:
            p.name = body.name
        if body.notes is not None:
            p.notes = body.notes
        from datetime import datetime, timezone
        p.updated_at = datetime.now(timezone.utc)
        await db.commit()

        return ParticipantResponse(
            id=p.id,
            name=p.name,
            notes=p.notes,
            archetype=p.obs_archetype or p.ps_type,
            confidence=p.obs_confidence if p.obs_confidence is not None else p.ps_confidence,
            reasoning=p.ps_reasoning,
            sessions_observed=p.obs_sessions,
            focus_score=p.obs_focus,
            stance_score=p.obs_stance,
            created_at=p.created_at.isoformat(),
            updated_at=p.updated_at.isoformat(),
        )


@app.delete("/participants/{participant_id}", status_code=204)
async def delete_participant(participant_id: str) -> None:
    """Delete a participant profile."""
    async with get_db_session() as db:
        p = await db.get(Participant, participant_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Participant not found")
        # Clean up observations and evidence
        await db.execute(
            delete(SessionParticipantObservation).where(
                SessionParticipantObservation.participant_id == participant_id
            )
        )
        await db.execute(
            delete(BehavioralEvidence).where(
                BehavioralEvidence.participant_id == participant_id
            )
        )
        await db.execute(
            delete(ParticipantContextProfile).where(
                ParticipantContextProfile.participant_id == participant_id
            )
        )
        from backend.models import session_participants
        await db.execute(
            session_participants.delete().where(
                session_participants.c.participant_id == participant_id
            )
        )
        await db.delete(p)


@app.get("/participants/{participant_id}/fingerprint")
async def get_participant_fingerprint(participant_id: str) -> dict[str, Any]:
    """Assemble and return a full behavioral fingerprint for a participant."""
    from backend.fingerprint import assemble_fingerprint

    async with get_db_session() as db:
        fp = await assemble_fingerprint(db, participant_id)
        if fp is None:
            raise HTTPException(status_code=404, detail="Participant not found")
        return fp.to_dict()


@app.put("/participants/{participant_id}/assign-name")
async def assign_participant_name(
    participant_id: str, body: ParticipantUpdateRequest,
) -> ParticipantResponse:
    """
    Assign or update a participant's name.

    If the new name matches an existing participant (fuzzy), the caller
    should merge manually. This endpoint just renames.
    """
    async with get_db_session() as db:
        p = await db.get(Participant, participant_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Participant not found")
        if body.name is not None:
            p.name = body.name
        from datetime import datetime, timezone
        p.updated_at = datetime.now(timezone.utc)
        await db.commit()

        return ParticipantResponse(
            id=p.id,
            name=p.name,
            notes=p.notes,
            archetype=p.obs_archetype or p.ps_type,
            confidence=p.obs_confidence if p.obs_confidence is not None else p.ps_confidence,
            reasoning=p.ps_reasoning,
            sessions_observed=p.obs_sessions,
            focus_score=p.obs_focus,
            stance_score=p.obs_stance,
            created_at=p.created_at.isoformat(),
            updated_at=p.updated_at.isoformat(),
        )


@app.post("/participants/pre-seed", response_model=PreSeedResponse)
async def pre_seed_participant(body: PreSeedRequest) -> PreSeedResponse:
    """
    Classify a participant's Communicator Superpower from free text.

    Runs the synchronous Claude Haiku classifier in a thread pool so it
    doesn't block the event loop.
    """
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=422, detail="text must be non-empty")
    try:
        result = await asyncio.to_thread(_preseed_classify, body.text)
    except (KeyError, _anthropic.AuthenticationError):
        raise HTTPException(status_code=503, detail="Anthropic API key is invalid or not configured")

    # Persist classification to a Participant row
    participant_id: str | None = None
    async with get_db_session() as db:
        participant = None
        if body.name:
            row = await db.execute(
                select(Participant).where(
                    Participant.user_id == _DEFAULT_USER_ID,
                    Participant.name == body.name,
                )
            )
            participant = row.scalar_one_or_none()

        if participant is None:
            participant = Participant(
                user_id=_DEFAULT_USER_ID,
                name=body.name,
                notes=body.text,
                ps_type=result.type,
                ps_confidence=result.confidence,
                ps_reasoning=result.reasoning,
                ps_state=result.state,
            )
            db.add(participant)
        else:
            participant.notes = body.text
            participant.ps_type = result.type
            participant.ps_confidence = result.confidence
            participant.ps_reasoning = result.reasoning
            participant.ps_state = result.state

        await db.flush()
        participant_id = participant.id
        await db.commit()

    return PreSeedResponse(
        type=result.type,
        confidence=result.confidence,
        state=result.state,
        reasoning=result.reasoning,
        participant_id=participant_id,
    )


@app.websocket("/ws/session/{session_id}")
async def websocket_session(ws: WebSocket, session_id: str) -> None:
    """
    Real-time coaching WebSocket for one meeting session.

    The session must be created via POST /sessions first. The session_id
    is used to look up the MeetingSession row for persistence.

    Audio pipeline
    ──────────────
    On connect we start:
      AudioPipeReader  →  DeepgramTranscriber  →  on_utterance → _handle_utterance
    Both are stopped when the session ends or the connection closes.

    If AudioPipeReader's silence watchdog fires (Swift binary stopped writing),
    we send {"type": "swift_restart_needed"} so the Electron renderer can ask
    the main process to restart the capture binary.
    """
    # Verify the session exists in the DB
    async with get_db_session() as db:
        row = await db.get(MeetingSession, session_id)
        if row is None:
            await ws.close(code=4004, reason="Session not found")
            return
        user_speaker = row.coaching_context or _DEFAULT_USER_SPEAKER

    await ws.accept()

    # ── Pre-flight: require Deepgram key before touching the audio pipeline ──
    deepgram_key = _load_settings().get("deepgram_api_key") or os.environ.get("DEEPGRAM_API_KEY", "")
    if not deepgram_key:
        await ws.send_json({
            "type": "error",
            "message": "Deepgram API key not configured. Open Settings and add your key.",
        })
        await ws.close()
        return

    # Load archetype context stashed by POST /sessions
    coaching_ctx = _session_coaching_context.pop(session_id, {})
    user_archetype = coaching_ctx.get("user_archetype")
    participants_info = coaching_ctx.get("participants", [])

    # Enrich participants with behavioral fingerprints from past sessions
    if participants_info:
        try:
            from backend.fingerprint import assemble_fingerprint
            from backend.identity import resolve_speaker
            async with get_db_session() as db:
                for pinfo in participants_info:
                    pname = pinfo.get("name", "")
                    if not pname:
                        continue
                    existing = await resolve_speaker(db, _DEFAULT_USER_ID, pname)
                    if existing:
                        fp = await assemble_fingerprint(db, existing.id)
                        if fp:
                            pinfo["fingerprint"] = fp.to_dict()
                            # Update archetype from fingerprint if we have behavioral data
                            if fp.archetype and fp.sessions_observed > 0:
                                pinfo["archetype"] = fp.archetype
        except Exception:
            pass  # Non-critical — proceed without fingerprints

    # Load historical effectiveness data and adaptive cadence for this archetype
    effectiveness_data: dict[tuple[str, str], float] = {}
    cadence_samples: list[float] = []
    if user_archetype:
        try:
            async with get_db_session() as db:
                eff_rows = await db.execute(
                    select(CoachingEffectiveness).where(
                        CoachingEffectiveness.user_archetype == user_archetype,
                    )
                )
                for er in eff_rows.scalars():
                    effectiveness_data[(er.user_archetype, er.counterpart_archetype)] = er.avg_effectiveness
                    # Only use cadence from pairings with enough data to be meaningful
                    if er.total_prompts >= 5:
                        cadence_samples.append(er.suggested_cadence_s)
        except Exception:
            pass  # Non-critical — proceed without effectiveness data

    # Average adapted cadence across all pairings; fall back to the 30s default
    session_cadence_s = sum(cadence_samples) / len(cadence_samples) if cadence_samples else 30.0

    engine = CoachingEngine(
        user_speaker=user_speaker,
        anthropic_client=AsyncAnthropic(),
        user_archetype=user_archetype,
        participants=participants_info,
        effectiveness_data=effectiveness_data or None,
        general_cadence_floor_s=session_cadence_s,
    )

    pipeline = SessionPipeline(
        session_id=session_id,
        user_id=_DEFAULT_USER_ID,
        user_speaker=user_speaker,
        coaching_engine=engine,
    )
    # Stash participant info for session-end profile updates
    pipeline.participants_info = participants_info  # type: ignore[attr-defined]
    pipeline.session_context = coaching_ctx.get("context", "unknown")  # type: ignore[attr-defined]

    # Load user's profile snapshot for personalized coaching
    try:
        async with get_db_session() as db:
            user = await db.get(User, _DEFAULT_USER_ID)
            if user is not None:
                ctx_rows = await db.execute(
                    select(ContextProfile).where(ContextProfile.user_id == user.id)
                )
                ctx_profiles = {cp.context: cp for cp in ctx_rows.scalars()}
                session_context = coaching_ctx.get("context", "unknown")
                pipeline.user_profile = get_profile_snapshot(user, ctx_profiles, session_context)
    except Exception:
        pass  # Non-critical — coaching works without it, just less personalized

    _session_manager.register(pipeline)

    # ── Audio pipeline ────────────────────────────────────────────────────

    async def _on_utterance(
        speaker_id: str, text: str, is_final: bool, start_s: float, end_s: float
    ) -> None:
        await _handle_utterance(
            ws, pipeline,
            {"speaker_id": speaker_id, "text": text,
             "is_final": is_final, "start": start_s, "end": end_s},
        )

    async def _on_silence() -> None:
        """Swift binary has stopped writing — tell the renderer to restart it."""
        try:
            await ws.send_json({"type": "swift_restart_needed"})
        except Exception:
            pass  # WebSocket may already be closing

    transcriber = DeepgramTranscriber(api_key=deepgram_key, on_utterance=_on_utterance)
    transcriber_connected = False

    # Audio level metering — send RMS to frontend every ~250 ms
    _level_accum: list[int] = []         # raw Int16 sample squares
    _level_sample_count: list[int] = [0] # mutable counter in closure
    _LEVEL_INTERVAL_SAMPLES = 16_000 // 4  # 250 ms at 16 kHz

    _deepgram_error_sent = False
    _deepgram_retry_after: float = 0.0  # monotonic time before which we skip reconnect

    async def _on_audio_chunk(data: bytes) -> None:
        """Forward audio to Deepgram, connecting lazily on first chunk."""
        nonlocal transcriber_connected, _deepgram_error_sent, _deepgram_retry_after

        # ── Audio level meter (always runs, even if Deepgram is down) ──────
        import struct as _struct, math as _math
        n_samples = len(data) // 2
        if n_samples > 0:
            try:
                samples = _struct.unpack(f"<{n_samples}h", data[:n_samples * 2])
                _level_accum.extend(s * s for s in samples)
                _level_sample_count[0] += n_samples
                if _level_sample_count[0] >= _LEVEL_INTERVAL_SAMPLES:
                    rms = _math.sqrt(sum(_level_accum) / len(_level_accum)) if _level_accum else 0
                    level = min(rms / 32767.0, 1.0)
                    _level_accum.clear()
                    _level_sample_count[0] = 0
                    try:
                        await ws.send_json({"type": "audio_level", "level": round(level, 4)})
                    except Exception:
                        pass
            except _struct.error:
                pass

        # ── Deepgram streaming ─────────────────────────────────────────────
        if not transcriber_connected or not transcriber.is_connected:
            # Backoff: don't retry Deepgram more than once every 10 seconds
            if time.monotonic() < _deepgram_retry_after:
                return
            if transcriber_connected:
                # Was connected but Deepgram dropped — reconnect
                logger.warning("Deepgram connection lost, reconnecting…")
                try:
                    await transcriber.disconnect()
                except Exception:
                    pass
                transcriber_connected = False
            try:
                await transcriber.connect()
                transcriber_connected = True
                _deepgram_error_sent = False
                _deepgram_retry_after = 0.0
                logger.info("Deepgram connected on first audio chunk")
            except Exception as exc:
                logger.error("Deepgram connect failed: %s", exc)
                _deepgram_retry_after = time.monotonic() + 10.0
                if not _deepgram_error_sent:
                    _deepgram_error_sent = True
                    try:
                        await ws.send_json({
                            "type": "no_audio",
                            "message": f"Transcription failed: {exc}. Check your Deepgram API key in Settings.",
                        })
                    except Exception:
                        pass
                return
        await transcriber.send_audio(data)

    audio_reader = AudioPipeReader(
        on_audio_chunk=_on_audio_chunk,
        on_silence_timeout=_on_silence,
    )

    try:
        await audio_reader.start()
    except Exception as exc:
        await ws.send_json({
            "type": "error",
            "message": f"Audio pipeline failed: {exc}",
        })
        await ws.close()
        _session_manager.remove(session_id)
        return

    async def _check_audio_started() -> None:
        """Send a warning if no audio arrives within the first 5 seconds."""
        await asyncio.sleep(5.0)
        if audio_reader.last_audio_time == 0.0:
            try:
                await ws.send_json({
                    "type": "no_audio",
                    "message": (
                        "No audio detected. To fix: open System Settings → Privacy & Security → "
                        "Screen Recording and grant access to the app, then restart the session."
                    ),
                })
            except Exception:
                pass

    asyncio.ensure_future(_check_audio_started())

    # ── Message loop ──────────────────────────────────────────────────────

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            done = await _handle_message(ws, pipeline, msg)
            if done:
                break

    except WebSocketDisconnect:
        pass
    finally:
        await audio_reader.stop()
        await transcriber.disconnect()
        _session_manager.remove(session_id)


async def _handle_message(
    ws: WebSocket,
    pipeline: SessionPipeline,
    msg: dict[str, Any],
) -> bool:
    """
    Dispatch one incoming WebSocket message.

    Returns True when the connection should be closed (after session_end),
    False for all other message types.
    """
    msg_type = msg.get("type")

    if msg_type == "utterance":
        await _handle_utterance(ws, pipeline, msg)

    elif msg_type == "ping":
        await ws.send_json({"type": "pong"})

    elif msg_type == "session_end":
        await _handle_session_end(ws, pipeline)
        return True

    else:
        await ws.send_json(
            {"type": "error", "message": f"Unknown message type: {msg_type!r}"}
        )

    return False


async def _handle_utterance(
    ws: WebSocket,
    pipeline: SessionPipeline,
    msg: dict[str, Any],
) -> None:
    """Process one utterance and push a coaching prompt if the engine fires one."""
    speaker_id = msg.get("speaker_id", "")
    text = msg.get("text", "")
    is_final = msg.get("is_final", True)
    start = float(msg.get("start", 0.0))
    end = float(msg.get("end", 0.0))

    # Echo transcript to frontend so it can display live text
    try:
        await ws.send_json({
            "type": "utterance",
            "speaker_id": speaker_id,
            "text": text,
            "is_final": is_final,
            "start": start,
            "end": end,
        })
    except Exception:
        pass

    prompt = await pipeline.process_utterance(
        speaker_id=speaker_id,
        text=text,
        is_final=is_final,
        start=start,
        end=end,
    )

    if prompt is None:
        return

    await ws.send_json(
        {
            "type": "coaching_prompt",
            "layer": prompt.layer,
            "text": prompt.text,
            "is_fallback": prompt.is_fallback,
            "triggered_by": prompt.triggered_by,
            "speaker_id": prompt.speaker_id,
        }
    )

    # Persist the prompt to the database
    trigger = "elm" if prompt.triggered_by.startswith("elm:") else "cadence"
    if prompt.is_fallback:
        trigger = "fallback"

    # Resolve counterpart archetype from profiler if this is an audience-layer prompt
    counterpart_arch = None
    if prompt.speaker_id:
        cls = pipeline.profiler.get_classification(prompt.speaker_id)
        if cls and cls.superpower != "Undetermined":
            counterpart_arch = cls.superpower

    async with get_db_session() as db:
        db.add(
            Prompt(
                session_id=pipeline.session_id,
                layer=prompt.layer,
                text=prompt.text,
                trigger=trigger,
                triggered_by=prompt.triggered_by,
                was_shown=True,
                utterance_index=len(pipeline.utterances) - 1,
                counterpart_archetype=counterpart_arch,
                bullet_ids_used=getattr(prompt, "bullet_ids_used", None) or None,
            )
        )


async def _persist_participant_classifications(
    db: AsyncSession,
    pipeline: SessionPipeline,
    context: str,
) -> None:
    """
    Persist profiler classifications and behavioral evidence for each
    participant at session end.

    For each non-user speaker:
      1. Resolve identity (fuzzy name match) or create new participant
      2. Write SessionParticipantObservation audit row
      3. EWMA-update behavioral profile
      4. Collect and store BehavioralEvidence (key utterances, ELM states,
         uptake/resistance, question types, convergence direction)
    """
    import json as _json
    import re
    from backend.identity import resolve_speaker
    from backend.signals import (
        _tokenize_text_for_phrases,
        _UPTAKE_PHRASES,
        _RESISTANCE_PHRASES,
        _classify_question,
    )

    all_cls = pipeline.profiler.all_classifications()
    participants_info: list[dict] = getattr(pipeline, "participants_info", [])

    if not all_cls:
        return

    # Pre-filter utterances by speaker for evidence collection
    utts_by_speaker: dict[str, list[dict]] = {}
    user_utts: list[dict] = []
    for u in pipeline.utterances:
        sid = u["speaker"]
        if sid == pipeline.user_speaker:
            user_utts.append(u)
        else:
            utts_by_speaker.setdefault(sid, []).append(u)

    for speaker_id, classification in all_cls.items():
        if speaker_id == pipeline.user_speaker:
            continue
        if classification.superpower == "Undetermined":
            continue

        # Determine display name
        name = ""
        if participants_info:
            try:
                idx = int(speaker_id.replace("speaker_", "")) - 1
            except ValueError:
                idx = -1
            if 0 <= idx < len(participants_info):
                name = participants_info[idx].get("name", "")

        if not name:
            name = speaker_id

        # Resolve identity — fuzzy match against existing profiles
        participant = await resolve_speaker(db, pipeline.user_id, name)
        if participant is None:
            participant = Participant(
                user_id=pipeline.user_id, name=name,
                ps_type=classification.superpower,
                ps_confidence=0.5,
                ps_state="active",
            )
            db.add(participant)
            await db.flush()

        # Audit trail
        db.add(SessionParticipantObservation(
            session_id=pipeline.session_id,
            participant_id=participant.id,
            focus_score=classification.focus_score,
            stance_score=classification.stance_score,
            confidence=classification.confidence,
            archetype=classification.superpower,
            utterance_count=classification.utterance_count,
            context=context,
        ))

        # EWMA-update behavioral profile
        ctx_result = await db.execute(
            select(ParticipantContextProfile).where(
                ParticipantContextProfile.participant_id == participant.id
            )
        )
        ctx_profiles = {cp.context: cp for cp in ctx_result.scalars()}

        ctx_key = context if context in VALID_CONTEXTS else "unknown"
        if ctx_key not in ctx_profiles:
            new_cp = ParticipantContextProfile(
                participant_id=participant.id, context=ctx_key
            )
            db.add(new_cp)
            await db.flush()
            ctx_profiles[ctx_key] = new_cp

        apply_participant_observation(
            participant, ctx_profiles,
            focus_score=classification.focus_score,
            stance_score=classification.stance_score,
            confidence=classification.confidence,
            context=ctx_key,
        )

        # ── Behavioral evidence collection ───────────────────────────
        key_evidence = pipeline.profiler.get_key_evidence(speaker_id, top_n=3)
        elm_episodes = pipeline.elm_detector.get_episode_history(speaker_id)

        # Per-speaker uptake/resistance counts
        speaker_utts = utts_by_speaker.get(speaker_id, [])
        uptake_n = 0
        resistance_n = 0
        q_types: dict[str, int] = {"challenging": 0, "clarifying": 0, "confirmatory": 0}
        for u in speaker_utts:
            tok = _tokenize_text_for_phrases(u["text"])
            if any(tok.startswith(p) or (", " + p) in tok or (". " + p) in tok for p in _UPTAKE_PHRASES):
                uptake_n += 1
            if any(tok.startswith(p) or (", " + p) in tok or (". " + p) in tok for p in _RESISTANCE_PHRASES):
                resistance_n += 1
            qtype = _classify_question(u["text"])
            if qtype in q_types:
                q_types[qtype] += 1

        # Simple convergence direction: compare pronoun shift
        # (we/our ratio in first half vs second half of this speaker's utterances)
        convergence_dir = 0.0
        if len(speaker_utts) >= 4:
            mid = len(speaker_utts) // 2
            we_re = re.compile(r"\b(we|our|us|ours|ourselves|together)\b", re.IGNORECASE)
            iy_re = re.compile(r"\b(I|my|me|mine|you|your|yours)\b", re.IGNORECASE)
            def _we_ratio(utts: list[dict]) -> float:
                text = " ".join(u["text"] for u in utts)
                we_c = len(we_re.findall(text))
                iy_c = len(iy_re.findall(text))
                return we_c / (we_c + iy_c + 0.01)
            first_ratio = _we_ratio(speaker_utts[:mid])
            second_ratio = _we_ratio(speaker_utts[mid:])
            convergence_dir = round(second_ratio - first_ratio, 3)

        db.add(BehavioralEvidence(
            session_id=pipeline.session_id,
            participant_id=participant.id,
            key_utterances=_json.dumps(key_evidence),
            elm_states=_json.dumps(list(set(elm_episodes))),
            uptake_count=uptake_n,
            resistance_count=resistance_n,
            question_types=_json.dumps(q_types),
            convergence_direction=convergence_dir,
            pronoun_shift=convergence_dir,  # same signal for now
            context=ctx_key,
        ))

        logger.info(
            "Participant %s updated: archetype=%s confidence=%.3f sessions=%d evidence=[%d utts, %d uptake, %d resist, elm=%s]",
            name, participant.obs_archetype,
            participant.obs_confidence, participant.obs_sessions,
            len(key_evidence), uptake_n, resistance_n, elm_episodes,
        )


async def _handle_session_end(
    ws: WebSocket,
    pipeline: SessionPipeline,
) -> None:
    """Compute scores, persist to DB, update profiles, notify client, and close."""
    has_utterances = len(pipeline.utterances) > 0
    scores = pipeline.compute_scores()

    growth = None
    async with get_db_session() as db:
        row = await db.get(MeetingSession, pipeline.session_id)
        if row is not None:
            if has_utterances:
                # Real session — persist scores
                row.persuasion_score = scores["persuasion_score"]
                row.ended_at = datetime.now(timezone.utc)
                prior = await _prior_scores(db, pipeline.user_id)
                if prior:
                    g = compute_growth_score(scores["persuasion_score"], prior)
                    if g is not None:
                        growth = g.delta

                # ── Profile update: Layer 1 + Layer 2 ──────────────────
                obs = pipeline.observer.get_observation(
                    session_id=pipeline.session_id,
                    context=row.context or "unknown",
                )
                # Write raw observation to session row (Layer 3)
                row.obs_focus = obs.focus_score
                row.obs_stance = obs.stance_score
                row.obs_utterance_count = obs.utterance_count
                row.obs_confidence = obs.obs_confidence

                if obs.utterance_count > 0:
                    user = await db.get(User, pipeline.user_id)
                    if user is not None:
                        ctx_result = await db.execute(
                            select(ContextProfile).where(
                                ContextProfile.user_id == pipeline.user_id
                            )
                        )
                        ctx_profiles = {
                            cp.context: cp for cp in ctx_result.scalars()
                        }

                        # Create ContextProfile if missing for this context
                        ctx_key = obs.context if obs.context in VALID_CONTEXTS else "unknown"
                        if ctx_key not in ctx_profiles:
                            new_cp = ContextProfile(
                                user_id=pipeline.user_id, context=ctx_key
                            )
                            db.add(new_cp)
                            await db.flush()
                            ctx_profiles[ctx_key] = new_cp

                        apply_session_observation(user, ctx_profiles, obs)
                        logger.info(
                            "Profile updated: core_focus=%.1f core_stance=%.1f "
                            "confidence=%.3f sessions=%d",
                            user.core_focus, user.core_stance,
                            user.core_confidence, user.core_sessions,
                        )

                # ── Prompt effectiveness scoring ───────────────────────
                prompt_rows = await db.execute(
                    select(Prompt).where(
                        Prompt.session_id == pipeline.session_id
                    )
                )
                for p in prompt_rows.scalars():
                    if p.utterance_index is not None:
                        eff, before, after = compute_prompt_effectiveness(
                            pipeline.utterances,
                            pipeline.user_speaker,
                            p.utterance_index,
                        )
                        p.effectiveness_score = eff
                        p.convergence_before = before
                        p.convergence_after = after
                        # ACE feedback loop: update bullet counters
                        if p.bullet_ids_used and eff is not None:
                            from backend.coaching_bullets import update_bullet_feedback
                            await update_bullet_feedback(db, p.bullet_ids_used, eff)

                # ── Persist participant classifications ────────────────
                await _persist_participant_classifications(
                    db, pipeline, row.context or "unknown"
                )

                # ── Aggregate coaching effectiveness ───────────────────
                user_arch = pipeline.engine._user_archetype
                session_ctx = row.context or "unknown"
                re_query = await db.execute(
                    select(Prompt).where(
                        Prompt.session_id == pipeline.session_id,
                        Prompt.effectiveness_score.is_not(None),
                        Prompt.counterpart_archetype.is_not(None),
                    )
                )
                for ep in re_query.scalars():
                    eff_row = (await db.execute(
                        select(CoachingEffectiveness).where(
                            CoachingEffectiveness.user_archetype == user_arch,
                            CoachingEffectiveness.counterpart_archetype == ep.counterpart_archetype,
                            CoachingEffectiveness.context == session_ctx,
                        )
                    )).scalar_one_or_none()
                    if eff_row is None:
                        eff_row = CoachingEffectiveness(
                            user_archetype=user_arch,
                            counterpart_archetype=ep.counterpart_archetype,
                            context=session_ctx,
                        )
                        db.add(eff_row)
                        await db.flush()

                    new_avg, new_total, new_eff, new_cad = update_coaching_effectiveness(
                        eff_row.avg_effectiveness,
                        eff_row.total_prompts,
                        eff_row.effective_prompts,
                        eff_row.suggested_cadence_s,
                        ep.effectiveness_score,
                    )
                    eff_row.avg_effectiveness = new_avg
                    eff_row.total_prompts = new_total
                    eff_row.effective_prompts = new_eff
                    eff_row.suggested_cadence_s = new_cad

                # ── Skill badge computation ────────────────────────────
                # Award a badge for any prompt type absent from the last 3 sessions
                recent_session_ids_q = await db.execute(
                    select(MeetingSession.id)
                    .where(MeetingSession.user_id == _DEFAULT_USER_ID)
                    .order_by(MeetingSession.started_at.desc())
                    .limit(3)
                )
                recent_session_ids = list(recent_session_ids_q.scalars())
                if len(recent_session_ids) >= 3:
                    recent_triggers: list[list[str]] = []
                    for sid in reversed(recent_session_ids):  # oldest → newest
                        triggers_q = await db.execute(
                            select(Prompt.triggered_by).where(
                                Prompt.session_id == sid,
                                Prompt.trigger != "fallback",
                                Prompt.triggered_by.is_not(None),
                            )
                        )
                        recent_triggers.append([t for t in triggers_q.scalars()])

                    for bt in compute_skill_badges(recent_triggers):
                        already_awarded = (await db.execute(
                            select(SkillBadge).where(
                                SkillBadge.user_id == _DEFAULT_USER_ID,
                                SkillBadge.trigger_type == bt,
                            )
                        )).scalar_one_or_none()
                        if already_awarded is None:
                            b_name, b_tagline = BADGE_METADATA[bt]
                            db.add(SkillBadge(
                                user_id=_DEFAULT_USER_ID,
                                trigger_type=bt,
                                badge_name=b_name,
                                tagline=b_tagline,
                            ))
                # ── Persist utterances for transcript retrieval ────────
                for seq, utt in enumerate(pipeline.utterances):
                    db.add(Utterance(
                        session_id=pipeline.session_id,
                        sequence=seq,
                        speaker_id=utt["speaker"],
                        text=utt["text"],
                        start_s=float(utt.get("start", 0.0)),
                        end_s=float(utt.get("end", 0.0)),
                        is_user=(utt["speaker"] == pipeline.user_speaker),
                    ))
            else:
                # Empty session (no speech detected) — delete the row
                await db.delete(row)

    await ws.send_json(
        {
            "type": "session_ended",
            "session_id": pipeline.session_id,
            "persuasion_score": scores["persuasion_score"] if has_utterances else None,
            "growth_delta": growth,
            "breakdown": {
                "timing": scores["timing_score"],
                "ego_safety": scores["ego_safety_score"],
                "convergence": scores["convergence_score"],
            },
        }
    )

    await ws.close()

    # ── Post-session background tasks (do not block WebSocket close) ──
    if has_utterances:
        asyncio.create_task(
            _generate_session_debrief(pipeline.session_id, pipeline.utterances, scores)
        )
        asyncio.create_task(
            _update_coaching_playbook(pipeline, scores)
        )


async def _generate_session_debrief(
    session_id: str,
    utterances: list[dict],
    scores: dict,
) -> None:
    """
    Generate a post-session coaching debrief with Claude Opus in the background.

    Writes the result to MeetingSession.debrief_text.  Silently no-ops if the
    API key is missing or the call fails — the debrief is non-critical.
    """
    api_key = _load_settings().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return

    sample = utterances[-60:]
    transcript_lines = "\n".join(
        f"{'[YOU]' if u['speaker'] == 'speaker_0' else u['speaker']}: {u['text'][:200]}"
        for u in sample
    )
    score_summary = (
        f"Persuasion Score: {scores['persuasion_score']}/100 "
        f"(Timing {scores['timing_score']}/30, "
        f"Ego Safety {scores['ego_safety_score']}/30, "
        f"Convergence {scores['convergence_score']}/40)"
    )

    speakers = set(u["speaker"] for u in utterances)
    non_user = speakers - {"speaker_0"}

    prompt = (
        "You are a $500/hr executive communication coach. You use the "
        "Communicator Superpower framework (Architect=Logic+Analyze, "
        "Firestarter=Narrative+Advocate, Inquisitor=Logic+Advocate, "
        "Bridge Builder=Narrative+Analyze).\n\n"
        f"SCORES\n{score_summary}\n"
        f"SPEAKERS: {len(speakers)} total ({len(non_user)} counterparts)\n\n"
        f"TRANSCRIPT ({len(sample)} turns)\n{transcript_lines}\n\n"
        "Write a concise coaching debrief covering:\n"
        "1. TEAM DYNAMICS — archetype distribution, complementary pairings, gaps\n"
        "2. KEY MOMENTS — where persuasion succeeded or failed, and why\n"
        "3. YOUR PERFORMANCE — what you did well, what you missed\n"
        "4. COACHING PRESCRIPTION — one concrete behavioral change for next time\n\n"
        "Be specific — cite moments. Use second person. No filler."
    )

    try:
        client = _anthropic.AsyncAnthropic(api_key=api_key)
        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=60.0,
        )
        debrief = response.content[0].text.strip()
        async with get_db_session() as db:
            row = await db.get(MeetingSession, session_id)
            if row is not None:
                row.debrief_text = debrief
    except Exception as exc:
        logger.warning("Opus debrief failed for session %s: %s", session_id, exc)


async def _update_coaching_playbook(
    pipeline: SessionPipeline,
    scores: dict,
) -> None:
    """
    Collect prompt effectiveness results and update the coaching bullet store.

    Uses the ACE (Agentic Context Engineering) pipeline:
      Reflector (Opus) → Curator (deterministic merge) → Bullet Store (SQLite)

    Runs in the background after session end — non-critical, failures are logged.
    """
    try:
        from backend.coaching_bullets import update_coaching_bullets

        # Collect prompt effectiveness data from the DB
        prompt_results: list[dict] = []
        async with get_db_session() as db:
            prompt_rows = await db.execute(
                select(Prompt).where(
                    Prompt.session_id == pipeline.session_id,
                    Prompt.effectiveness_score.is_not(None),
                )
            )
            for p in prompt_rows.scalars():
                prompt_results.append({
                    "triggered_by": p.triggered_by or "unknown",
                    "counterpart_archetype": p.counterpart_archetype or "Unknown",
                    "text": p.text or "",
                    "effectiveness_score": p.effectiveness_score,
                    "convergence_before": p.convergence_before,
                    "convergence_after": p.convergence_after,
                })

            # Get meeting context from the session row
            row = await db.get(MeetingSession, pipeline.session_id)
            context = row.context if row else "unknown"

        # Compute talk time ratio
        user_utts = sum(1 for u in pipeline.utterances if u["speaker"] == pipeline.user_speaker)
        total_utts = len(pipeline.utterances)
        talk_ratio = user_utts / total_utts if total_utts > 0 else 0.0

        session_summary = {
            "persuasion_score": scores.get("persuasion_score"),
            "timing_score": scores.get("timing_score"),
            "ego_safety_score": scores.get("ego_safety_score"),
            "convergence_score": scores.get("convergence_score"),
            "ego_threat_events": scores.get("ego_threat_events", 0),
            "talk_time_ratio": round(talk_ratio, 2),
            "total_utterances": total_utts,
            "context": context,
            "prompt_results": prompt_results,
        }

        api_key = _load_settings().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

        # ACE pipeline: Reflector → Curator → Bullet Store
        async with get_db_session() as db:
            await update_coaching_bullets(
                db=db,
                user_id=pipeline.user_id,
                user_archetype=pipeline.engine._user_archetype,
                session_id=pipeline.session_id,
                session_summary=session_summary,
                api_key=api_key,
            )
    except Exception as exc:
        logger.warning(
            "Coaching bullet update failed for session %s: %s",
            pipeline.session_id, exc,
        )


# ---------------------------------------------------------------------------
# Skill Badges endpoint
# ---------------------------------------------------------------------------

@app.get("/skill-badges")
async def get_skill_badges() -> list[dict]:
    """Return all skill badges awarded to the local user, newest first."""
    async with get_db_session() as db:
        result = await db.execute(
            select(SkillBadge)
            .where(SkillBadge.user_id == _DEFAULT_USER_ID)
            .order_by(SkillBadge.awarded_at.desc())
        )
        return [
            {
                "id": b.id,
                "trigger_type": b.trigger_type,
                "badge_name": b.badge_name,
                "tagline": b.tagline,
                "awarded_at": b.awarded_at.isoformat(),
                "consecutive_sessions": b.consecutive_sessions,
            }
            for b in result.scalars()
        ]


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------

_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "..", ".settings.json")


def _load_settings() -> dict[str, str]:
    try:
        with open(_SETTINGS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict[str, str]) -> None:
    with open(_SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


class SettingsRequest(BaseModel):
    anthropic_api_key: str | None = None
    deepgram_api_key: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None


@app.get("/settings")
async def get_settings() -> dict[str, Any]:
    data = _load_settings()
    # Mask keys for safety — only return whether they are set
    return {
        "anthropic_api_key_set": bool(data.get("anthropic_api_key")),
        "deepgram_api_key_set": bool(data.get("deepgram_api_key")),
        "google_client_id_set": bool(data.get("google_client_id")),
        "google_client_secret_set": bool(data.get("google_client_secret")),
    }


@app.post("/settings", status_code=204)
async def save_settings(body: SettingsRequest) -> None:
    data = _load_settings()
    if body.anthropic_api_key is not None:
        data["anthropic_api_key"] = body.anthropic_api_key
    if body.deepgram_api_key is not None:
        data["deepgram_api_key"] = body.deepgram_api_key
    if body.google_client_id is not None:
        data["google_client_id"] = body.google_client_id
    if body.google_client_secret is not None:
        data["google_client_secret"] = body.google_client_secret
    _save_settings(data)


# ---------------------------------------------------------------------------
# Self-assessment endpoints
# ---------------------------------------------------------------------------

class AssessmentItemSchema(BaseModel):
    id: str
    axis: str
    reverse: bool
    text: str


class AssessmentResponseSchema(BaseModel):
    item_id: str
    response: int  # 1–7
    response_time_ms: float | None = None


class SubmitAssessmentRequest(BaseModel):
    responses: list[AssessmentResponseSchema]
    micro_argument: str | None = None


class AssessmentResultResponse(BaseModel):
    archetype: str | None
    focus_score: float
    stance_score: float
    confidence: float
    reasoning: str


@app.get("/self-assessment/items", response_model=list[AssessmentItemSchema])
async def get_assessment_items() -> list[AssessmentItemSchema]:
    return [
        AssessmentItemSchema(id=item.id, axis=item.axis, reverse=item.reverse, text=item.text)
        for item in _ASSESSMENT_ITEMS
    ]


@app.post("/self-assessment/submit", response_model=AssessmentResultResponse)
async def submit_assessment(body: SubmitAssessmentRequest) -> AssessmentResultResponse:
    responses = [
        AssessmentResponse(
            item_id=r.item_id,
            raw_score=r.response,
            response_time_ms=int(r.response_time_ms or 0),
        )
        for r in body.responses
    ]
    axes = _sa_score(responses)

    micro = None
    if body.micro_argument and body.micro_argument.strip():
        api_key = _load_settings().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            client = _anthropic.Anthropic(api_key=api_key)
            try:
                micro = await asyncio.to_thread(
                    _sa_classify_micro, body.micro_argument, client=client,
                )
            except _anthropic.AuthenticationError:
                raise HTTPException(status_code=503, detail="Anthropic API key is invalid or not configured")

    result = _sa_build_result(axes, micro_argument=micro)
    return AssessmentResultResponse(
        archetype=result.archetype,
        focus_score=result.focus_score,
        stance_score=result.stance_score,
        confidence=result.confidence,
        reasoning=result.note,
    )


# ---------------------------------------------------------------------------
# Calendar OAuth + meetings endpoints
# ---------------------------------------------------------------------------

_CALENDAR_REDIRECT_URI = "http://127.0.0.1:8000/calendar/callback"

# Temporary store for pending OAuth state (single-user desktop app)
_calendar_oauth_pending: dict[str, Any] = {}


@app.get("/calendar/auth-url")
async def get_calendar_auth_url() -> dict[str, str]:
    svc = _get_calendar_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Google Calendar not configured — add Google Client ID and Secret in Settings")
    url = svc.get_auth_url(redirect_uri=_CALENDAR_REDIRECT_URI)
    return {"url": url}


@app.get("/calendar/callback")
async def calendar_oauth_callback(code: str | None = None, error: str | None = None) -> Any:
    """Loopback redirect endpoint — Google redirects here after consent."""
    from fastapi.responses import HTMLResponse
    if error:
        return HTMLResponse(
            f"<html><body style='font-family:system-ui;padding:40px;text-align:center'>"
            f"<h2>Authorization failed</h2><p>{error}</p>"
            f"<p>You can close this tab.</p></body></html>",
            status_code=400,
        )
    if not code:
        return HTMLResponse(
            "<html><body style='font-family:system-ui;padding:40px;text-align:center'>"
            "<h2>Missing authorization code</h2>"
            "<p>You can close this tab.</p></body></html>",
            status_code=400,
        )
    svc = _get_calendar_service()
    if svc is None:
        return HTMLResponse(
            "<html><body style='font-family:system-ui;padding:40px;text-align:center'>"
            "<h2>Calendar not configured</h2></body></html>",
            status_code=503,
        )
    try:
        await svc.exchange_code(code, _CALENDAR_REDIRECT_URI)
    except Exception as exc:
        return HTMLResponse(
            f"<html><body style='font-family:system-ui;padding:40px;text-align:center'>"
            f"<h2>Connection failed</h2><p>{exc}</p>"
            f"<p>You can close this tab.</p></body></html>",
            status_code=400,
        )
    return HTMLResponse(
        "<html><body style='font-family:system-ui;padding:40px;text-align:center'>"
        "<h2 style='color:#5A9E6F'>Connected!</h2>"
        "<p>Google Calendar is now linked to Persuasion Dojo.</p>"
        "<p style='color:#888'>You can close this tab and return to the app.</p>"
        "</body></html>"
    )


@app.get("/calendar/status")
async def get_calendar_status() -> dict[str, Any]:
    """Check if Google Calendar is configured and connected."""
    svc = _get_calendar_service()
    if svc is None:
        return {"configured": False, "connected": False}
    return {"configured": True, "connected": svc.is_authenticated}


@app.post("/calendar/disconnect", status_code=204)
async def disconnect_calendar() -> None:
    """Remove stored Google Calendar tokens."""
    from pathlib import Path
    token_path = Path.home() / ".persuasion_dojo_token.json"
    token_path.unlink(missing_ok=True)


@app.get("/calendar/meetings")
async def get_calendar_meetings(hours_ahead: int = 24) -> list[dict[str, Any]]:
    svc = _get_calendar_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Google Calendar not configured — add Google Client ID and Secret in Settings")
    if not svc.is_authenticated:
        raise HTTPException(status_code=400, detail="Not connected — authorize Google Calendar first")
    try:
        meetings = await svc.get_upcoming_meetings(hours_ahead)
        return [
            {
                "id": m.id,
                "title": m.title,
                "start": m.start_dt.isoformat(),
                "attendees": m.attendee_emails,
            }
            for m in meetings
        ]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Team sync endpoints
# ---------------------------------------------------------------------------

class ExportRequest(BaseModel):
    passphrase: str


class ImportRequest(BaseModel):
    bundle: str
    passphrase: str


@app.post("/team/export")
async def team_export(body: ExportRequest) -> dict[str, str]:
    async with get_db_session() as db:
        from backend.models import Participant
        rows = await db.execute(select(Participant))
        participants = rows.scalars().all()

    records = [
        ParticipantRecord(
            id=p.id,
            name=p.name,
            notes=p.notes,
            ps_type=p.ps_type,
            ps_confidence=p.ps_confidence,
            ps_reasoning=p.ps_reasoning,
            ps_state=p.ps_state,
        )
        for p in participants
    ]
    try:
        bundle = await asyncio.to_thread(_TeamSync.export_participants, records, body.passphrase)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"bundle": bundle}


@app.post("/team/import", status_code=204)
async def team_import(body: ImportRequest) -> None:
    try:
        records = await asyncio.to_thread(_TeamSync.import_participants, body.bundle, body.passphrase)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Import failed: {exc}") from exc

    async with get_db_session() as db:
        from backend.models import Participant
        for r in records:
            existing = await db.execute(
                select(Participant).where(Participant.name == r.name)
            )
            if existing.scalar_one_or_none() is None:
                db.add(Participant(
                    user_id=_DEFAULT_USER_ID,
                    name=r.name,
                    notes=r.notes,
                    ps_type=r.ps_type,
                    ps_confidence=r.ps_confidence,
                    ps_reasoning=r.ps_reasoning,
                    ps_state=r.ps_state,
                ))


# ---------------------------------------------------------------------------
# Retro import endpoint
# ---------------------------------------------------------------------------

from fastapi import UploadFile, File, BackgroundTasks


_active_retro_jobs: dict[str, dict[str, Any]] = {}


@app.post("/retro/upload")
async def retro_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """
    Accept an audio file (WAV/MP3/M4A) or text transcript (.txt/.json) and
    kick off retroactive analysis in the background.

    Text transcripts are parsed locally — no Deepgram key required.
    Audio files require a Deepgram API key in Settings.
    """
    from backend.retro_import import RetroImporter, is_text_transcript, parse_text_transcript

    job_id = str(uuid.uuid4())
    _active_retro_jobs[job_id] = {"status": "pending", "progress": 0, "total": 0}

    file_bytes = await file.read()
    filename = file.filename or "upload.wav"

    async def _run() -> None:
        job = _active_retro_jobs[job_id]
        job["status"] = "processing"
        utterances: list[dict[str, Any]] = []

        # Create a MeetingSession up front so we have a session_id
        async with get_db_session() as db:
            session = MeetingSession(user_id=_DEFAULT_USER_ID, context="retro", title=filename)
            db.add(session)
            await db.flush()
            session_id = session.id
        job["session_id"] = session_id

        async def on_utterance(speaker_id: str, text: str, is_final: bool, start_s: float, end_s: float) -> None:
            utterances.append({"speaker_id": speaker_id, "text": text, "start": start_s, "end": end_s})

        async def on_progress(delivered: int, total: int) -> None:
            job["progress"] = delivered
            job["total"] = total

        try:
            importer = RetroImporter(
                api_key=_load_settings().get("deepgram_api_key") or os.environ.get("DEEPGRAM_API_KEY", ""),
                on_utterance=on_utterance,
                on_progress=on_progress,
            )

            if is_text_transcript(filename):
                # Parse locally — no Deepgram needed
                parsed = parse_text_transcript(file_bytes.decode("utf-8", errors="replace"))
                if not parsed:
                    raise ValueError("Could not parse transcript — check the file format.")
                await importer.process_utterances(parsed)
            else:
                # Audio file — send to Deepgram
                suffix = os.path.splitext(filename)[1] or ".wav"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(file_bytes)
                    tmp_path = tmp.name
                try:
                    await importer.process_file(tmp_path)
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

            # Run profiler on ALL speakers to classify participants.
            # In retro analysis we don't reliably know which speaker is the
            # user, so profile everyone and let the user curate later.
            profiler = ParticipantProfiler()
            speakers_seen: set[str] = set()
            for utt in utterances:
                speakers_seen.add(utt["speaker_id"])
                profiler.add_utterance(utt["speaker_id"], utt["text"])
            logger.info("[retro] speakers seen: %s (%d utterances)", speakers_seen, len(utterances))

            participant_profiles: dict[str, dict] = {}
            for sid, cls in profiler.all_classifications().items():
                logger.info("[retro] profiler: %s → %s (conf=%.2f)", sid, cls.superpower, cls.confidence)
                # Include all speakers — even "Undetermined" — so profiles are created
                participant_profiles[sid] = {
                    "speaker_id": sid,
                    "archetype": cls.superpower if cls.superpower != "Undetermined" else "Unknown",
                    "confidence": round(cls.confidence, 2),
                }

            # Persist utterances, profiles, and compute score
            scores: dict[str, Any] = {}
            async with get_db_session() as db:
                row = await db.get(MeetingSession, session_id)
                if row is not None:
                    row.ended_at = datetime.now(timezone.utc)
                    # Persist utterances
                    for seq, utt in enumerate(utterances):
                        db.add(Utterance(
                            session_id=session_id,
                            sequence=seq,
                            speaker_id=utt["speaker_id"],
                            text=utt["text"],
                            start_s=float(utt.get("start", 0.0)),
                            end_s=float(utt.get("end", 0.0)),
                            is_user=(utt["speaker_id"] == _DEFAULT_USER_SPEAKER),
                        ))
                    # Compute persuasion score
                    score_utterances = [
                        {"speaker": u["speaker_id"], "text": u["text"],
                         "start": u.get("start", 0.0), "end": u.get("end", 0.0)}
                        for u in utterances
                    ]
                    if score_utterances:
                        result = compute_persuasion_score(score_utterances, _DEFAULT_USER_SPEAKER)
                        row.persuasion_score = result.score
                        scores = {
                            "persuasion_score": result.score,
                            "timing_score": round(result.timing.score * 30),
                            "ego_safety_score": round(result.ego_safety.score * 30),
                            "convergence_score": round(result.convergence.score * 40),
                        }

                    # Persist participant profiles with identity resolution + behavioral evidence
                    import json as _retro_json
                    import re as _re
                    from backend.identity import resolve_speaker
                    from backend.signals import (
                        _tokenize_text_for_phrases as _tok,
                        _UPTAKE_PHRASES as _UP,
                        _RESISTANCE_PHRASES as _RES,
                        _classify_question as _cq,
                    )
                    from backend.models import session_participants as _sp

                    # Pre-filter utterances by speaker
                    _utts_by_spk: dict[str, list[dict]] = {}
                    for _u in utterances:
                        _utts_by_spk.setdefault(_u["speaker_id"], []).append(_u)

                    logger.info("[retro] persisting %d participant profiles", len(participant_profiles))
                    for sid, profile in participant_profiles.items():
                        display_name = sid
                        if _re.match(r"^speaker_\d+$", sid):
                            display_name = sid.replace("_", " ").title()

                        # Identity resolution
                        p = await resolve_speaker(db, _DEFAULT_USER_ID, display_name)
                        if p is None:
                            p = Participant(
                                user_id=_DEFAULT_USER_ID,
                                name=display_name,
                                ps_type=profile["archetype"],
                                ps_confidence=profile["confidence"],
                                ps_state="active",
                            )
                            db.add(p)
                            await db.flush()
                            logger.info("[retro] created participant: %s → %s (id=%s)", display_name, profile["archetype"], p.id)
                        else:
                            # Update top-level profile if retro analysis has higher confidence
                            if profile["confidence"] > (p.ps_confidence or 0):
                                p.ps_type = profile["archetype"]
                                p.ps_confidence = profile["confidence"]
                            logger.info("[retro] resolved existing participant: %s (id=%s)", display_name, p.id)

                        cls = profiler.all_classifications().get(sid)
                        if cls:
                            # EWMA update
                            _ctx_res = await db.execute(
                                select(ParticipantContextProfile).where(
                                    ParticipantContextProfile.participant_id == p.id
                                )
                            )
                            _ctx_map = {cp.context: cp for cp in _ctx_res.scalars()}
                            if "retro" not in _ctx_map:
                                _new_cp = ParticipantContextProfile(
                                    participant_id=p.id, context="retro"
                                )
                                db.add(_new_cp)
                                await db.flush()
                                _ctx_map["retro"] = _new_cp

                            apply_participant_observation(
                                p, _ctx_map,
                                focus_score=cls.focus_score,
                                stance_score=cls.stance_score,
                                confidence=cls.confidence,
                                context="retro",
                            )

                            # Audit trail
                            db.add(SessionParticipantObservation(
                                session_id=session_id,
                                participant_id=p.id,
                                focus_score=cls.focus_score,
                                stance_score=cls.stance_score,
                                confidence=cls.confidence,
                                archetype=cls.superpower,
                                utterance_count=cls.utterance_count,
                                context="retro",
                            ))

                            # ── Behavioral evidence ──────────────────
                            key_ev = profiler.get_key_evidence(sid, top_n=3)

                            # Run ELM detection on this speaker's utterances
                            _elm = ELMDetector(user_speaker=_DEFAULT_USER_SPEAKER)
                            for _eu in utterances:
                                _elm.process_utterance(_eu["speaker_id"], _eu["text"])
                            elm_eps = _elm.get_episode_history(sid)

                            spk_utts = _utts_by_spk.get(sid, [])
                            _up_n, _res_n = 0, 0
                            _qt: dict[str, int] = {"challenging": 0, "clarifying": 0, "confirmatory": 0}
                            for _su in spk_utts:
                                _t = _tok(_su["text"])
                                if any(_t.startswith(_p) or (", " + _p) in _t or (". " + _p) in _t for _p in _UP):
                                    _up_n += 1
                                if any(_t.startswith(_p) or (", " + _p) in _t or (". " + _p) in _t for _p in _RES):
                                    _res_n += 1
                                _qr = _cq(_su["text"])
                                if _qr in _qt:
                                    _qt[_qr] += 1

                            # Pronoun convergence direction
                            _conv_dir = 0.0
                            if len(spk_utts) >= 4:
                                _mid = len(spk_utts) // 2
                                _we_r = _re.compile(r"\b(we|our|us|ours|ourselves|together)\b", _re.IGNORECASE)
                                _iy_r = _re.compile(r"\b(I|my|me|mine|you|your|yours)\b", _re.IGNORECASE)
                                def _wr(us):
                                    t = " ".join(x["text"] for x in us)
                                    wc = len(_we_r.findall(t))
                                    ic = len(_iy_r.findall(t))
                                    return wc / (wc + ic + 0.01)
                                _conv_dir = round(_wr(spk_utts[_mid:]) - _wr(spk_utts[:_mid]), 3)

                            db.add(BehavioralEvidence(
                                session_id=session_id,
                                participant_id=p.id,
                                key_utterances=_retro_json.dumps(key_ev),
                                elm_states=_retro_json.dumps(list(set(elm_eps))),
                                uptake_count=_up_n,
                                resistance_count=_res_n,
                                question_types=_retro_json.dumps(_qt),
                                convergence_direction=_conv_dir,
                                pronoun_shift=_conv_dir,
                                context="retro",
                            ))

                        # Link participant to session (skip if already linked)
                        _existing = await db.execute(
                            select(_sp).where(
                                _sp.c.session_id == session_id,
                                _sp.c.participant_id == p.id,
                            )
                        )
                        if _existing.first() is None:
                            await db.execute(
                                _sp.insert().values(session_id=session_id, participant_id=p.id)
                            )
                        profile["name"] = p.name or display_name
                        profile["participant_id"] = p.id

            job["status"] = "done"
            job["utterances"] = utterances
            job["session_id"] = session_id
            job["scores"] = scores
            job["participants"] = list(participant_profiles.values())

            # Kick off debrief generation in background (non-blocking)
            if scores and utterances:
                asyncio.create_task(
                    _generate_retro_debrief(job, session_id, utterances, scores)
                )
        except Exception as exc:
            logger.exception("[retro] job %s failed: %s", job_id, exc)
            job["status"] = "error"
            job["error"] = str(exc)

    background_tasks.add_task(_run)
    return {"job_id": job_id}


async def _generate_retro_debrief(
    job: dict[str, Any],
    session_id: str,
    utterances: list[dict],
    scores: dict,
) -> None:
    """Generate exec-coach-level debrief for retro analysis."""
    api_key = _load_settings().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        job["debrief"] = "Set an Anthropic API key in Settings to enable coaching debrief."
        return

    # Build full transcript (up to 60 utterances for richer analysis)
    sample = utterances[-60:]
    transcript_lines = "\n".join(
        f"{'[YOU]' if u['speaker_id'] == _DEFAULT_USER_SPEAKER else u['speaker_id']}: {u['text'][:200]}"
        for u in sample
    )
    score_summary = (
        f"Persuasion Score: {scores['persuasion_score']}/100 "
        f"(Timing {scores['timing_score']}/30, "
        f"Ego Safety {scores['ego_safety_score']}/30, "
        f"Convergence {scores['convergence_score']}/40)"
    )

    # Include participant profiles if available
    participants = job.get("participants", [])
    profile_lines = ""
    if participants:
        profile_lines = "PARTICIPANT PROFILES (detected from speech patterns)\n"
        for p in participants:
            profile_lines += f"  {p['speaker_id']}: {p['archetype']} (confidence {p['confidence']:.0%})\n"

    # Identify unique speakers
    speakers = set(u["speaker_id"] for u in utterances)
    non_user_speakers = speakers - {_DEFAULT_USER_SPEAKER}

    prompt = (
        "You are a $500/hr executive communication coach who specializes in "
        "persuasion dynamics and team communication patterns. You use the "
        "Communicator Superpower framework:\n"
        "- Architect: Logic + Analyze. Data-first, systematic, needs structure.\n"
        "- Firestarter: Narrative + Advocate. Energy-driven, inspires through story.\n"
        "- Inquisitor: Logic + Advocate. Questions everything, needs evidence.\n"
        "- Bridge Builder: Narrative + Analyze. Reads the room, builds consensus.\n\n"
        "The two key axes are: Logic vs. Narrative (how they process) and "
        "Advocate vs. Analyze (how they engage).\n\n"
        "ELM (Elaboration Likelihood Model) context: people in Central Route mode "
        "process through logic and evidence. People in Peripheral Route mode respond "
        "to cues, authority, social proof. Ego-threatened people shut down Central "
        "Route processing entirely.\n\n"
        f"SCORES\n{score_summary}\n\n"
        f"{profile_lines}\n"
        f"SPEAKERS: {len(speakers)} total ({len(non_user_speakers)} counterparts)\n\n"
        f"TRANSCRIPT ({len(sample)} turns)\n{transcript_lines}\n\n"
        "Write a coaching debrief with the following sections. Be specific — cite "
        "actual moments from the transcript. Use second person for the coached user.\n\n"
        "1. TEAM DYNAMICS (2-3 sentences)\n"
        "Analyze the archetype distribution in the room. Identify complementary "
        "pairings (e.g. Firestarter-Architect) and gaps. What does the composition "
        "mean for how ideas land?\n\n"
        "2. KEY MOMENTS (2-3 sentences)\n"
        "Identify the 1-2 most important moments in the conversation. Where did "
        "persuasion succeed or fail? Why — was the room in Central or Peripheral "
        "Route? Did anyone get ego-threatened?\n\n"
        "3. YOUR PERFORMANCE (2-3 sentences)\n"
        "What did you do well? Where did you miss? Be specific about what mode you "
        "were in and whether it matched what the room needed.\n\n"
        "4. COACHING PRESCRIPTION (2-3 sentences)\n"
        "One concrete behavioral change for the next meeting with this group. "
        "Frame it as: when [specific situation], do [specific action] because "
        "[specific reason based on the audience's processing style].\n\n"
        "Write in direct, confident prose. No filler. Every sentence should contain "
        "an insight the user could not derive from reading the transcript alone."
    )

    try:
        client = _anthropic.AsyncAnthropic(api_key=api_key)
        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=60.0,
        )
        debrief = response.content[0].text.strip()
        job["debrief"] = debrief
        # Also persist to DB
        async with get_db_session() as db:
            row = await db.get(MeetingSession, session_id)
            if row is not None:
                row.debrief_text = debrief
    except Exception as exc:
        logger.warning("Retro debrief failed for session %s: %s", session_id, exc)
        job["debrief"] = "Coaching debrief could not be generated."


@app.get("/retro/jobs/{job_id}")
async def get_retro_job(job_id: str) -> dict[str, Any]:
    job = _active_retro_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
