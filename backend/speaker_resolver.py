"""
LLM-based speaker resolution — maps diarized speaker labels to real names.

During a live session, Deepgram assigns opaque labels like ``counterpart_0``,
``counterpart_1`` to speakers on the system audio stream.  This module
periodically asks Claude to map those labels to real names using:

1. Calendar roster (known attendees)
2. Transcript context (self-identification, direct address, role indicators)
3. Cross-session participant database (names from past meetings)

The resolver runs as a background asyncio task, invoking Claude every
``interval_s`` seconds with the accumulated transcript.  High-confidence
mappings are locked so they don't flip-flop.

Usage
─────
    resolver = SpeakerResolver(
        anthropic_client=AsyncAnthropic(),
        known_names=["Alice Chen", "Bob Smith"],
        ws_send=ws.send_json,
        on_mapping_updated=my_persist_callback,
    )
    resolver.add_utterance("counterpart_0", "I think we should review Q3...")
    await resolver.start()
    ...
    name = resolver.resolve("counterpart_0")  # "Alice Chen" or "counterpart_0"
    await resolver.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Awaitable, Callable

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

# Model used for speaker resolution — Haiku for speed, same as coaching engine.
_MODEL = "claude-haiku-4-5-20241022"

# Fuzzy name matching threshold — shared with identity.py convention.
FUZZY_MATCH_THRESHOLD = 0.85

_SYSTEM_PROMPT = """\
You are identifying speakers in a meeting transcript.

You will be given:
1. A list of known attendees (from the meeting calendar invite)
2. A transcript with speaker labels (counterpart_0, counterpart_1, etc.)

Your job: map each counterpart_N label to the most likely attendee name.

Look for these signals:
- Self-identification: "Hi, I'm Sarah" or "This is David from legal"
- Being addressed by name: "Thanks Sarah" or "Sarah, what do you think?"
- Role/title references: "As the engineering lead..." (maps to known attendees' roles)
- Topic expertise: consistent ownership of a domain topic
- Meeting structure: first speaker after pleasantries is often the organizer

Respond ONLY with valid JSON (no markdown, no explanation):
{
  "mappings": [
    {"speaker_id": "counterpart_0", "name": "Alice Chen", "confidence": 0.85, "evidence": "addressed as Alice at turn 5"},
    {"speaker_id": "counterpart_1", "name": "Bob Smith", "confidence": 0.4, "evidence": "unclear, possibly Bob based on topic"}
  ]
}

Rules:
- confidence is 0.0–1.0 (only use ≥0.8 for strong evidence like direct naming)
- If you cannot determine a mapping, omit that speaker from the array
- Only use names from the known attendees list — do not invent names
- If no attendees list is provided, look for self-identification only
"""


class SpeakerResolver:
    """Periodic LLM-based speaker-to-name resolution."""

    def __init__(
        self,
        *,
        anthropic_client: AsyncAnthropic | None = None,
        known_names: list[str] | None = None,
        interval_s: float = 15.0,
        confidence_threshold: float = 0.7,
        lock_threshold: float = 0.8,
        ws_send: Callable[..., Awaitable[None]] | None = None,
        on_mapping_updated: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._client = anthropic_client or AsyncAnthropic()
        # Filter out None/empty values from known_names
        self._known_names = [n for n in (known_names or []) if n]
        self._interval = interval_s
        self._threshold = confidence_threshold
        self._lock_threshold = lock_threshold
        self._ws_send = ws_send
        self._on_mapping_updated = on_mapping_updated

        self._transcript: list[dict[str, str]] = []
        self._mappings: dict[str, str] = {}
        self._confidences: dict[str, float] = {}
        self._locked: set[str] = set()  # speaker_ids with locked mappings

        self._task: asyncio.Task | None = None
        self._running = False
        self._last_resolved_len = 0  # track transcript length to skip no-op cycles

        # Adaptive scheduling state
        self._start_time: float | None = None  # monotonic time of first utterance

        # Resolver accuracy metrics (written at session end)
        self._metrics = {
            "total_resolutions": 0,
            "user_corrections": 0,
            "time_to_first_resolution": None,
            "locked_at_end": 0,
            "turn_tracker_agreements": 0,
            "turn_tracker_disagreements": 0,
        }

        # Voiceprint boost: set via set_voiceprint_data() from main.py
        self._voiceprint_similarities: dict[str, tuple[str, float]] = {}
        # speaker_id → (matched_name, cosine_similarity)

        # Turn tracker boost: set via set_turn_tracker_scores() from main.py
        self._turn_tracker_scores: dict[str, dict[str, float]] = {}
        # speaker_id → {name: score}

        # Track which speaker_ids already counted for turn tracker metrics
        # to avoid overcounting across resolution cycles.
        self._tt_counted: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_utterance(self, speaker_id: str, text: str) -> None:
        """Buffer a transcribed utterance for the next resolution cycle."""
        if self._start_time is None:
            self._start_time = time.monotonic()
        self._transcript.append({"speaker": speaker_id, "text": text})

    async def start(self) -> None:
        """Start the periodic resolution background loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        """Stop the resolution loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def resolve(self, speaker_id: str) -> str:
        """Return the resolved name for a speaker_id, or the ID itself."""
        return self._mappings.get(speaker_id, speaker_id)

    def set_confirmed_name(self, speaker_id: str, name: str) -> None:
        """User confirmed or edited a name — lock it permanently."""
        # Track corrections (different name than current mapping)
        existing = self._mappings.get(speaker_id)
        if existing is not None and existing != name:
            self._metrics["user_corrections"] += 1
        self._mappings[speaker_id] = name
        self._confidences[speaker_id] = 1.0
        self._locked.add(speaker_id)

    def set_voiceprint_match(
        self, speaker_id: str, matched_name: str, similarity: float,
    ) -> None:
        """Inject a voiceprint match for use in the next resolution cycle.

        Called from main.py when a speaker's embedding matches a known
        participant's voiceprint centroid with similarity > 0.7.
        """
        self._voiceprint_similarities[speaker_id] = (matched_name, similarity)

    def set_turn_tracker_scores(
        self, scores: dict[str, dict[str, float]],
    ) -> None:
        """Inject turn tracker vocative link scores for the next resolution cycle.

        Called from main.py before each resolution cycle with the output of
        TurnTracker.get_name_scores().
        """
        self._turn_tracker_scores = scores

    @property
    def mappings(self) -> dict[str, str]:
        """Current speaker_id → name mappings (copy)."""
        return dict(self._mappings)

    @property
    def confidences(self) -> dict[str, float]:
        """Current speaker_id → confidence mappings (copy)."""
        return dict(self._confidences)

    @property
    def metrics(self) -> dict:
        """Resolver accuracy metrics for this session (copy)."""
        m = dict(self._metrics)
        m["locked_at_end"] = len(self._locked)
        return m

    def _current_interval(self) -> float:
        """Adaptive interval based on meeting phase."""
        if self._start_time is None:
            return self._interval
        elapsed = time.monotonic() - self._start_time
        if elapsed < 120:
            return 10.0  # Intro phase: aggressive
        if self._mappings and all(sid in self._locked for sid in self._mappings):
            return 60.0  # All locked: coast
        return self._interval  # Default: 15s

    # ------------------------------------------------------------------
    # DB pre-seeding
    # ------------------------------------------------------------------

    @staticmethod
    async def load_known_names_from_db(
        db_session_factory: Callable,
        user_id: str,
    ) -> list[str]:
        """Load participant names from past sessions (last 90 days) for this user."""
        try:
            from sqlalchemy import select

            from backend.models import Participant

            async with db_session_factory() as db:
                cutoff = datetime.now(timezone.utc) - timedelta(days=90)
                result = await db.execute(
                    select(Participant.name).where(
                        Participant.user_id == user_id,
                        Participant.name.isnot(None),
                        Participant.updated_at >= cutoff,
                    )
                )
                return [row[0] for row in result.fetchall() if row[0]]
        except Exception:
            logger.warning("SpeakerResolver: failed to load names from DB")
            return []

    # ------------------------------------------------------------------
    # Fuzzy name matching
    # ------------------------------------------------------------------

    def _fuzzy_match_name(self, name: str) -> str | None:
        """Return best matching known name, or None if no match >= threshold."""
        if not self._known_names:
            return None
        best_name: str | None = None
        best_ratio = 0.0
        normalized = name.strip().lower()
        for known in self._known_names:
            ratio = SequenceMatcher(None, normalized, known.lower()).ratio()
            if ratio >= FUZZY_MATCH_THRESHOLD and ratio > best_ratio:
                best_ratio = ratio
                best_name = known
        if best_name is not None:
            logger.debug(
                "SpeakerResolver: fuzzy matched %r → %r (ratio=%.3f)",
                name, best_name, best_ratio,
            )
        return best_name

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Run resolution at adaptive intervals while active."""
        while self._running:
            await asyncio.sleep(self._current_interval())
            if not self._running:
                break
            # Need enough context for meaningful inference
            if len(self._transcript) < 5:
                continue
            # Skip if no new utterances since last cycle
            current_len = len(self._transcript)
            if current_len == self._last_resolved_len:
                continue
            self._last_resolved_len = current_len
            try:
                await self._resolve_once()
            except Exception:
                logger.exception("SpeakerResolver: resolution cycle failed")

    async def _resolve_once(self) -> None:
        """Send transcript + roster to Claude, parse response, update mappings."""
        # Build transcript text: first 20 + last 80 utterances to preserve
        # early introductions ("Hi, I'm Sarah") in long meetings.
        n = len(self._transcript)
        if n <= 100:
            recent = list(self._transcript)
        else:
            recent = self._transcript[:20] + self._transcript[-80:]

        transcript_text = "\n".join(
            f'{u["speaker"]}: {u["text"]}' for u in recent
        )

        attendees_text = (
            ", ".join(self._known_names) if self._known_names
            else "(no attendee list available — rely on self-identification only)"
        )

        user_prompt = (
            f"Known attendees: {attendees_text}\n\n"
            f"Transcript:\n{transcript_text}"
        )

        response = await self._client.messages.create(
            model=_MODEL,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Guard against empty content array
        if not response.content:
            logger.warning("SpeakerResolver: empty content in LLM response")
            return

        # Parse the response
        text = response.content[0].text.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("SpeakerResolver: failed to parse LLM response: %s", text[:200])
            return

        mappings_list = data.get("mappings", [])
        if not isinstance(mappings_list, list):
            return
        for entry in mappings_list:
            if not isinstance(entry, dict):
                continue
            speaker_id = str(entry.get("speaker_id", "")).strip()
            name = str(entry.get("name", "")).strip()[:120]
            try:
                confidence = float(entry.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0

            if not speaker_id or not name:
                continue

            # Name validation: fuzzy match against known attendees, or validate
            # plausibility when no roster is available.
            if self._known_names:
                matched = self._fuzzy_match_name(name)
                if matched is None:
                    logger.debug(
                        "SpeakerResolver: rejected name %r — no fuzzy match in attendees",
                        name,
                    )
                    continue
                name = matched  # Use the canonical known name
            else:
                # No roster — validate name is plausible (not "speaker_0", etc.)
                try:
                    from backend.identity import is_plausible_speaker_name
                    if not is_plausible_speaker_name(name):
                        logger.debug(
                            "SpeakerResolver: rejected implausible name %r (no roster)",
                            name,
                        )
                        continue
                except ImportError:
                    pass  # identity module unavailable, skip validation

            # Skip locked mappings (user-confirmed or high-confidence)
            if speaker_id in self._locked:
                continue

            # Only apply if above threshold
            if confidence < self._threshold:
                continue

            # Confidence comparison with flip-flop guard:
            # - Same name: allow confidence to drift down (0.9 decay factor)
            # - Different name: must strictly beat existing (prevents oscillation)
            existing_name = self._mappings.get(speaker_id)
            existing_confidence = self._confidences.get(speaker_id, 0.0)
            if existing_name is not None:
                if existing_name == name:
                    if confidence < existing_confidence * 0.9:
                        continue
                else:
                    if confidence <= existing_confidence:
                        continue

            # Voiceprint confidence boost: if a voiceprint match agrees with
            # the LLM mapping, boost confidence by 0.15.
            _boosted = False
            vp_match = self._voiceprint_similarities.get(speaker_id)
            if vp_match is not None:
                vp_name, vp_sim = vp_match
                if vp_name == name and vp_sim > 0.7:
                    old_conf = confidence
                    confidence += 0.15
                    _boosted = True
                    logger.info(
                        "Voiceprint boost: %s %.2f → %.2f (sim=%.2f)",
                        name, old_conf, confidence, vp_sim,
                    )

            # Turn tracker confidence boost: if vocative link evidence agrees
            # with the LLM mapping, boost confidence by 0.10.
            tt_scores = self._turn_tracker_scores.get(speaker_id)
            if tt_scores is not None:
                tt_score = tt_scores.get(name, 0.0)
                if tt_score > 0:
                    old_conf = confidence
                    confidence += 0.10
                    _boosted = True
                    # Count each speaker only once for kill-switch metrics.
                    if speaker_id not in self._tt_counted:
                        self._metrics["turn_tracker_agreements"] += 1
                        self._tt_counted.add(speaker_id)
                    logger.info(
                        "Turn tracker boost: %s %.2f → %.2f (score=%.2f)",
                        name, old_conf, confidence, tt_score,
                    )
                elif tt_scores:
                    # Tracker has scores but for a different name: disagreement.
                    if speaker_id not in self._tt_counted:
                        self._metrics["turn_tracker_disagreements"] += 1
                        self._tt_counted.add(speaker_id)

            # Combined cap: non-LLM boosts cannot push confidence past lock threshold.
            if _boosted:
                confidence = min(confidence, self._lock_threshold - 0.01)

            self._mappings[speaker_id] = name
            self._confidences[speaker_id] = confidence

            # Track metrics
            self._metrics["total_resolutions"] += 1
            if self._metrics["time_to_first_resolution"] is None and self._start_time is not None:
                self._metrics["time_to_first_resolution"] = round(
                    time.monotonic() - self._start_time, 1
                )

            # Lock high-confidence mappings
            if confidence >= self._lock_threshold:
                self._locked.add(speaker_id)

            logger.info(
                "SpeakerResolver: %s → %s (confidence=%.2f, evidence=%s)",
                speaker_id, name, confidence, entry.get("evidence", ""),
            )

            # Persist mapping to DB via callback
            if self._on_mapping_updated:
                try:
                    await self._on_mapping_updated(speaker_id, name, confidence)
                except Exception:
                    logger.debug("SpeakerResolver: mapping persistence callback failed")

            # Notify frontend
            if self._ws_send:
                try:
                    await self._ws_send({
                        "type": "speaker_identified",
                        "speaker_id": speaker_id,
                        "name": name,
                        "confidence": confidence,
                    })
                except Exception:
                    logger.debug("SpeakerResolver: failed to send WS notification")
