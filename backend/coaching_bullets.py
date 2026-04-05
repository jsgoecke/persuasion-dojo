"""
ACE-style structured coaching playbook — incremental bullet store.

Replaces the monolithic markdown playbook (coaching_memory.py) with a
structured bullet store backed by SQLite.  Three ACE roles:

  Reflector  — Opus extracts JSON delta entries from session evidence
  Curator    — deterministic Python merge (no LLM) into the bullet store
  Selector   — fast relevance scoring (<10ms) picks top bullets for Haiku

Each bullet has helpful/harmful counters updated from per-prompt
effectiveness scores, creating a closed adaptive loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CoachingBullet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ACTIVE_BULLETS = 100
_RETIRE_THRESHOLD_MARGIN = 2  # harmful >= helpful + margin → retire
_MAX_DELTAS_PER_SESSION = 8
_MAX_CONTEXT_BULLETS = 15
_MAX_CONTEXT_WORDS = 500

# Relevance scoring weights
_W_NET_HELPFUL = 0.5
_W_NET_HELPFUL_CAP = 3.0
_W_EVIDENCE = 0.3
_W_EVIDENCE_CAP = 1.5
_W_ARCHETYPE_MATCH = 3.0
_W_ARCHETYPE_MISMATCH = -0.5
_W_ELM_MATCH = 2.5
_W_ELM_MISMATCH = -0.3
_W_CONTEXT_MATCH = 1.5
_W_CONTEXT_MISMATCH = -0.2
_W_CATEGORY_EFFECTIVE = 0.5
_W_CATEGORY_INEFFECTIVE = 0.3
_RECENCY_DAYS = 30

# BKT-aware weights (Phase 3)
_W_SKILL_MASTERED = -2.0   # Penalize bullets for mastered skills (P(know) > 0.85)
_W_SKILL_LEARNING = 1.5    # Bonus for skills in zone of proximal development (P(know) 0.3–0.7)
_W_THOMPSON = 2.0          # Scale Thompson [0,1] to match deterministic score magnitude

# Effectiveness thresholds for feedback
_EFF_HELPFUL_THRESHOLD = 0.6
_EFF_HARMFUL_THRESHOLD = 0.3

# Stop words for dedup key computation
_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for",
    "from", "has", "have", "in", "is", "it", "its", "not", "of", "on",
    "or", "that", "the", "their", "them", "then", "there", "they",
    "this", "to", "was", "were", "will", "with", "you", "your",
})

# Playbook fallback path (legacy)
_PLAYBOOK_DIR = Path(__file__).resolve().parent.parent / "data" / "playbooks"


# ---------------------------------------------------------------------------
# Dedup key
# ---------------------------------------------------------------------------

def compute_dedup_key(content: str) -> str:
    """
    Lightweight text fingerprint for deduplication.

    Lowercase, strip punctuation, remove stop words, sort remaining words,
    take first 12 alphabetically.  Deterministic and O(1).
    """
    text = content.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    words = [w for w in text.split() if w not in _STOP_WORDS and len(w) > 1]
    words = sorted(set(words))
    return " ".join(words[:12])


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

def relevance_score(
    bullet: CoachingBullet,
    counterpart_archetype: str | None = None,
    elm_state: str | None = None,
    context: str | None = None,
    now: datetime | None = None,
    skill_mastery: dict[str, float] | None = None,
) -> float:
    """
    Score a bullet for relevance to the current coaching situation.

    skill_mastery: optional dict of skill_key → P(know). When provided,
    applies BKT-aware weighting: mastered skills get penalized, skills in
    the zone of proximal development get a bonus.
    """
    now = now or datetime.now(timezone.utc)
    score = 0.0

    # Net helpfulness
    net = bullet.helpful_count - bullet.harmful_count
    score += min(net * _W_NET_HELPFUL, _W_NET_HELPFUL_CAP)

    # Evidence breadth
    score += min(bullet.evidence_count * _W_EVIDENCE, _W_EVIDENCE_CAP)

    # Dimensional matches
    if bullet.counterpart_archetype:
        if counterpart_archetype and bullet.counterpart_archetype == counterpart_archetype:
            score += _W_ARCHETYPE_MATCH
        else:
            score += _W_ARCHETYPE_MISMATCH

    if bullet.elm_state:
        if elm_state and bullet.elm_state == elm_state:
            score += _W_ELM_MATCH
        elif elm_state:
            score += _W_ELM_MISMATCH

    if bullet.context:
        if context and bullet.context == context:
            score += _W_CONTEXT_MATCH
        else:
            score += _W_CONTEXT_MISMATCH

    # Category bonus
    if bullet.category in ("effective", "tactic"):
        score += _W_CATEGORY_EFFECTIVE
    elif bullet.category == "ineffective":
        score += _W_CATEGORY_INEFFECTIVE

    # Recency bonus (decays linearly over _RECENCY_DAYS)
    updated = bullet.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    days_old = max(0, (now - updated).days)
    score += max(0.0, 1.0 - days_old / _RECENCY_DAYS)

    # BKT skill mastery weighting
    if skill_mastery and bullet.elm_state:
        # Map bullet's elm_state to skill key
        skill_key = bullet.elm_state if bullet.elm_state in skill_mastery else None
        if skill_key:
            p_know = skill_mastery[skill_key]
            if p_know > 0.85:
                score += _W_SKILL_MASTERED
            elif 0.3 <= p_know <= 0.7:
                score += _W_SKILL_LEARNING

    return score


# ---------------------------------------------------------------------------
# Thompson Sampling (Phase 4)
# ---------------------------------------------------------------------------

def thompson_sample_score(
    helpful: int,
    harmful: int,
    *,
    alpha_prior: float = 1.0,
    beta_prior: float = 1.0,
) -> float:
    """
    Draw from Beta(alpha + helpful, beta + harmful).

    Returns a sample in [0, 1]. Higher helpful → samples skew higher.
    Pure function (uses random, but deterministic for a given seed).
    """
    alpha = alpha_prior + helpful
    beta_param = beta_prior + harmful
    return random.betavariate(alpha, beta_param)


def contextual_relevance_score(
    bullet: CoachingBullet,
    counterpart_archetype: str | None = None,
    elm_state: str | None = None,
    context: str | None = None,
    skill_mastery: dict[str, float] | None = None,
    now: datetime | None = None,
    *,
    explore: bool = True,
) -> float:
    """
    Combines deterministic relevance scoring with Thompson Sampling exploration.

    When explore=True:
        score = relevance_score(...) + thompson_sample_score(helpful, harmful)
    When explore=False:
        score = relevance_score(...)  # identical to existing behavior

    This allows the system to explore under-tested bullets while still
    prioritizing known-good ones.
    """
    base = relevance_score(
        bullet,
        counterpart_archetype=counterpart_archetype,
        elm_state=elm_state,
        context=context,
        now=now,
        skill_mastery=skill_mastery,
    )
    if not explore:
        return base

    thompson = thompson_sample_score(bullet.helpful_count, bullet.harmful_count)
    return base + _W_THOMPSON * thompson


# ---------------------------------------------------------------------------
# Context selection (fast — <10ms)
# ---------------------------------------------------------------------------

async def get_coaching_context(
    db: AsyncSession,
    user_id: str,
    counterpart_archetype: str | None = None,
    elm_state: str | None = None,
    context: str | None = None,
    *,
    max_bullets: int = _MAX_CONTEXT_BULLETS,
    max_words: int = _MAX_CONTEXT_WORDS,
) -> tuple[str, list[str]]:
    """
    Select the most relevant coaching bullets and format for Haiku.

    Returns (formatted_text, list_of_bullet_ids).
    Falls back to the legacy markdown playbook if no bullets exist.
    """
    result = await db.execute(
        select(CoachingBullet).where(
            CoachingBullet.user_id == user_id,
            CoachingBullet.is_active.is_(True),
        ).order_by(CoachingBullet.updated_at.desc()).limit(50)
    )
    bullets = list(result.scalars())

    if not bullets:
        # Fallback to legacy playbook
        fallback = _read_legacy_playbook(user_id)
        return (fallback, [])

    now = datetime.now(timezone.utc)
    scored = [
        (b, relevance_score(b, counterpart_archetype, elm_state, context, now))
        for b in bullets
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    selected = scored[:max_bullets]
    bullet_ids = [b.id for b, _ in selected]

    # Group by category for readability
    by_cat: dict[str, list[str]] = {}
    word_count = 0
    for b, _ in selected:
        if word_count >= max_words:
            break
        cat = b.category.upper()
        text = b.content
        evidence = f" (confirmed {b.evidence_count}x)" if b.evidence_count > 1 else ""
        entry = f"{text}{evidence}"
        words_in_entry = len(entry.split())
        if word_count + words_in_entry > max_words:
            break
        by_cat.setdefault(cat, []).append(entry)
        word_count += words_in_entry

    if not by_cat:
        return ("", bullet_ids)

    lines = []
    for cat, entries in by_cat.items():
        lines.append(f"{cat}: {' | '.join(entries)}")

    formatted = "YOUR COACHING PLAYBOOK (learned from prior sessions):\n" + "\n".join(lines)
    return (formatted, bullet_ids)


def _read_legacy_playbook(user_id: str) -> str:
    """Read the old-format markdown playbook for fallback."""
    path = _PLAYBOOK_DIR / f"{user_id}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if "No patterns recorded yet" in text:
        return ""
    # Cap at max words
    words = text.split()
    if len(words) > _MAX_CONTEXT_WORDS:
        text = " ".join(words[:_MAX_CONTEXT_WORDS]) + "…"
    return f"YOUR COACHING PLAYBOOK (learned from prior sessions):\n{text}"


# ---------------------------------------------------------------------------
# Curator — deterministic merge (NO LLM)
# ---------------------------------------------------------------------------

async def curator_merge(
    db: AsyncSession,
    user_id: str,
    session_id: str,
    deltas: list[dict[str, Any]],
) -> int:
    """
    Deterministically merge Reflector delta entries into the bullet store.

    Returns the number of bullets affected (created + updated).
    """
    affected = 0

    for delta in deltas[:_MAX_DELTAS_PER_SESSION]:
        action = delta.get("action", "").lower()
        if action == "new":
            affected += await _merge_new(db, user_id, session_id, delta)
        elif action == "reinforce":
            affected += await _merge_reinforce(db, user_id, session_id, delta)
        elif action == "contradict":
            affected += await _merge_contradict(db, session_id, delta)
        else:
            logger.warning("Unknown delta action: %s", action)

    # Enforce bullet cap
    await _enforce_cap(db, user_id)

    return affected


async def _merge_new(
    db: AsyncSession, user_id: str, session_id: str, delta: dict,
) -> int:
    content = delta.get("content", "").strip()
    if not content:
        return 0

    dedup = compute_dedup_key(content)

    # Check for duplicate
    existing = await db.execute(
        select(CoachingBullet).where(
            CoachingBullet.user_id == user_id,
            CoachingBullet.dedup_key == dedup,
            CoachingBullet.is_active.is_(True),
        ).limit(1)
    )
    dupe = existing.scalar_one_or_none()

    if dupe is not None:
        # Implicit reinforce
        dupe.helpful_count += 1
        dupe.evidence_count += 1
        dupe.last_evidence_session_id = session_id
        dupe.updated_at = datetime.now(timezone.utc)
        # Update content if Reflector provided a refined version
        if content and len(content) > len(dupe.content) * 0.5:
            dupe.content = content
        return 1

    # Insert new bullet
    now = datetime.now(timezone.utc)
    bullet = CoachingBullet(
        user_id=user_id,
        content=content,
        category=delta.get("category", "effective"),
        helpful_count=0,
        harmful_count=0,
        counterpart_archetype=delta.get("counterpart_archetype"),
        elm_state=delta.get("elm_state"),
        context=delta.get("context"),
        user_archetype=delta.get("user_archetype"),
        source_session_id=session_id,
        last_evidence_session_id=session_id,
        evidence_count=1,
        dedup_key=dedup,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(bullet)
    return 1


async def _merge_reinforce(
    db: AsyncSession, user_id: str, session_id: str, delta: dict,
) -> int:
    bullet_id = delta.get("bullet_id")
    if not bullet_id:
        # Treat as new
        return await _merge_new(db, user_id, session_id, {**delta, "action": "new"})

    bullet = await db.get(CoachingBullet, bullet_id)
    if bullet is None or not bullet.is_active:
        # Bullet retired or missing — treat as new
        return await _merge_new(db, user_id, session_id, {**delta, "action": "new"})

    bullet.helpful_count += 1
    bullet.evidence_count += 1
    bullet.last_evidence_session_id = session_id
    bullet.updated_at = datetime.now(timezone.utc)

    # Update content if Reflector refined it
    new_content = delta.get("content", "").strip()
    if new_content:
        bullet.content = new_content

    return 1


async def _merge_contradict(
    db: AsyncSession, session_id: str, delta: dict,
) -> int:
    bullet_id = delta.get("bullet_id")
    if not bullet_id:
        return 0

    bullet = await db.get(CoachingBullet, bullet_id)
    if bullet is None or not bullet.is_active:
        return 0

    bullet.harmful_count += 1
    bullet.last_evidence_session_id = session_id
    bullet.updated_at = datetime.now(timezone.utc)

    # Retire if threshold exceeded
    if bullet.harmful_count >= bullet.helpful_count + _RETIRE_THRESHOLD_MARGIN:
        bullet.is_active = False
        bullet.retired_reason = "contradicted"

    return 1


async def _enforce_cap(db: AsyncSession, user_id: str) -> None:
    """Retire lowest-scoring bullets if count exceeds cap."""
    result = await db.execute(
        select(CoachingBullet).where(
            CoachingBullet.user_id == user_id,
            CoachingBullet.is_active.is_(True),
        ).order_by(
            # Sort by net score ascending so worst bullets are first
            (CoachingBullet.helpful_count - CoachingBullet.harmful_count).asc(),
            CoachingBullet.updated_at.asc(),
        )
    )
    active = list(result.scalars())

    excess = len(active) - _MAX_ACTIVE_BULLETS
    if excess <= 0:
        return

    for bullet in active[:excess]:
        bullet.is_active = False
        bullet.retired_reason = "cap_exceeded"


# ---------------------------------------------------------------------------
# Feedback — update counters from prompt effectiveness
# ---------------------------------------------------------------------------

async def update_bullet_feedback(
    db: AsyncSession,
    bullet_ids_csv: str | None,
    effectiveness_score: float | None,
) -> None:
    """
    Update helpful/harmful counters on bullets based on prompt effectiveness.

    Called at session end for each prompt that has an effectiveness score.
    """
    if not bullet_ids_csv or effectiveness_score is None:
        return

    bullet_ids = [bid.strip() for bid in bullet_ids_csv.split(",") if bid.strip()]
    if not bullet_ids:
        return

    now = datetime.now(timezone.utc)

    for bid in bullet_ids:
        bullet = await db.get(CoachingBullet, bid)
        if bullet is None or not bullet.is_active:
            continue

        if effectiveness_score > _EFF_HELPFUL_THRESHOLD:
            bullet.helpful_count += 1
        elif effectiveness_score < _EFF_HARMFUL_THRESHOLD:
            bullet.harmful_count += 1
            # Check retirement
            if bullet.harmful_count >= bullet.helpful_count + _RETIRE_THRESHOLD_MARGIN:
                bullet.is_active = False
                bullet.retired_reason = "contradicted"

        bullet.updated_at = now


# ---------------------------------------------------------------------------
# Reflector — extract lessons from session evidence (Opus, background)
# ---------------------------------------------------------------------------

_REFLECTOR_PROMPT = """\
You are the REFLECTOR in a coaching system. Your job is to extract lessons \
from a coaching session and produce structured delta entries.

CURRENT BULLET STORE (existing insights with IDs):
{bullets}

NEW SESSION EVIDENCE:
{evidence}

INSTRUCTIONS:
Produce a JSON array of delta entries. Each entry is one of:
1. NEW — a new insight not covered by existing bullets
2. REINFORCE — an existing bullet confirmed by new evidence (reference its ID)
3. CONTRADICT — an existing bullet contradicted by new evidence (reference its ID)

Schema for each entry:
{{
  "action": "new" | "reinforce" | "contradict",
  "bullet_id": null (for new) | "existing-bullet-uuid" (for reinforce/contradict),
  "content": "The insight text (≤60 words, specific and actionable)",
  "category": "effective" | "ineffective" | "pairing" | "trend" | "tactic",
  "counterpart_archetype": "Architect" | "Firestarter" | "Inquisitor" | "Bridge Builder" | null,
  "elm_state": "ego_threat" | "shortcut" | "consensus_protection" | null,
  "context": "board" | "team" | "1:1" | "client" | "all-hands" | null,
  "confidence": 0.0-1.0
}}

Rules:
- Each bullet should be ONE discrete insight, not a paragraph
- Maximum {max_deltas} delta entries (focus on strongest signals)
- For REINFORCE: reference the bullet_id of the bullet being confirmed
- For CONTRADICT: reference the bullet_id and explain what conflicts
- Prefer updating existing bullets over creating near-duplicates
- Output ONLY the JSON array. No explanation, no markdown fences."""


async def reflector_extract(
    user_id: str,
    user_archetype: str,
    session_summary: dict[str, Any],
    current_bullets: list[dict[str, Any]],
    session_id: str,
    *,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Reflector role: extract lessons from session evidence via Opus.

    Returns a list of delta entries (dicts), or empty list on failure.
    """
    from anthropic import AsyncAnthropic

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        logger.info("No API key — skipping reflector extraction")
        return []

    # Format bullets for the prompt
    if current_bullets:
        bullet_lines = []
        for b in current_bullets:
            line = f"[{b['id']}] ({b['category']}) {b['content']}"
            if b.get("counterpart_archetype"):
                line += f" [for {b['counterpart_archetype']}]"
            if b.get("elm_state"):
                line += f" [when {b['elm_state']}]"
            helpful = b.get("helpful_count", 0)
            harmful = b.get("harmful_count", 0)
            line += f" (helpful={helpful}, harmful={harmful})"
            bullet_lines.append(line)
        bullets_text = "\n".join(bullet_lines)
    else:
        bullets_text = "(No existing bullets — this is the first session)"

    # Format session evidence (reuse the format from coaching_memory)
    evidence_text = _format_session_evidence(user_archetype, session_summary)

    prompt = _REFLECTOR_PROMPT.format(
        bullets=bullets_text,
        evidence=evidence_text,
        max_deltas=_MAX_DELTAS_PER_SESSION,
    )

    try:
        client = AsyncAnthropic(api_key=key)
        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=30.0,
        )
        raw = response.content[0].text.strip()

        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        deltas = json.loads(raw)
        if not isinstance(deltas, list):
            logger.warning("Reflector returned non-list JSON: %s", type(deltas))
            return []

        return deltas[:_MAX_DELTAS_PER_SESSION]

    except json.JSONDecodeError as exc:
        logger.warning("Reflector returned invalid JSON: %s", exc)
        return []
    except Exception as exc:
        logger.warning("Reflector extraction failed: %s", exc)
        return []


def _format_session_evidence(user_archetype: str, summary: dict) -> str:
    """Format session data into a readable evidence block for the Reflector."""
    lines = [
        f"User archetype: {user_archetype}",
        f"Meeting context: {summary.get('context', 'unknown')}",
        f"Persuasion Score: {summary.get('persuasion_score', '?')}/100",
        f"  Timing: {summary.get('timing_score', '?')}",
        f"  Ego Safety: {summary.get('ego_safety_score', '?')}",
        f"  Convergence: {summary.get('convergence_score', '?')}",
        f"Ego threat events: {summary.get('ego_threat_events', 0)}",
        f"Talk time ratio: {summary.get('talk_time_ratio', '?')}",
        f"Total utterances: {summary.get('total_utterances', '?')}",
        "",
        "Coaching prompts and their effectiveness:",
    ]

    prompt_results = summary.get("prompt_results", [])
    if not prompt_results:
        lines.append("  (no prompts with effectiveness data)")
    else:
        for pr in prompt_results:
            eff = pr.get("effectiveness_score")
            eff_label = f"{eff:.2f}" if eff is not None else "n/a"
            before = pr.get("convergence_before")
            after = pr.get("convergence_after")
            delta = ""
            if before is not None and after is not None:
                delta = f" (convergence {before:.2f} → {after:.2f})"
            lines.append(
                f"  - [{pr.get('triggered_by', '?')}] "
                f"→ {pr.get('counterpart_archetype', '?')}: "
                f"\"{pr.get('text', '')[:80]}\" "
                f"effectiveness={eff_label}{delta}"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Migration from legacy playbook
# ---------------------------------------------------------------------------

_MIGRATION_PROMPT = """\
You are migrating an existing coaching playbook into structured bullet entries.
Parse the following playbook and extract every discrete insight as a separate entry.

PLAYBOOK:
{playbook}

Output a JSON array where each entry has:
{{
  "action": "new",
  "bullet_id": null,
  "content": "The insight text (≤60 words, specific and actionable)",
  "category": "effective" | "ineffective" | "pairing" | "trend" | "tactic",
  "counterpart_archetype": "Architect" | "Firestarter" | "Inquisitor" | "Bridge Builder" | null,
  "elm_state": "ego_threat" | "shortcut" | "consensus_protection" | null,
  "context": null,
  "confidence": 0.7
}}

Rules:
- Each bullet should be ONE discrete insight from the playbook
- Preserve all specific advice, archetype pairings, and tactical recommendations
- Do not summarize or combine insights — keep them granular
- Output ONLY the JSON array. No explanation, no markdown fences."""


async def migrate_playbook_to_bullets(
    db: AsyncSession,
    user_id: str,
    *,
    api_key: str | None = None,
) -> int:
    """
    One-time migration: parse existing playbook.md into coaching bullets.

    Returns the number of bullets created.
    """
    from anthropic import AsyncAnthropic

    path = _PLAYBOOK_DIR / f"{user_id}.md"
    if not path.exists():
        return 0

    playbook = path.read_text(encoding="utf-8")
    if "No patterns recorded yet" in playbook:
        return 0

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        logger.info("No API key — skipping playbook migration")
        return 0

    try:
        client = AsyncAnthropic(api_key=key)
        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-opus-4-6",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": _MIGRATION_PROMPT.format(playbook=playbook),
                }],
            ),
            timeout=45.0,
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        # Truncate at last complete JSON object if the response was cut off
        if raw.count("[") > raw.count("]"):
            # Find last complete object and close the array
            last_brace = raw.rfind("}")
            if last_brace > 0:
                raw = raw[:last_brace + 1] + "]"

        deltas = json.loads(raw)
        if not isinstance(deltas, list):
            logger.warning("Migration returned non-list JSON")
            return 0

        count = await curator_merge(db, user_id, "migration", deltas)
        await db.commit()

        # Rename old playbook as backup
        backup = path.with_suffix(".md.migrated")
        path.rename(backup)
        logger.info(
            "Migrated playbook for user %s: %d bullets, backup at %s",
            user_id, count, backup,
        )
        return count

    except Exception as exc:
        logger.warning("Playbook migration failed for user %s: %s", user_id, exc)
        return 0


# ---------------------------------------------------------------------------
# Full post-session pipeline (replaces update_playbook)
# ---------------------------------------------------------------------------

async def update_coaching_bullets(
    db: AsyncSession,
    user_id: str,
    user_archetype: str,
    session_id: str,
    session_summary: dict[str, Any],
    *,
    api_key: str | None = None,
) -> None:
    """
    Full ACE pipeline: check migration → Reflector → Curator.

    Called as a background task after session end (replaces update_playbook).
    """
    # Check if migration is needed (first run)
    bullet_count = await db.execute(
        select(CoachingBullet.id).where(
            CoachingBullet.user_id == user_id,
        ).limit(1)
    )
    if bullet_count.scalar_one_or_none() is None:
        await migrate_playbook_to_bullets(db, user_id, api_key=api_key)

    # Load current bullets for the Reflector
    result = await db.execute(
        select(CoachingBullet).where(
            CoachingBullet.user_id == user_id,
            CoachingBullet.is_active.is_(True),
        ).order_by(CoachingBullet.updated_at.desc()).limit(50)
    )
    current_bullets = [
        {
            "id": b.id,
            "content": b.content,
            "category": b.category,
            "helpful_count": b.helpful_count,
            "harmful_count": b.harmful_count,
            "counterpart_archetype": b.counterpart_archetype,
            "elm_state": b.elm_state,
            "context": b.context,
            "evidence_count": b.evidence_count,
        }
        for b in result.scalars()
    ]

    # Reflector: extract lessons
    deltas = await reflector_extract(
        user_id, user_archetype, session_summary,
        current_bullets, session_id,
        api_key=api_key,
    )

    if not deltas:
        logger.info("Reflector produced no deltas for session %s", session_id)
        return

    # Curator: merge deterministically
    affected = await curator_merge(db, user_id, session_id, deltas)
    await db.commit()

    logger.info(
        "ACE update for user %s session %s: %d deltas → %d bullets affected",
        user_id, session_id, len(deltas), affected,
    )
