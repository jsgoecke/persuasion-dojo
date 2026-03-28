"""
Identity resolution for participant speakers.

Matches speaker names from transcripts to existing Participant records using
exact (case-insensitive) and fuzzy string matching.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Participant

# Minimum similarity ratio for fuzzy name matching
_FUZZY_THRESHOLD = 0.85

# Pattern for Deepgram-style generic speaker IDs
_SPEAKER_N_RE = re.compile(r"^speaker_\d+$", re.IGNORECASE)


async def resolve_speaker(
    db: AsyncSession,
    user_id: str,
    speaker_name: str,
) -> Participant | None:
    """
    Resolve a speaker name to an existing Participant record.

    Resolution strategy (first match wins):
      1. Exact name match (case-insensitive)
      2. Fuzzy name match (SequenceMatcher ratio >= 0.85)
      3. Return None — caller creates a new participant or defers

    Generic speaker IDs (``speaker_0``, ``speaker_1``, …) always return None
    since they carry no identity signal.
    """
    if not speaker_name or _SPEAKER_N_RE.match(speaker_name):
        return None

    normalized = speaker_name.strip().lower()

    # 1. Exact match (case-insensitive)
    result = await db.execute(
        select(Participant).where(
            Participant.user_id == user_id,
            func.lower(Participant.name) == normalized,
        )
    )
    exact = result.scalar_one_or_none()
    if exact is not None:
        return exact

    # 2. Fuzzy match — load all names for this user and find best match
    all_rows = await db.execute(
        select(Participant).where(
            Participant.user_id == user_id,
            Participant.name.isnot(None),
        )
    )
    best_match: Participant | None = None
    best_ratio = 0.0

    for (p,) in all_rows:
        ratio = SequenceMatcher(None, normalized, (p.name or "").lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = p

    if best_ratio >= _FUZZY_THRESHOLD and best_match is not None:
        return best_match

    return None
