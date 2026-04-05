"""
ORM models, pure dataclasses, and profile update functions.

Architecture (three-layer archetype model):
─────────────────────────────────────────────────────────────────────────
Layer 1 — Core axes (User.core_focus / User.core_stance)
  Stable aggregate across ALL sessions. Starts from the self-assessment prior
  (confidence ≈ 0.35) and converges toward behavioral evidence over ~8–10 sessions.
  After ~15 sessions the self-assessment prior contributes < 10% of the score.

Layer 2 — Context-stratified axes (ContextProfile)
  One row per (user, context). Tracks how the user's expression differs by
  meeting type: board / team / 1:1 / client / all-hands / unknown.
  Used by coaching_engine.py once min_context_sessions (default 3) is reached.

Layer 3 — Session observations (MeetingSession.obs_focus / obs_stance)
  Raw behavioral read for a single session, produced by profiler.py.
  Feeds the EWMA update to Layers 1 and 2.
─────────────────────────────────────────────────────────────────────────

Pure update flow (no I/O):
  obs = SessionObservation(...)           # produced by profiler.py
  apply_session_observation(user, ctx_profiles, obs)
  snapshot = get_profile_snapshot(user, ctx_profiles, context="board")
  # snapshot.archetype → context-aware archetype for coaching_engine.py

Confidence schedule:
  confidence = 1.0 − 0.65 × e^(−sessions / 7.0), clamped to [0.35, 0.95]
  Sessions 0 → 0.35 (self-assessment prior)
  Sessions 3 → ≈0.58
  Sessions 7 → ≈0.76
  Sessions 15 → ≈0.91
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Table, Column, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend.pre_seeding import SuperpowerType
from backend.self_assessment import map_to_archetype, NEUTRAL_BAND


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

MeetingContext = Literal["board", "team", "1:1", "client", "all-hands", "unknown"]

VALID_CONTEXTS: tuple[str, ...] = ("board", "team", "1:1", "client", "all-hands", "unknown")

# Minimum behavioral sessions before a context profile is trusted over the core profile
MIN_CONTEXT_SESSIONS = 3

# Self-assessment prior confidence — behavioral evidence will dilute this
SELF_ASSESSMENT_PRIOR_CONFIDENCE = 0.35

# Confidence schedule: 1.0 - 0.65 * exp(-sessions / SESSION_HALF_LIFE)
_SESSION_HALF_LIFE = 7.0
_CONF_FLOOR = 0.35
_CONF_CEIL = 0.95


# ---------------------------------------------------------------------------
# ORM base
# ---------------------------------------------------------------------------

class Base(AsyncAttrs, DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Association table: MeetingSession ↔ Participant (many-to-many)
# ---------------------------------------------------------------------------

session_participants = Table(
    "session_participants",
    Base.metadata,
    Column("session_id", String(36), ForeignKey("meeting_sessions.id"), primary_key=True),
    Column("participant_id", String(36), ForeignKey("participants.id"), primary_key=True),
)


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """
    The app user. Owns their own core profile (evolves with session data)
    plus an initial self-assessment snapshot.

    Core axes update on every session via apply_session_observation().
    Self-assessment snapshot is written once on onboarding and never updated
    (it preserves the original self-report for UI display and audit).
    """
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    display_name: Mapped[str | None] = mapped_column(String(200))

    # ── Layer 1: core axes ─────────────────────────────────────────────────
    # Aggregate behavioral signal. Starts at self-assessment values.
    # Positive focus = Logic; negative = Narrative.
    # Positive stance = Advocacy; negative = Analysis.
    core_focus: Mapped[float] = mapped_column(Float, default=0.0)
    core_stance: Mapped[float] = mapped_column(Float, default=0.0)
    core_focus_var: Mapped[float] = mapped_column(Float, default=0.0)
    core_stance_var: Mapped[float] = mapped_column(Float, default=0.0)
    core_confidence: Mapped[float] = mapped_column(Float, default=SELF_ASSESSMENT_PRIOR_CONFIDENCE)
    core_sessions: Mapped[int] = mapped_column(Integer, default=0)

    # ── Self-assessment snapshot (immutable after onboarding) ──────────────
    sa_focus: Mapped[float | None] = mapped_column(Float)
    sa_stance: Mapped[float | None] = mapped_column(Float)
    sa_archetype: Mapped[str | None] = mapped_column(String(50))
    sa_confidence: Mapped[float | None] = mapped_column(Float)
    sa_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Relationships ──────────────────────────────────────────────────────
    context_profiles: Mapped[list[ContextProfile]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    participants: Mapped[list[Participant]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[MeetingSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ContextProfile(Base):
    """
    Layer 2: per-context axis scores for a user.

    One row per (user, context). Tracks how the user's communication style
    shifts by meeting type. Only consulted by coaching_engine.py once
    sessions >= MIN_CONTEXT_SESSIONS for that context.

    Example: A user who is an Inquisitor overall (core) may consistently
    express as Firestarter in board presentations (context_profile "board").
    """
    __tablename__ = "context_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    context: Mapped[str] = mapped_column(String(50))       # MeetingContext value
    focus_score: Mapped[float] = mapped_column(Float, default=0.0)
    stance_score: Mapped[float] = mapped_column(Float, default=0.0)
    focus_var: Mapped[float] = mapped_column(Float, default=0.0)
    stance_var: Mapped[float] = mapped_column(Float, default=0.0)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped[User] = relationship(back_populates="context_profiles")


class Participant(Base):
    """
    A meeting counterpart (not the app user).

    Pre-seeded from free text via pre_seeding.classify(). Profile evolves
    as the user attends more meetings with them.

    Note: Participant profiling is for the user's benefit (coaching context),
    not for the participant. Participant data stays local (SQLite only).
    """
    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    name: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(String(2000))   # free-text used for pre-seeding

    # Pre-seed classification (from pre_seeding.classify)
    ps_type: Mapped[str | None] = mapped_column(String(50))     # SuperpowerType or None
    ps_confidence: Mapped[float | None] = mapped_column(Float)
    ps_reasoning: Mapped[str | None] = mapped_column(String(500))
    ps_state: Mapped[str] = mapped_column(String(20), default="pending")  # "active"|"pending"

    # Confidence range across sessions (append-only, V1)
    # Stored as comma-separated floats for V1 simplicity; V2 gets a proper table
    ps_confidence_history: Mapped[str | None] = mapped_column(String(500))

    # Behavioral observation (EWMA across sessions from profiler.py)
    obs_focus: Mapped[float | None] = mapped_column(Float)         # -100…+100
    obs_stance: Mapped[float | None] = mapped_column(Float)        # -100…+100
    obs_focus_var: Mapped[float] = mapped_column(Float, default=0.0)
    obs_stance_var: Mapped[float] = mapped_column(Float, default=0.0)
    obs_confidence: Mapped[float | None] = mapped_column(Float)
    obs_sessions: Mapped[int] = mapped_column(Integer, default=0)
    obs_archetype: Mapped[str | None] = mapped_column(String(50))  # derived from axes

    user: Mapped[User] = relationship(back_populates="participants")
    sessions: Mapped[list[MeetingSession]] = relationship(
        secondary=session_participants, back_populates="participants"
    )
    context_profiles: Mapped[list["ParticipantContextProfile"]] = relationship(
        back_populates="participant", cascade="all, delete-orphan"
    )
    behavioral_evidence: Mapped[list["BehavioralEvidence"]] = relationship(
        back_populates="participant", cascade="all, delete-orphan"
    )


class ParticipantContextProfile(Base):
    """Per-context axis scores for a participant (mirrors ContextProfile for users)."""
    __tablename__ = "participant_context_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    participant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("participants.id"), index=True
    )
    context: Mapped[str] = mapped_column(String(50))
    focus_score: Mapped[float] = mapped_column(Float, default=0.0)
    stance_score: Mapped[float] = mapped_column(Float, default=0.0)
    focus_var: Mapped[float] = mapped_column(Float, default=0.0)
    stance_var: Mapped[float] = mapped_column(Float, default=0.0)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    participant: Mapped[Participant] = relationship(back_populates="context_profiles")


class SessionParticipantObservation(Base):
    """Raw per-session classification for a participant (audit trail)."""
    __tablename__ = "session_participant_observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("meeting_sessions.id"), index=True
    )
    participant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("participants.id"), index=True
    )
    focus_score: Mapped[float] = mapped_column(Float)
    stance_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    archetype: Mapped[str] = mapped_column(String(50))
    utterance_count: Mapped[int] = mapped_column(Integer)
    context: Mapped[str] = mapped_column(String(50), default="unknown")

    # Per-participant convergence signals (populated at session end)
    convergence_score: Mapped[float | None] = mapped_column(Float)
    lsm_score: Mapped[float | None] = mapped_column(Float)
    pronoun_score: Mapped[float | None] = mapped_column(Float)
    uptake_score: Mapped[float | None] = mapped_column(Float)


class BehavioralEvidence(Base):
    """
    Per-session behavioral evidence for a participant.

    Captures what they actually said and did — key utterances, signal patterns,
    ELM states, interaction dynamics — forming a situational fingerprint over time.
    """
    __tablename__ = "behavioral_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("meeting_sessions.id"), index=True
    )
    participant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("participants.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Top utterances by signal strength: [{text, signals, strength}]
    key_utterances: Mapped[str | None] = mapped_column(Text)

    # Detected behavioral markers
    elm_states: Mapped[str | None] = mapped_column(Text)        # JSON: ["ego_threat", ...]
    uptake_count: Mapped[int] = mapped_column(Integer, default=0)
    resistance_count: Mapped[int] = mapped_column(Integer, default=0)
    question_types: Mapped[str | None] = mapped_column(Text)    # JSON: {challenging: N, ...}

    # Interaction dynamics with user
    convergence_direction: Mapped[float | None] = mapped_column(Float)  # -1..+1
    pronoun_shift: Mapped[float | None] = mapped_column(Float)          # -1..+1

    # Situational context
    context: Mapped[str] = mapped_column(String(50), default="unknown")
    situation_note: Mapped[str | None] = mapped_column(String(500))

    participant: Mapped[Participant] = relationship(back_populates="behavioral_evidence")


class CoachingEffectiveness(Base):
    """
    Aggregate coaching effectiveness per (user_archetype, counterpart_archetype, context).

    Tracks how well coaching prompts work for each archetype pairing in each
    context. Updated at session end from per-prompt effectiveness scores.
    """
    __tablename__ = "coaching_effectiveness"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_archetype: Mapped[str] = mapped_column(String(50), index=True)
    counterpart_archetype: Mapped[str] = mapped_column(String(50))
    context: Mapped[str] = mapped_column(String(50), default="unknown")

    avg_effectiveness: Mapped[float] = mapped_column(Float, default=0.5)
    total_prompts: Mapped[int] = mapped_column(Integer, default=0)
    effective_prompts: Mapped[int] = mapped_column(Integer, default=0)
    suggested_cadence_s: Mapped[float] = mapped_column(Float, default=30.0)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CoachingBullet(Base):
    """
    ACE-style structured coaching insight — one discrete lesson per row.

    Replaces the monolithic markdown playbook with incremental, counter-tracked
    bullets that are merged deterministically (no LLM in the merge step).
    Bullets are scored by relevance for each coaching prompt and selected
    by a fast Python ranker (<10ms).
    """
    __tablename__ = "coaching_bullets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Content
    content: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(30))  # effective|ineffective|pairing|trend|tactic

    # ACE counters
    helpful_count: Mapped[int] = mapped_column(Integer, default=0)
    harmful_count: Mapped[int] = mapped_column(Integer, default=0)

    # Dimensional metadata for fast filtering
    counterpart_archetype: Mapped[str | None] = mapped_column(String(50))
    elm_state: Mapped[str | None] = mapped_column(String(30))
    context: Mapped[str | None] = mapped_column(String(50))
    user_archetype: Mapped[str | None] = mapped_column(String(50))

    # Provenance
    source_session_id: Mapped[str | None] = mapped_column(String(36))
    last_evidence_session_id: Mapped[str | None] = mapped_column(String(36))
    evidence_count: Mapped[int] = mapped_column(Integer, default=1)

    # Deduplication key — lightweight text fingerprint
    dedup_key: Mapped[str | None] = mapped_column(String(200), index=True)

    # Soft delete
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    retired_reason: Mapped[str | None] = mapped_column(String(200))


class SkillBadge(Base):
    """
    A skill badge awarded when a coaching prompt type hasn't fired for
    3 consecutive sessions — indicating the user has internalized the skill.

    trigger_type maps to the coaching prompt's triggered_by string,
    e.g. "elm:ego_threat" → "Psychological Safety" badge.
    """
    __tablename__ = "skill_badges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(50))      # e.g. "elm:ego_threat"
    badge_name: Mapped[str] = mapped_column(String(200))       # e.g. "Psychological Safety"
    tagline: Mapped[str] = mapped_column(String(200))          # e.g. "the skill is yours now"
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    consecutive_sessions: Mapped[int] = mapped_column(Integer, default=3)


class SkillMastery(Base):
    """
    Bayesian Knowledge Tracing (BKT) per-skill mastery tracking.

    Replaces frequency-decay SkillBadge logic with proper BKT.
    One row per (user, skill_key). P(know) converges toward 1.0 as the
    user demonstrates correct application of the skill.

    Skill key taxonomy (simplified to 5 keys per CEO review):
        elm:ego_threat, elm:shortcut, pairing:archetype_match,
        timing:talk_ratio, convergence:uptake
    """
    __tablename__ = "skill_mastery"
    __table_args__ = (
        UniqueConstraint("user_id", "skill_key", name="uq_skill_mastery_user_skill"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    skill_key: Mapped[str] = mapped_column(String(50), index=True)

    # BKT parameters
    p_know: Mapped[float] = mapped_column(Float, default=0.1)    # P(L0) — prior knowledge
    p_transit: Mapped[float] = mapped_column(Float, default=0.05)  # P(T) — learning rate (conservative: ~20 sessions to converge)
    p_guess: Mapped[float] = mapped_column(Float, default=0.2)   # P(G) — lucky guess
    p_slip: Mapped[float] = mapped_column(Float, default=0.1)    # P(S) — careless error

    # Observation tracking
    opportunities: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# Valid skill keys (simplified taxonomy per CEO review finding #3)
SKILL_KEYS: tuple[str, ...] = (
    "elm:ego_threat",
    "elm:shortcut",
    "pairing:archetype_match",
    "timing:talk_ratio",
    "convergence:uptake",
)


class MeetingSession(Base):
    """
    A single meeting session.

    Named MeetingSession (not Session) to avoid collision with SQLAlchemy's
    own Session class.

    obs_focus / obs_stance are the behavioral axis readings from profiler.py
    for this session. These feed the EWMA update to User.core_* and ContextProfile.
    """
    __tablename__ = "meeting_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    context: Mapped[str] = mapped_column(String(50), default="unknown")  # MeetingContext
    title: Mapped[str | None] = mapped_column(String(500))               # from calendar

    # Scoring (from scoring.py)
    persuasion_score: Mapped[int | None] = mapped_column(Integer)        # 0–100
    growth_delta: Mapped[float | None] = mapped_column(Float)

    # Layer 3: behavioral observation (from profiler.py)
    obs_focus: Mapped[float | None] = mapped_column(Float)               # -100…+100
    obs_stance: Mapped[float | None] = mapped_column(Float)
    obs_utterance_count: Mapped[int] = mapped_column(Integer, default=0)
    obs_confidence: Mapped[float] = mapped_column(Float, default=0.0)    # signal quality

    # Profile snapshot used for coaching during this session
    coaching_archetype: Mapped[str | None] = mapped_column(String(50))
    coaching_context: Mapped[str | None] = mapped_column(String(50))
    coaching_confidence: Mapped[float | None] = mapped_column(Float)

    # Post-session Opus debrief (populated in background after session ends)
    debrief_text: Mapped[str | None] = mapped_column(String(4000))

    user: Mapped[User] = relationship(back_populates="sessions")
    prompts: Mapped[list["Prompt"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    utterances: Mapped[list["Utterance"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    participants: Mapped[list[Participant]] = relationship(
        secondary=session_participants, back_populates="sessions"
    )


class Prompt(Base):
    """
    A coaching prompt surfaced during a session.

    layer: "self"     → is the user in the right mode right now?
           "audience" → what does this participant need?
           "group"    → when to push, yield, or invite contribution?

    trigger: "elm"      → ELM ego-threat event detected
             "cadence"  → regular cadence floor reached
             "fallback" → Haiku timeout, cached prompt used
    """
    __tablename__ = "prompts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("meeting_sessions.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    layer: Mapped[str] = mapped_column(String(20))             # "self"|"audience"|"group"
    text: Mapped[str] = mapped_column(String(2000))
    trigger: Mapped[str] = mapped_column(String(20))           # "elm"|"cadence"|"fallback"
    triggered_by: Mapped[str | None] = mapped_column(String(50))  # e.g. "elm:ego_threat"
    was_shown: Mapped[bool] = mapped_column(Boolean, default=True)

    # Effectiveness tracking (populated at session end)
    utterance_index: Mapped[int | None] = mapped_column(Integer)
    effectiveness_score: Mapped[float | None] = mapped_column(Float)
    convergence_before: Mapped[float | None] = mapped_column(Float)
    convergence_after: Mapped[float | None] = mapped_column(Float)
    counterpart_archetype: Mapped[str | None] = mapped_column(String(50))

    participant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("participants.id")
    )
    # Comma-separated IDs of coaching bullets included in this prompt's context
    bullet_ids_used: Mapped[str | None] = mapped_column(String(1000))

    session: Mapped[MeetingSession] = relationship(back_populates="prompts")
    participant: Mapped[Participant | None] = relationship()


class Utterance(Base):
    """
    A single speaker turn within a MeetingSession, persisted for transcript retrieval.

    sequence: 0-based insertion order within the session.
    is_user: True when speaker_id == the coached user's speaker ID.
    start_s / end_s: time offsets from session start (0.0 for retro text imports
    where timestamps are unavailable).
    """
    __tablename__ = "utterances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("meeting_sessions.id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    speaker_id: Mapped[str] = mapped_column(String(50))
    text: Mapped[str] = mapped_column(String(5000))
    start_s: Mapped[float] = mapped_column(Float, default=0.0)
    end_s: Mapped[float] = mapped_column(Float, default=0.0)
    is_user: Mapped[bool] = mapped_column(Boolean, default=False)

    session: Mapped[MeetingSession] = relationship(back_populates="utterances")


# ---------------------------------------------------------------------------
# Pure dataclasses (no ORM — pass these across module boundaries)
# ---------------------------------------------------------------------------

@dataclass
class SessionObservation:
    """
    Behavioral axis observation for a single session.

    Produced by profiler.py after a session ends (or in real time for live coaching).
    Used by apply_session_observation() to update Layers 1 and 2.

    obs_confidence: how much behavioral signal was present (0.0–1.0).
    A session with only 3 utterances has low obs_confidence; one with 50 has high.
    Low-confidence observations are weighted less in the EWMA update.
    """
    session_id: str
    context: str                    # MeetingContext value
    focus_score: float              # -100…+100, behavioral read
    stance_score: float             # -100…+100, behavioral read
    utterance_count: int
    obs_confidence: float = 1.0     # signal quality (0.0–1.0)


@dataclass
class ProfileSnapshot:
    """
    Context-aware profile for coaching_engine.py to consume.

    archetype: the active archetype — either context-specific (if enough context
               data) or core (if insufficient context data).
    core_archetype: the aggregate archetype across all contexts.
    context_shifts: True when the context archetype differs from core —
                    signals that the user adapts their style situationally.

    Example coaching use:
      "You're an Inquisitor overall, but in board meetings you shift toward
      Firestarter. This board meeting: lean into that narrative opening first,
      then anchor with data once you have the room."
    """
    archetype: SuperpowerType | Literal["Undetermined"]
    focus_score: float
    stance_score: float
    focus_variance: float
    stance_variance: float
    confidence: float
    context: str
    context_sessions: int
    is_context_specific: bool       # True → context profile used; False → core used
    core_archetype: SuperpowerType | Literal["Undetermined"]
    core_sessions: int
    context_shifts: bool            # True when context archetype ≠ core archetype


# ---------------------------------------------------------------------------
# Confidence schedule
# ---------------------------------------------------------------------------

def confidence_from_sessions(n_sessions: int) -> float:
    """
    Map behavioral session count to profile confidence.

    Uses exponential saturation: confidence = 1.0 − 0.65 × e^(−n / 7.0)
    Clamped to [SELF_ASSESSMENT_PRIOR_CONFIDENCE, 0.95].

    Rationale: the self-assessment prior anchors the floor at 0.35.
    Behavioral evidence accumulates with diminishing returns — each new session
    adds less incremental confidence than the previous one.
    """
    if n_sessions <= 0:
        return SELF_ASSESSMENT_PRIOR_CONFIDENCE
    raw = 1.0 - 0.65 * math.exp(-n_sessions / _SESSION_HALF_LIFE)
    return round(min(_CONF_CEIL, max(_CONF_FLOOR, raw)), 4)


# ---------------------------------------------------------------------------
# Core EWMA update (pure — no I/O)
# ---------------------------------------------------------------------------

def _welford_m2_update(
    old_m2: float,
    old_mean: float,
    new_mean: float,
    new_obs: float,
    obs_confidence: float,
) -> float:
    """
    Welford's online M2 accumulator update (numerically stable).

    Stores M2 (running sum of squared deviations from mean), NOT variance.
    Variance is derived on read as: variance = M2 / n (for n >= 2).

    Must be called AFTER _ewma_update so that new_mean is available.
    If obs_confidence == 0, returns old_m2 unchanged.

    The *_var fields on ORM models actually store M2 (not variance).
    """
    weight = max(0.0, min(1.0, obs_confidence))
    if weight == 0.0:
        return old_m2
    delta_old = new_obs - old_mean
    delta_new = new_obs - new_mean
    # Standard Welford: M2 += (x - old_mean) * (x - new_mean)
    # Weight attenuates low-confidence observations
    new_m2 = old_m2 + weight * delta_old * delta_new
    return max(0.0, round(new_m2, 4))


def m2_to_variance(m2: float, n_sessions: int) -> float:
    """Convert M2 accumulator to population variance. Returns 0.0 for n < 2."""
    if n_sessions < 2:
        return 0.0
    return max(0.0, m2 / n_sessions)


def _ewma_update(
    old_score: float,
    old_sessions: int,
    new_obs: float,
    obs_confidence: float,
) -> float:
    """
    Running-mean update weighted by observation confidence.

    new_score = (old_sessions × old_score + obs_confidence × new_obs)
                ─────────────────────────────────────────────────────
                        old_sessions + obs_confidence

    Low obs_confidence (few utterances) contributes less to the aggregate.
    High obs_confidence (many utterances) counts nearly as a full session.
    """
    weight = max(0.0, min(1.0, obs_confidence))
    denominator = old_sessions + weight
    if denominator == 0:
        return new_obs
    return (old_sessions * old_score + weight * new_obs) / denominator


def apply_session_observation(
    user: User,
    context_profiles: dict[str, ContextProfile],
    obs: SessionObservation,
) -> None:
    """
    Update Layer 1 (User core axes) and Layer 2 (ContextProfile) in place.

    Parameters
    ----------
    user:
        The User ORM object. Modified in place.
    context_profiles:
        Dict mapping context → ContextProfile for this user. The matching
        context is updated. If no matching profile exists, this is a no-op
        for Layer 2 (caller should create a ContextProfile first).
    obs:
        Behavioral observation from profiler.py.

    Note: this function is pure (no database I/O). Caller is responsible
    for committing the changes via the async session.
    """
    # ── Layer 1: update core axes ──────────────────────────────────────────
    old_focus = user.core_focus
    old_stance = user.core_stance
    new_focus = round(
        _ewma_update(user.core_focus, user.core_sessions, obs.focus_score, obs.obs_confidence),
        1,
    )
    new_stance = round(
        _ewma_update(user.core_stance, user.core_sessions, obs.stance_score, obs.obs_confidence),
        1,
    )
    n_after = user.core_sessions + 1
    # M2 accumulator update AFTER EWMA (needs new_mean), BEFORE session count increments
    user.core_focus_var = _welford_m2_update(
        user.core_focus_var, old_focus, new_focus, obs.focus_score, obs.obs_confidence,
    )
    user.core_stance_var = _welford_m2_update(
        user.core_stance_var, old_stance, new_stance, obs.stance_score, obs.obs_confidence,
    )
    user.core_focus = new_focus
    user.core_stance = new_stance
    user.core_sessions = n_after
    user.core_confidence = confidence_from_sessions(user.core_sessions)

    # ── Layer 2: update context-specific profile ───────────────────────────
    ctx = context_profiles.get(obs.context)
    if ctx is not None:
        old_ctx_focus = ctx.focus_score
        old_ctx_stance = ctx.stance_score
        new_ctx_focus = round(
            _ewma_update(ctx.focus_score, ctx.sessions, obs.focus_score, obs.obs_confidence),
            1,
        )
        new_ctx_stance = round(
            _ewma_update(ctx.stance_score, ctx.sessions, obs.stance_score, obs.obs_confidence),
            1,
        )
        n_ctx_after = ctx.sessions + 1
        ctx.focus_var = _welford_m2_update(
            ctx.focus_var, old_ctx_focus, new_ctx_focus, obs.focus_score, obs.obs_confidence,
        )
        ctx.stance_var = _welford_m2_update(
            ctx.stance_var, old_ctx_stance, new_ctx_stance, obs.stance_score, obs.obs_confidence,
        )
        ctx.focus_score = new_ctx_focus
        ctx.stance_score = new_ctx_stance
        ctx.sessions = n_ctx_after
        ctx.updated_at = _now()


def apply_participant_observation(
    participant: Participant,
    context_profiles: dict[str, ParticipantContextProfile],
    focus_score: float,
    stance_score: float,
    confidence: float,
    context: str = "unknown",
    *,
    neutral_band: int = NEUTRAL_BAND,
) -> None:
    """
    Update a participant's behavioral profile from a session observation.

    Same EWMA logic as apply_session_observation() but for counterparts.
    Modifies the Participant and matching ParticipantContextProfile in place.
    """
    old_sessions = participant.obs_sessions or 0
    old_focus = participant.obs_focus or 0.0
    old_stance = participant.obs_stance or 0.0

    new_focus = round(
        _ewma_update(old_focus, old_sessions, focus_score, confidence), 1
    )
    new_stance = round(
        _ewma_update(old_stance, old_sessions, stance_score, confidence), 1
    )
    n_after = old_sessions + 1
    # M2 accumulator update AFTER EWMA (needs new_mean)
    participant.obs_focus_var = _welford_m2_update(
        participant.obs_focus_var, old_focus, new_focus, focus_score, confidence,
    )
    participant.obs_stance_var = _welford_m2_update(
        participant.obs_stance_var, old_stance, new_stance, stance_score, confidence,
    )
    participant.obs_focus = new_focus
    participant.obs_stance = new_stance
    participant.obs_sessions = n_after
    participant.obs_confidence = round(
        confidence_from_sessions(participant.obs_sessions), 4
    )
    participant.obs_archetype = map_to_archetype(
        participant.obs_focus, participant.obs_stance, neutral_band=neutral_band
    )
    participant.updated_at = _now()

    # Update context-specific profile
    ctx = context_profiles.get(context)
    if ctx is not None:
        old_ctx_focus = ctx.focus_score
        old_ctx_stance = ctx.stance_score
        new_ctx_focus = round(
            _ewma_update(ctx.focus_score, ctx.sessions, focus_score, confidence), 1
        )
        new_ctx_stance = round(
            _ewma_update(ctx.stance_score, ctx.sessions, stance_score, confidence), 1
        )
        n_ctx_after = ctx.sessions + 1
        ctx.focus_var = _welford_m2_update(
            ctx.focus_var, old_ctx_focus, new_ctx_focus, focus_score, confidence,
        )
        ctx.stance_var = _welford_m2_update(
            ctx.stance_var, old_ctx_stance, new_ctx_stance, stance_score, confidence,
        )
        ctx.focus_score = new_ctx_focus
        ctx.stance_score = new_ctx_stance
        ctx.sessions = n_ctx_after
        ctx.updated_at = _now()


def get_profile_snapshot(
    user: User,
    context_profiles: dict[str, ContextProfile],
    context: str,
    *,
    min_context_sessions: int = MIN_CONTEXT_SESSIONS,
    neutral_band: int = NEUTRAL_BAND,
) -> ProfileSnapshot:
    """
    Return a context-aware ProfileSnapshot for coaching_engine.py.

    Decision logic:
      1. If context_profiles[context] exists AND sessions >= min_context_sessions
         → use context-specific scores (Layer 2)
      2. Otherwise
         → fall back to core scores (Layer 1)

    Both branches still report core_archetype for comparison and for the
    coaching engine to detect context_shifts.

    Parameters
    ----------
    user:           User ORM object.
    context_profiles: dict of ContextProfile keyed by context string.
    context:        The meeting context (MeetingContext value).
    min_context_sessions: minimum sessions before context profile is trusted.
    neutral_band:   neutral band for archetype mapping (default: 15).
    """
    core_archetype = map_to_archetype(
        user.core_focus, user.core_stance, neutral_band=neutral_band
    )

    ctx = context_profiles.get(context)
    use_context = ctx is not None and ctx.sessions >= min_context_sessions

    if use_context:
        archetype = map_to_archetype(
            ctx.focus_score, ctx.stance_score, neutral_band=neutral_band
        )
        focus_score = ctx.focus_score
        stance_score = ctx.stance_score
        focus_variance = m2_to_variance(ctx.focus_var, ctx.sessions)
        stance_variance = m2_to_variance(ctx.stance_var, ctx.sessions)
        context_sessions = ctx.sessions
        # Confidence is the minimum of core confidence and a context-session-based value
        context_conf = confidence_from_sessions(ctx.sessions)
        confidence = min(user.core_confidence, context_conf)
    else:
        archetype = core_archetype
        focus_score = user.core_focus
        stance_score = user.core_stance
        focus_variance = m2_to_variance(user.core_focus_var, user.core_sessions)
        stance_variance = m2_to_variance(user.core_stance_var, user.core_sessions)
        context_sessions = ctx.sessions if ctx else 0
        confidence = user.core_confidence

    context_shifts = (
        use_context
        and archetype != core_archetype
        and archetype != "Undetermined"
        and core_archetype != "Undetermined"
    )

    return ProfileSnapshot(
        archetype=archetype,
        focus_score=focus_score,
        stance_score=stance_score,
        focus_variance=focus_variance,
        stance_variance=stance_variance,
        confidence=confidence,
        context=context,
        context_sessions=context_sessions,
        is_context_specific=use_context,
        core_archetype=core_archetype,
        core_sessions=user.core_sessions,
        context_shifts=context_shifts,
    )


def seed_from_self_assessment(
    user: User,
    focus_score: float,
    stance_score: float,
    archetype: str | None,
    confidence: float,
) -> None:
    """
    Seed Layer 1 from a completed self-assessment. Call once on onboarding.

    Sets the core axes to the self-assessment values (confidence = sa_confidence),
    and stores the original self-assessment snapshot for display and audit.

    Raises ValueError if called when sa_completed_at is already set
    (prevents accidental overwrites after onboarding).
    """
    if user.sa_completed_at is not None:
        raise ValueError(
            f"User {user.id} already has a self-assessment (completed "
            f"{user.sa_completed_at.isoformat()}). Cannot re-seed."
        )

    now = _now()
    user.core_focus = round(focus_score, 1)
    user.core_stance = round(stance_score, 1)
    user.core_confidence = round(min(_CONF_CEIL, max(_CONF_FLOOR, confidence)), 4)
    user.core_sessions = 0  # no behavioral sessions yet

    user.sa_focus = round(focus_score, 1)
    user.sa_stance = round(stance_score, 1)
    user.sa_archetype = archetype
    user.sa_confidence = round(confidence, 4)
    user.sa_completed_at = now


# ---------------------------------------------------------------------------
# CAPS If-Then Signature — canonical home: backend/scoring.py
# Re-exported here for backward compatibility.
# ---------------------------------------------------------------------------
from backend.scoring import CAPSSignature, compute_caps_signature  # noqa: F401, E402
