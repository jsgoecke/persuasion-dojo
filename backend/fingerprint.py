"""
Behavioral fingerprint assembly for participant profiles.

Aggregates evidence across sessions into a rich behavioral portrait: core
archetype tendency, context-specific variations, key behavioral patterns,
notable utterances, ELM tendencies, and interaction dynamics.

The fingerprint is assembled on-demand (not materialized) — every query
reflects the latest evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    BehavioralEvidence,
    Participant,
    ParticipantContextProfile,
    SessionParticipantObservation,
)


@dataclass
class ContextVariation:
    context: str
    archetype: str | None
    sessions: int
    focus_score: float
    stance_score: float


@dataclass
class NotableUtterance:
    text: str
    signals: dict[str, int]
    strength: int
    context: str


@dataclass
class BehavioralFingerprint:
    participant_id: str
    name: str | None

    # Core tendency
    archetype: str | None
    confidence: float | None
    focus_score: float | None
    stance_score: float | None
    sessions_observed: int

    # Context variations
    context_variations: list[ContextVariation] = field(default_factory=list)

    # Derived behavioral patterns (human-readable strings)
    patterns: list[str] = field(default_factory=list)

    # Top utterances across all sessions ranked by signal strength
    notable_utterances: list[NotableUtterance] = field(default_factory=list)

    # ELM tendencies across sessions
    elm_tendencies: dict[str, int] = field(default_factory=dict)

    # Interaction dynamics (averages across sessions)
    avg_convergence: float = 0.0
    avg_uptake_ratio: float = 0.5

    def to_dict(self) -> dict:
        return {
            "participant_id": self.participant_id,
            "name": self.name,
            "archetype": self.archetype,
            "confidence": self.confidence,
            "focus_score": self.focus_score,
            "stance_score": self.stance_score,
            "sessions_observed": self.sessions_observed,
            "context_variations": [
                {"context": cv.context, "archetype": cv.archetype,
                 "sessions": cv.sessions, "focus_score": cv.focus_score,
                 "stance_score": cv.stance_score}
                for cv in self.context_variations
            ],
            "patterns": self.patterns,
            "notable_utterances": [
                {"text": u.text, "signals": u.signals,
                 "strength": u.strength, "context": u.context}
                for u in self.notable_utterances
            ],
            "elm_tendencies": self.elm_tendencies,
            "avg_convergence": self.avg_convergence,
            "avg_uptake_ratio": self.avg_uptake_ratio,
        }

    def coaching_summary(self, max_lines: int = 4) -> str:
        """Compact text summary for injection into coaching prompts."""
        parts: list[str] = []
        if self.archetype:
            conf = f", {self.sessions_observed} sessions" if self.sessions_observed > 1 else ""
            parts.append(f"{self.archetype}{conf}")

        for p in self.patterns[:2]:
            parts.append(p)

        # Context shift note
        if len(self.context_variations) > 1:
            archetypes = {cv.archetype for cv in self.context_variations if cv.archetype}
            if len(archetypes) > 1:
                shifts = "; ".join(
                    f"{cv.context}: {cv.archetype}" for cv in self.context_variations
                    if cv.archetype and cv.archetype != self.archetype
                )
                if shifts:
                    parts.append(f"shifts in some contexts ({shifts})")

        return ". ".join(parts[:max_lines])


# ---------------------------------------------------------------------------
# Pattern derivation heuristics
# ---------------------------------------------------------------------------

def _derive_patterns(
    evidence_rows: list[BehavioralEvidence],
    observation_rows: list[SessionParticipantObservation],
    context_profiles: list[ParticipantContextProfile],
) -> list[str]:
    """
    Derive human-readable behavioral patterns from accumulated evidence.

    These are rule-based heuristics — no LLM involved. Each pattern is a
    coaching-relevant insight about how this person communicates.
    """
    patterns: list[str] = []
    if not evidence_rows:
        return patterns

    n = len(evidence_rows)

    # Uptake vs resistance tendency
    total_uptake = sum(e.uptake_count for e in evidence_rows)
    total_resist = sum(e.resistance_count for e in evidence_rows)
    if total_uptake + total_resist >= 3:
        ratio = total_uptake / (total_uptake + total_resist + 0.01)
        if ratio > 0.7:
            patterns.append("tends to build on ideas — natural collaborator, invite early")
        elif ratio < 0.3:
            patterns.append("often pushes back — expects evidence before aligning")
        elif 0.4 <= ratio <= 0.6:
            patterns.append("balanced engagement — builds on some ideas, challenges others")

    # ELM tendencies
    elm_counts: dict[str, int] = {}
    for e in evidence_rows:
        try:
            states = json.loads(e.elm_states) if e.elm_states else []
        except (json.JSONDecodeError, TypeError):
            states = []
        for s in states:
            elm_counts[s] = elm_counts.get(s, 0) + 1

    if elm_counts.get("ego_threat", 0) / max(n, 1) > 0.4:
        patterns.append("frequently defensive when challenged — lead with acknowledgment")
    if elm_counts.get("shortcut", 0) / max(n, 1) > 0.4:
        patterns.append("tends to agree too quickly — probe for genuine buy-in")
    if elm_counts.get("consensus_protection", 0) / max(n, 1) > 0.3:
        patterns.append("suppresses dissent — explicitly invite pushback")

    # Question tendency
    total_q: dict[str, int] = {"challenging": 0, "clarifying": 0, "confirmatory": 0}
    for e in evidence_rows:
        try:
            qt = json.loads(e.question_types) if e.question_types else {}
        except (json.JSONDecodeError, TypeError):
            qt = {}
        for k in total_q:
            total_q[k] += qt.get(k, 0)
    q_total = sum(total_q.values())
    if q_total >= 3:
        if total_q["challenging"] / q_total > 0.5:
            patterns.append("asks mostly challenging questions — welcome skepticism with data")
        elif total_q["clarifying"] / q_total > 0.5:
            patterns.append("asks clarifying questions — engage by walking through details")
        elif total_q["confirmatory"] / q_total > 0.4:
            patterns.append("asks confirmatory questions — likely ready to commit, push for action")

    # Convergence tendency
    conv_dirs = [e.convergence_direction for e in evidence_rows
                 if e.convergence_direction is not None]
    if conv_dirs:
        avg_conv = sum(conv_dirs) / len(conv_dirs)
        if avg_conv > 0.1:
            patterns.append("converges over time — patience pays off with this person")
        elif avg_conv < -0.1:
            patterns.append("tends to diverge — address concerns early, don't wait")

    # Context-specific variation
    if len(context_profiles) > 1:
        from backend.models import map_to_archetype
        archetypes = {}
        for cp in context_profiles:
            if cp.sessions >= 1:
                arch = map_to_archetype(cp.focus_score, cp.stance_score)
                if arch != "Undetermined":
                    archetypes[cp.context] = arch
        unique = set(archetypes.values())
        if len(unique) > 1:
            shifts = ", ".join(f"{ctx}={arch}" for ctx, arch in archetypes.items())
            patterns.append(f"adapts style by situation ({shifts})")

    return patterns


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

async def assemble_fingerprint(
    db: AsyncSession,
    participant_id: str,
) -> BehavioralFingerprint | None:
    """
    Build a full behavioral fingerprint for a participant from all stored data.

    Returns None if the participant doesn't exist.
    """
    participant = await db.get(Participant, participant_id)
    if participant is None:
        return None

    # Context profiles
    ctx_result = await db.execute(
        select(ParticipantContextProfile).where(
            ParticipantContextProfile.participant_id == participant_id
        )
    )
    ctx_profiles = list(ctx_result.scalars())

    from backend.models import map_to_archetype
    context_variations = [
        ContextVariation(
            context=cp.context,
            archetype=map_to_archetype(cp.focus_score, cp.stance_score),
            sessions=cp.sessions,
            focus_score=cp.focus_score,
            stance_score=cp.stance_score,
        )
        for cp in ctx_profiles
        if cp.sessions >= 1
    ]

    # Observation history
    obs_result = await db.execute(
        select(SessionParticipantObservation).where(
            SessionParticipantObservation.participant_id == participant_id
        ).order_by(SessionParticipantObservation.id.desc())
    )
    observations = list(obs_result.scalars())

    # Behavioral evidence
    ev_result = await db.execute(
        select(BehavioralEvidence).where(
            BehavioralEvidence.participant_id == participant_id
        ).order_by(BehavioralEvidence.created_at.desc())
    )
    evidence_rows = list(ev_result.scalars())

    # Notable utterances — top 5 across all sessions by strength
    all_utterances: list[NotableUtterance] = []
    for e in evidence_rows:
        try:
            utts = json.loads(e.key_utterances) if e.key_utterances else []
        except (json.JSONDecodeError, TypeError):
            utts = []
        for u in utts:
            all_utterances.append(NotableUtterance(
                text=u.get("text", ""),
                signals=u.get("signals", {}),
                strength=u.get("strength", 0),
                context=e.context,
            ))
    all_utterances.sort(key=lambda u: u.strength, reverse=True)

    # ELM tendencies
    elm_tendencies: dict[str, int] = {}
    for e in evidence_rows:
        try:
            states = json.loads(e.elm_states) if e.elm_states else []
        except (json.JSONDecodeError, TypeError):
            states = []
        for s in states:
            elm_tendencies[s] = elm_tendencies.get(s, 0) + 1

    # Convergence & uptake averages
    conv_dirs = [e.convergence_direction for e in evidence_rows
                 if e.convergence_direction is not None]
    avg_convergence = sum(conv_dirs) / len(conv_dirs) if conv_dirs else 0.0

    uptake_total = sum(e.uptake_count for e in evidence_rows)
    resist_total = sum(e.resistance_count for e in evidence_rows)
    avg_uptake_ratio = uptake_total / (uptake_total + resist_total + 0.01) if evidence_rows else 0.5

    # Derive patterns
    patterns = _derive_patterns(evidence_rows, observations, ctx_profiles)

    return BehavioralFingerprint(
        participant_id=participant_id,
        name=participant.name,
        archetype=participant.obs_archetype or participant.ps_type,
        confidence=participant.obs_confidence if participant.obs_confidence is not None else participant.ps_confidence,
        focus_score=participant.obs_focus,
        stance_score=participant.obs_stance,
        sessions_observed=participant.obs_sessions,
        context_variations=context_variations,
        patterns=patterns,
        notable_utterances=all_utterances[:5],
        elm_tendencies=elm_tendencies,
        avg_convergence=round(avg_convergence, 3),
        avg_uptake_ratio=round(avg_uptake_ratio, 3),
    )
