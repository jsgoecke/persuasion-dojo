"""
Vocative-bootstrapped turn-taking tracker for speaker identification.

Extracts vocative name mentions from utterances ("Thanks Greg", "Sarah,
what do you think?") and links them to the next speaker(s) via turn
adjacency.  Provides a zero-API-cost confidence signal that accumulates
across the full session, complementing the LLM resolver's sliding window.

Usage
─────
    tracker = TurnTracker(known_names=["Greg Wilson", "Sarah Chen"])
    tracker.add_turn("counterpart_0", "Thanks Greg, that's a great point.", 10.0, 12.5)
    tracker.add_turn("counterpart_1", "Yeah I agree with that.", 12.8, 14.0)
    scores = tracker.get_name_scores()
    # {"counterpart_1": {"Greg Wilson": 0.8}}
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

logger = logging.getLogger(__name__)

# Minimum vocative links before reporting a score for a speaker.
COLD_START_THRESHOLD = 3

# Maximum gap (seconds) between speakers to count as a direct-address response.
MAX_TURN_GAP_S = 5.0

# How many subsequent speakers to check for the addressed name.
LOOKAHEAD_WINDOW = 3

# Keep at most this many turns in memory to bound growth in long meetings.
_MAX_TURNS = 2000


def _build_vocative_start(name: str) -> re.Pattern:
    escaped = re.escape(name)
    return re.compile(
        rf"^(?:hi|hey|thanks|thank\s+you|ok|okay|so|well|right|alright),?\s+{escaped}\b",
        re.IGNORECASE,
    )


def _build_vocative_end(name: str) -> re.Pattern:
    escaped = re.escape(name)
    return re.compile(rf"\b{escaped}[,.?!]?\s*$", re.IGNORECASE)


def _build_vocative_question(name: str) -> re.Pattern:
    escaped = re.escape(name)
    return re.compile(
        rf"\b{escaped},?\s+(?:what|how|can|could|would|do|did|are|is|have|has)\b",
        re.IGNORECASE,
    )


def _build_third_party(name: str) -> re.Pattern:
    escaped = re.escape(name)
    return re.compile(
        rf"\b(?:talked?\s+to|spoke\s+(?:to|with)|asked|told|emailed|called|met\s+with|from)\s+{escaped}\b"
        rf"|\b{escaped}\s+(?:said|mentioned|told|suggested|proposed|recommended|noted|pointed)\b"
        rf"|\bas\s+{escaped}\s+(?:mentioned|said|noted|pointed)\b"
        rf"|\b{escaped}'s\s+(?:point|idea|suggestion|approach|proposal)\b",
        re.IGNORECASE,
    )


class TurnTracker:
    """Vocative extraction + turn transition linking for speaker identification."""

    def __init__(self, known_names: list[str] | None = None) -> None:
        self._known_names: list[str] = [n for n in (known_names or []) if n]

        # Pre-compile regex patterns per first name (the vocative token).
        # We extract first names from full roster names for matching.
        self._first_names: dict[str, str] = {}  # first_name -> full_name
        self._vocative_start: dict[str, re.Pattern] = {}
        self._vocative_end: dict[str, re.Pattern] = {}
        self._vocative_question: dict[str, re.Pattern] = {}
        self._third_party: dict[str, re.Pattern] = {}

        # Build a case-insensitive word-boundary pattern for the early-exit check.
        # This replaces the plain `in` substring check to avoid matching substrings
        # like "Greg" in "Gregory" while still matching lowercase ASR output.
        self._name_present: dict[str, re.Pattern] = {}

        # Detect ambiguous first names (multiple roster entries share the same first name).
        first_name_counts: dict[str, int] = defaultdict(int)
        for full_name in self._known_names:
            first = full_name.split()[0] if full_name.strip() else ""
            if first:
                first_name_counts[first] += 1

        for full_name in self._known_names:
            first = full_name.split()[0] if full_name.strip() else ""
            if not first:
                continue
            # Skip ambiguous first names (multiple attendees share it).
            if first_name_counts[first] > 1:
                logger.debug(
                    "TurnTracker: skipping ambiguous first name %r (%d attendees)",
                    first, first_name_counts[first],
                )
                continue
            self._first_names[first] = full_name
            self._vocative_start[first] = _build_vocative_start(first)
            self._vocative_end[first] = _build_vocative_end(first)
            self._vocative_question[first] = _build_vocative_question(first)
            self._third_party[first] = _build_third_party(first)
            # Case-insensitive word-boundary presence check
            self._name_present[first] = re.compile(
                rf"\b{re.escape(first)}\b", re.IGNORECASE,
            )

        # Turn history: list of (speaker_id, text, start_s, end_s).
        self._turns: list[tuple[str, str, float, float]] = []

        # Vocative link counts: speaker_id -> {full_name: count}.
        self._vocative_links: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Dedup set: (prev_idx, full_name, current_sid) to avoid double-counting.
        self._credited: set[tuple[int, str, str]] = set()

        # Track offset when turns are pruned so _credited keys stay valid.
        self._turn_offset = 0

    def add_turn(
        self,
        speaker_id: str,
        text: str,
        start_s: float = 0.0,
        end_s: float = 0.0,
    ) -> None:
        """Process a new utterance: extract vocative cues and link to subsequent speakers."""
        self._turns.append((speaker_id, text, start_s, end_s))

        # Prune old turns to bound memory in long meetings.
        if len(self._turns) > _MAX_TURNS:
            prune_count = len(self._turns) - _MAX_TURNS
            self._turns = self._turns[prune_count:]
            self._turn_offset += prune_count
            # Evict stale _credited entries referencing pruned indices.
            min_valid = self._turn_offset
            self._credited = {
                k for k in self._credited if k[0] >= min_valid
            }

        # Process PREVIOUS turns that might contain vocative cues addressing
        # this new speaker. We check the last few turns before this one.
        self._link_pending_vocatives()

    def get_name_scores(self) -> dict[str, dict[str, float]]:
        """Return {speaker_id: {name: score}} for speakers with >= COLD_START_THRESHOLD links.

        Scores are normalized to [0, 1] per speaker.
        """
        result: dict[str, dict[str, float]] = {}
        for speaker_id, name_counts in self._vocative_links.items():
            # Filter to names meeting cold start threshold.
            qualified = {n: c for n, c in name_counts.items() if c >= COLD_START_THRESHOLD}
            if not qualified:
                continue
            max_count = max(qualified.values())
            if max_count == 0:
                continue
            result[speaker_id] = {
                name: count / max_count for name, count in qualified.items()
            }
        return result

    def _link_pending_vocatives(self) -> None:
        """Check recent turns for vocative cues that address the current (latest) speaker."""
        if len(self._turns) < 2:
            return

        current_local_idx = len(self._turns) - 1
        current_abs_idx = current_local_idx + self._turn_offset
        current_sid, _, current_start, _ = self._turns[current_local_idx]

        # Look back at turns that might contain a vocative cue for the current speaker.
        # Check up to LOOKAHEAD_WINDOW turns before this one.
        start_local = max(0, current_local_idx - LOOKAHEAD_WINDOW)
        for prev_local in range(start_local, current_local_idx):
            prev_abs = prev_local + self._turn_offset
            prev_sid, prev_text, _, prev_end = self._turns[prev_local]

            # Don't link a speaker to themselves.
            if prev_sid == current_sid:
                continue

            # Timestamp gap filter: skip if gap > MAX_TURN_GAP_S.
            if current_start > 0 and prev_end > 0:
                gap = current_start - prev_end
                if gap > MAX_TURN_GAP_S:
                    continue

            # Extract vocative names from the previous turn's text.
            vocative_names = self._extract_vocatives(prev_text)
            if not vocative_names:
                continue

            for full_name in vocative_names:
                # Only credit once per (prev_turn, name, target_speaker).
                turn_key = (prev_abs, full_name, current_sid)
                if turn_key in self._credited:
                    continue
                self._credited.add(turn_key)
                self._vocative_links[current_sid][full_name] += 1
                logger.debug(
                    "TurnTracker: vocative link %s → %s (speaker %s, count=%d)",
                    prev_sid, full_name, current_sid,
                    self._vocative_links[current_sid][full_name],
                )

    def _extract_vocatives(self, text: str) -> list[str]:
        """Extract vocative name mentions from text, filtering third-party references.

        Returns list of full roster names found as vocative cues.
        Uses case-insensitive word-boundary check so both "Greg" and "greg"
        (common in ASR output) are detected, while "Gregory" is rejected.
        """
        results: list[str] = []
        for first_name, full_name in self._first_names.items():
            # Case-insensitive word-boundary presence check.
            if not self._name_present[first_name].search(text):
                continue

            # Third-party reference filter: skip if this is a back-reference.
            if self._third_party[first_name].search(text):
                continue

            # Check vocative patterns.
            if (
                self._vocative_start[first_name].search(text)
                or self._vocative_end[first_name].search(text)
                or self._vocative_question[first_name].search(text)
            ):
                results.append(full_name)

        return results
