"""
LLM-based speaker resolution — maps diarized speaker labels to real names.

During a live session, Deepgram assigns opaque labels like ``counterpart_0``,
``counterpart_1`` to speakers on the system audio stream.  This module
periodically asks Claude to map those labels to real names using:

1. Calendar roster (known attendees)
2. Transcript context (self-identification, direct address, role indicators)

The resolver runs as a background asyncio task, invoking Claude every
``interval_s`` seconds with the accumulated transcript.  High-confidence
mappings are locked so they don't flip-flop.

Usage
─────
    resolver = SpeakerResolver(
        anthropic_client=AsyncAnthropic(),
        known_names=["Alice Chen", "Bob Smith"],
        ws_send=ws.send_json,
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
from typing import Awaitable, Callable

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

# Model used for speaker resolution — Haiku for speed, same as coaching engine.
_MODEL = "claude-haiku-4-5-20241022"

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
        interval_s: float = 60.0,
        confidence_threshold: float = 0.7,
        lock_threshold: float = 0.8,
        ws_send: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._client = anthropic_client or AsyncAnthropic()
        self._known_names = known_names or []
        self._interval = interval_s
        self._threshold = confidence_threshold
        self._lock_threshold = lock_threshold
        self._ws_send = ws_send

        self._transcript: list[dict[str, str]] = []
        self._mappings: dict[str, str] = {}
        self._confidences: dict[str, float] = {}
        self._locked: set[str] = set()  # speaker_ids with locked mappings

        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_utterance(self, speaker_id: str, text: str) -> None:
        """Buffer a transcribed utterance for the next resolution cycle."""
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
        self._mappings[speaker_id] = name
        self._confidences[speaker_id] = 1.0
        self._locked.add(speaker_id)

    @property
    def mappings(self) -> dict[str, str]:
        """Current speaker_id → name mappings (copy)."""
        return dict(self._mappings)

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Run resolution every interval_s while active."""
        while self._running:
            await asyncio.sleep(self._interval)
            if not self._running:
                break
            # Need enough context for meaningful inference
            if len(self._transcript) < 5:
                continue
            try:
                await self._resolve_once()
            except Exception:
                logger.exception("SpeakerResolver: resolution cycle failed")

    async def _resolve_once(self) -> None:
        """Send transcript + roster to Claude, parse response, update mappings."""
        # Build transcript text (last 100 utterances for context window)
        recent = self._transcript[-100:]
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

            # When a known attendees list exists, only accept names from it
            if self._known_names and name not in self._known_names:
                logger.debug("SpeakerResolver: rejected name %r — not in attendees list", name)
                continue

            # Skip locked mappings (user-confirmed or high-confidence)
            if speaker_id in self._locked:
                continue

            # Only apply if above threshold
            if confidence < self._threshold:
                continue

            # Only update if confidence improved or this is a new mapping
            existing_confidence = self._confidences.get(speaker_id, 0.0)
            if confidence < existing_confidence:
                continue

            self._mappings[speaker_id] = name
            self._confidences[speaker_id] = confidence

            # Lock high-confidence mappings
            if confidence >= self._lock_threshold:
                self._locked.add(speaker_id)

            logger.info(
                "SpeakerResolver: %s → %s (confidence=%.2f, evidence=%s)",
                speaker_id, name, confidence, entry.get("evidence", ""),
            )

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
