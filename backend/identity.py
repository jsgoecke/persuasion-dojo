"""
Identity resolution for participant speakers.

Matches speaker names from transcripts to existing Participant records using
exact (case-insensitive) and fuzzy string matching. Includes name validation
to prevent non-name strings (system messages, technical terms) from becoming
participant records.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Participant

# Minimum similarity ratio for fuzzy name matching
_FUZZY_THRESHOLD = 0.85

# Patterns for generic speaker IDs that carry no identity signal
_SPEAKER_N_RE = re.compile(r"^(speaker|counterpart)_\d+$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Name validation — single source of truth
# ---------------------------------------------------------------------------

# Words that appear in system/technical context, never as real speaker names.
# Lowercase for comparison.
_BLOCKLIST_WORDS = frozenset({
    # Technical / system terms
    "kill switch", "killswitch", "timeout", "prompt timeout", "system",
    "system message", "error", "warning", "debug", "info", "log",
    "timestamp", "recording", "transcript", "audio", "video",
    "meeting", "session", "note", "notes", "action item", "action items",
    "summary", "agenda", "minutes", "follow up", "follow-up",
    # UI / app artifacts
    "coaching prompt", "coaching", "prompt", "overlay", "notification",
    "fallback", "cached", "retry", "reconnect", "disconnect",
    "connecting", "connected", "disconnected", "loading", "processing",
    # File / format artifacts
    "webvtt", "kind", "language", "captions", "subtitles",
    "description", "chapters", "metadata", "header", "footer",
    # Generic non-names
    "unknown", "unnamed", "anonymous", "unidentified", "moderator",
    "host", "co-host", "presenter", "organizer", "attendee",
    "everyone", "all", "group", "team", "channel",
    "you", "me", "i", "we", "they",
})

# A plausible name: 1-4 words, each word starts with a letter, total 2-60 chars.
# Allows names like "Sarah", "Jean-Pierre", "Sarah Chen", "Dr. Smith",
# "María José García", "O'Brien"
_NAME_WORD_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ]")
_MAX_NAME_WORDS = 5
_MIN_NAME_LEN = 2
_MAX_NAME_LEN = 60


def is_plausible_speaker_name(name: str) -> bool:
    """
    Return True if the string looks like a real person's name.

    Rejects:
    - Empty / whitespace-only strings
    - Generic speaker IDs (speaker_0, counterpart_1)
    - Known technical / system terms (blocklist)
    - Strings that don't look like names (too many words, starts with
      digits/symbols, contains suspicious characters)

    This is the single gatekeeper that prevents garbage from becoming
    Participant records. Used by both the transcript parser and the
    identity resolution layer.
    """
    if not name or not name.strip():
        return False

    cleaned = name.strip()

    # Generic speaker IDs
    if _SPEAKER_N_RE.match(cleaned):
        return False

    # Length bounds
    if len(cleaned) < _MIN_NAME_LEN or len(cleaned) > _MAX_NAME_LEN:
        return False

    # Blocklist (exact match, case-insensitive)
    if cleaned.lower() in _BLOCKLIST_WORDS:
        return False

    # Must not contain characters that never appear in names
    # (brackets, braces, equals, pipes, backslashes, angle brackets, slashes)
    if re.search(r'[{}\[\]|\\<>=/@#$%^&*~`]', cleaned):
        return False

    # Must not start with a digit (timestamps, line numbers)
    if cleaned[0].isdigit():
        return False

    # Must not be ALL CAPS single word over 6 chars (likely an acronym or label)
    words = cleaned.split()
    if len(words) == 1 and len(cleaned) > 6 and cleaned.isupper():
        return False

    # Too many words — real names rarely exceed 4-5 words
    if len(words) > _MAX_NAME_WORDS:
        return False

    # At least one word should start with a letter
    if not any(_NAME_WORD_RE.match(w) for w in words):
        return False

    # Reject if it looks like a sentence (contains common verbs/articles as
    # standalone words in positions that names wouldn't have them)
    lower_words = [w.lower().rstrip(".,;:!?") for w in words]
    _SENTENCE_MARKERS = {"the", "a", "an", "is", "are", "was", "were", "has",
                         "have", "had", "will", "can", "should", "would",
                         "could", "this", "that", "these", "those", "it",
                         "its", "not", "no", "yes", "and", "or", "but",
                         "for", "with", "from", "into", "onto", "upon",
                         "about", "after", "before", "during", "between"}
    # If more than half the words are sentence markers, it's not a name
    marker_count = sum(1 for w in lower_words if w in _SENTENCE_MARKERS)
    if len(words) >= 2 and marker_count > len(words) / 2:
        return False

    return True


def is_generic_speaker_id(name: str) -> bool:
    """Return True if the name is a generic speaker/counterpart ID (e.g. speaker_0)."""
    return bool(_SPEAKER_N_RE.match(name)) if name else False


async def resolve_speaker(
    db: AsyncSession,
    user_id: str,
    speaker_name: str,
) -> Participant | None:
    """
    Resolve a speaker name to an existing Participant record.

    Resolution strategy (first match wins):
      1. Reject if not a plausible speaker name
      2. Exact name match (case-insensitive)
      3. Fuzzy name match (SequenceMatcher ratio >= 0.85)
      4. Return None — caller creates a new participant or defers

    Generic speaker IDs (``speaker_0``, ``speaker_1``, …) and non-name
    strings always return None.
    """
    if not speaker_name or not is_plausible_speaker_name(speaker_name):
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
