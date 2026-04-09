"""
Real-time coaching engine — Claude Haiku prompt generation.

Three-layer coaching architecture (evaluated each trigger):
  Layer 1 (Self):     Is the user communicating in the right mode for this moment?
  Layer 2 (Audience): Who is this participant and what do they need right now?
  Layer 3 (Group):    When to push, yield, or invite contribution from the group?

Priority and cadence floors
────────────────────────────
  ELM-triggered (ego_threat / consensus_protection / shortcut)  10 s floor
  General cadence (self / group)                                 15 s floor

ELM prompts suppressed while user_is_speaking (audience-layer needs
counterpart context). Self-layer general prompts fire on user utterances
too — this is how "you've been advocating too long, ask a question"
works.

Fallback
─────────
If Claude Haiku exceeds haiku_timeout_s (default 1.5 s) the engine returns
the last successfully generated CoachingPrompt for that layer, with
is_fallback=True.  The overlay renders a subtle "↻ cached" badge.
If no cached prompt exists yet, None is returned and no prompt is shown.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Literal

from anthropic import AsyncAnthropic

from backend.coaching_memory import get_coaching_context as _get_legacy_coaching_context
from backend.elm_detector import ELMEvent
from backend.models import ProfileSnapshot
from backend.profiler import WindowClassification


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

CoachingLayer = Literal["self", "audience", "group"]

_SYSTEM_PROMPT = (
    "You are a $500/hr executive communication coach embedded in a live meeting overlay. "
    "You use the Communicator Superpower framework:\n"
    "- Architect: needs data, structure, evidence before moving.\n"
    "- Firestarter: leads with energy, story, vision.\n"
    "- Inquisitor: challenges everything, needs proof.\n"
    "- Bridge Builder: reads the room, builds consensus.\n\n"
    "Output EXACTLY ONE coaching tip in plain, simple English. "
    "No jargon, no academic terms, no framework labels. "
    "Write like you're texting a friend quick advice during a meeting.\n\n"
    "Format: a short WHY clause (≤8 words, "
    "naming the specific person) followed by a dash and the ACTION (≤12 words, verb-first imperative). "
    "Always name the specific person in your tip when a name is provided. "
    "Example: 'Sarah needs proof — lead with a specific number.'\n"
    "Example: 'Mike is getting defensive — acknowledge his point first, then redirect.'\n"
    "Example: 'The group is going along to get along — ask what concerns haven't been raised.'\n"
    "No preamble, no labels, no quotes. Never use terms like 'ego safety', "
    "'peripheral route', 'central route', 'ELM', 'cognitive load', or 'processing mode'. "
    "Output only the tip."
)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 80   # ~25 words (why clause + action), with headroom
_FLEX_NOTE_ENABLED = True  # Killswitch: set False to suppress flexibility notes

# Human-readable labels for ELM states used in prompts
_ELM_STATE_DESCRIPTION: dict[str, str] = {
    "ego_threat": "getting defensive, feels personally attacked",
    "shortcut": "nodding along but not actually engaged",
    "consensus_protection": "shutting down disagreement too early",
}

_ELM_COACHING_GOAL: dict[str, str] = {
    "ego_threat": "make them feel heard so they can think clearly again",
    "shortcut": "get them to share what they really think",
    "consensus_protection": "make it safe for someone to disagree",
}


# ---------------------------------------------------------------------------
# Archetype pairing advice
# ---------------------------------------------------------------------------

_ARCHETYPE_PAIRING: dict[tuple[str, str], str] = {
    # Firestarter coaching other types
    ("Firestarter", "Architect"): "They need data and structure — anchor your energy in specific numbers and a clear framework",
    ("Firestarter", "Inquisitor"): "They'll challenge you — welcome their questions and back your vision with evidence",
    ("Firestarter", "Bridge Builder"): "They're reading the room — slow down, check in with the group, and build on their consensus",
    ("Firestarter", "Firestarter"): "Two high-energy advocates — take turns, acknowledge their ideas before adding yours",
    # Architect coaching other types
    ("Architect", "Firestarter"): "They lead with energy and story — connect your data to their vision, don't just correct",
    ("Architect", "Inquisitor"): "They want evidence too — align on shared data points and build logical agreement",
    ("Architect", "Bridge Builder"): "They value harmony — frame your analysis as supporting the group, not challenging it",
    ("Architect", "Architect"): "Two data-driven thinkers — agree on the framework first, then debate the details",
    # Inquisitor coaching other types
    ("Inquisitor", "Firestarter"): "They lead with passion — ask questions that sharpen their idea rather than deflating it",
    ("Inquisitor", "Architect"): "They want structure — frame your questions as building on their framework, not tearing it down",
    ("Inquisitor", "Bridge Builder"): "They seek consensus — turn your challenges into inclusive questions the group can explore",
    ("Inquisitor", "Inquisitor"): "Two questioners — propose a direction to avoid analysis paralysis",
    # Bridge Builder coaching other types
    ("Bridge Builder", "Firestarter"): "They have big energy — validate their vision and gently weave in other voices",
    ("Bridge Builder", "Architect"): "They want proof — show you've heard the data and connect it to the group's needs",
    ("Bridge Builder", "Inquisitor"): "They need to probe — create space for their questions while keeping momentum",
    ("Bridge Builder", "Bridge Builder"): "Two consensus seekers — someone needs to advocate, take a gentle stand",
}


def _archetype_pairing_advice(user_type: str, counterpart_type: str) -> str:
    """Return specific advice for how user_type should communicate with counterpart_type."""
    advice = _ARCHETYPE_PAIRING.get((user_type, counterpart_type))
    if advice:
        return advice
    # Fallback for unknown types
    if counterpart_type == "Unknown" or user_type == "Unknown":
        return "Listen actively and mirror their communication style"
    return f"Adapt your {user_type} style to connect with their {counterpart_type} approach"


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

@dataclass
class CoachingPrompt:
    """
    A single coaching prompt surfaced to the overlay.

    layer:        which coaching layer this addresses
    text:         the tip shown to the user (≤18 words)
    is_fallback:  True when served from cache because Haiku timed out or errored
    triggered_by: "elm:ego_threat" | "elm:shortcut" | "elm:consensus_protection"
                  | "cadence:self" | "cadence:group"
    speaker_id:   counterpart speaker ID (audience layer only; "" otherwise)
    bullet_ids_used: comma-separated IDs of coaching bullets in the prompt context
    """
    layer: CoachingLayer
    text: str
    is_fallback: bool = False
    triggered_by: str = ""
    speaker_id: str = ""
    bullet_ids_used: str = ""


# ---------------------------------------------------------------------------
# CoachingEngine
# ---------------------------------------------------------------------------

class CoachingEngine:
    """
    Real-time coaching engine.

    Usage (inside WebSocket handler):
        engine = CoachingEngine(user_speaker="speaker_0")
        ...
        prompt = await engine.process(
            elm_event=elm_event,          # None if no ELM signal this turn
            participant_profile=profiler.get_classification(speaker_id),
            user_profile=profile_snapshot,
            user_is_speaking=(current_speaker == user_speaker_id),
        )
        if prompt:
            await ws.send_json({"type": "coaching_prompt", **asdict(prompt)})
        ...
        engine.reset()   # call at session end
    """

    def __init__(
        self,
        user_speaker: str,
        anthropic_client: AsyncAnthropic | None = None,
        elm_cadence_floor_s: float = 10.0,
        general_cadence_floor_s: float = 15.0,
        haiku_timeout_s: float = 1.5,
        model: str = _DEFAULT_MODEL,
        user_archetype: str | None = None,
        participants: list[dict[str, str]] | None = None,
        effectiveness_data: dict[tuple[str, str], float] | None = None,
        user_id: str | None = None,
    ) -> None:
        self._user_speaker = user_speaker
        self._client = anthropic_client or AsyncAnthropic()
        self._elm_floor = elm_cadence_floor_s
        self._general_floor = general_cadence_floor_s
        self._timeout = haiku_timeout_s
        self._model = model
        self._user_archetype = user_archetype or "Unknown"
        self._participants = participants or []
        self._effectiveness = effectiveness_data or {}
        self._user_id = user_id

        # Monotonic timestamp of the last emitted prompt (0.0 = never)
        self._last_prompt_time: float = 0.0
        # Last successfully generated prompt per layer (for fallback)
        self._cache: dict[CoachingLayer, CoachingPrompt] = {}
        # Bullet IDs from the most recent context selection (set per prompt cycle)
        self._last_bullet_ids: str = ""

    @property
    def user_archetype(self) -> str:
        return self._user_archetype

    @user_archetype.setter
    def user_archetype(self, value: str) -> None:
        self._user_archetype = value

    # ------------------------------------------------------------------
    # Initial session prompt
    # ------------------------------------------------------------------

    async def initial_prompt(
        self,
        *,
        user_profile: ProfileSnapshot | None = None,
        user_display_name: str = "",
        meeting_title: str = "",
    ) -> CoachingPrompt | None:
        """
        Generate a welcome coaching prompt at session start.

        Fires once when the session connects, before any utterances.
        Incorporates: user name, archetype profile, meeting context,
        known participants with pairing advice, and learned coaching bullets.
        """
        user_type = (
            user_profile.archetype if user_profile and user_profile.archetype != "Undetermined"
            else self._user_archetype
        )

        # User identity and profile context
        name_line = f"The user's name is {user_display_name}." if user_display_name else ""
        profile_line = f"You are a {user_type}."
        if user_profile and user_profile.context_shifts:
            profile_line += (
                f" In most meetings you're a {user_profile.core_archetype}, "
                f"but in {user_profile.context} settings you shift toward {user_type}."
            )
        confidence_line = ""
        if user_profile and user_profile.core_sessions >= 3:
            confidence_line = (
                f"Based on {user_profile.core_sessions} sessions observed."
            )

        # Meeting context
        meeting_note = f'Meeting: "{meeting_title}"' if meeting_title else ""

        # Build participant roster with pairing dynamics
        participants_section = ""
        if self._participants:
            roster = []
            for p in self._participants:
                pname = p.get("name", "Unknown")
                arch = p.get("archetype", "Unknown")
                pairing = self._enriched_pairing_advice(arch, pname)
                fp = p.get("fingerprint")
                if fp:
                    sessions = fp.get("sessions_observed", 0)
                    patterns = fp.get("patterns", [])
                    summary = f"{pname} is a {arch} ({sessions} prior sessions)"
                    if patterns:
                        summary += f". Pattern: {patterns[0]}"
                    roster.append(f"  - {summary}. {pairing}")
                else:
                    roster.append(f"  - {pname} is a {arch}. {pairing}")
            participants_section = (
                "People in this meeting:\n"
                + "\n".join(roster) + "\n"
            )

        # Include learned coaching context from prior sessions
        playbook_section = ""
        self._last_bullet_ids = ""
        if self._user_id:
            ctx, bullet_ids = await self._load_coaching_context("Unknown")
            if ctx:
                playbook_section = f"{ctx}\n"
            if bullet_ids:
                self._last_bullet_ids = ",".join(bullet_ids)

        user_msg = (
            f"{name_line}\n"
            f"{profile_line}\n"
            f"{confidence_line}\n"
            f"{meeting_note}\n\n"
            f"{participants_section}\n"
            f"{playbook_section}\n"
            "This is the start of a session. Generate an opening coaching tip.\n"
            "RULES:\n"
            f"- Address the user by their first name ({(user_display_name.split()[0] if user_display_name.split() else 'there') if user_display_name else 'there'}).\n"
            "- If participants are listed, name the person who will be hardest "
            "to persuade and give ONE specific thing to do in the first 2 minutes "
            "based on the pairing between the user's type and that person's type.\n"
            "- If no participants are listed, give a readiness tip based on the "
            "user's archetype tendencies.\n"
            "- Keep it warm, direct, and actionable. One or two sentences max."
        )
        prompt = await self._call_haiku("self", user_msg, "session:start", "")
        if prompt:
            self._last_prompt_time = time.monotonic()
        return prompt

    # ------------------------------------------------------------------
    # Core processor
    # ------------------------------------------------------------------

    async def process(
        self,
        *,
        elm_event: ELMEvent | None = None,
        participant_profile: WindowClassification | None = None,
        user_profile: ProfileSnapshot | None = None,
        recent_transcript: list[dict[str, str]] | None = None,
        user_is_speaking: bool = False,
    ) -> CoachingPrompt | None:
        """
        Evaluate one coaching cycle and return a prompt or None.

        Pass elm_event when ELMDetector.process_utterance() returned a non-None event.
        Pass None for elm_event on regular cadence ticks.

        Returns None when:
          - user_is_speaking AND cadence floor not reached (self-layer still fires)
          - the applicable cadence floor has not elapsed
          - Haiku fails AND no cached prompt exists for that layer
        """
        now = time.monotonic()

        if elm_event is not None and not user_is_speaking:
            # ELM prompts (audience-layer) only fire on counterpart utterances
            if now - self._last_prompt_time < self._elm_floor:
                return None
            prompt = await self._elm_prompt(elm_event, participant_profile, user_profile)
        else:
            # Self-layer general prompts fire on BOTH user and counterpart utterances.
            # This is how "you've been advocating for 4 minutes — ask a question" works.
            if now - self._last_prompt_time < self._general_floor:
                return None
            prompt = await self._general_prompt(
                participant_profile, user_profile, recent_transcript
            )

        if prompt is not None:
            self._last_prompt_time = now
            # Only cache fresh (non-fallback) prompts so stale text is not re-cached
            if not prompt.is_fallback:
                self._cache[prompt.layer] = prompt

        return prompt

    # ------------------------------------------------------------------
    # Prompt builders — one per trigger type
    # ------------------------------------------------------------------

    async def _elm_prompt(
        self,
        event: ELMEvent,
        participant: WindowClassification | None,
        user: ProfileSnapshot | None,
    ) -> CoachingPrompt | None:
        """Audience-layer prompt triggered by an ELM state event."""
        state = event.state
        evidence_text = (
            "; ".join(str(e) for e in event.evidence[:2])
            if event.evidence
            else event.utterance[:80]
        )

        # Use profiler classification if available, fall back to pre-seeded participant data
        counterpart_type = participant.superpower if participant else self._lookup_participant(event.speaker_id)
        user_type = (
            user.archetype if user and user.archetype != "Undetermined"
            else self._user_archetype
        )

        state_desc = _ELM_STATE_DESCRIPTION.get(state, state.replace("_", " "))
        goal = _ELM_COACHING_GOAL.get(state, "improve the conversation")

        # Build counterpart-specific advice based on archetype pairing + effectiveness + fingerprint
        counterpart_name = self._resolve_speaker_name(event.speaker_id)
        pairing_note = self._enriched_pairing_advice(counterpart_type, counterpart_name)

        # Include learned coaching context from prior sessions (ACE bullet store)
        playbook_section = ""
        self._last_bullet_ids = ""
        if self._user_id:
            ctx, bullet_ids = await self._load_coaching_context(
                counterpart_type, state
            )
            if ctx:
                playbook_section = f"\n{ctx}\n\n"
            if bullet_ids:
                self._last_bullet_ids = ",".join(bullet_ids)

        # Plain-English situation description (no academic jargon)
        if state == "ego_threat":
            route_note = "They feel attacked — logic won't land right now. Acknowledge their point first."
        elif state == "shortcut":
            route_note = "They're agreeing on autopilot, not actually thinking it through. Ask something specific."
        elif state == "consensus_protection":
            route_note = "The group is rushing to agree — someone has a concern they're not saying. Make space."
        else:
            route_note = ""

        # Build counterpart label: "Sarah (Architect)" or just "Architect" if no name
        counterpart_label = (
            f"{counterpart_name} ({counterpart_type})" if counterpart_name
            else counterpart_type
        )

        user_msg = (
            f"Counterpart: {counterpart_label} — {state_desc}\n"
            f"What's happening: {route_note}\n"
            f'What they just said: "{evidence_text}"\n'
            f"You ({user_type}) → {counterpart_label}: {pairing_note}\n"
            f"Goal: {goal}\n"
            f"{playbook_section}"
            f"Give a plain-English coaching tip that names {counterpart_name or 'the counterpart'} and tells me exactly what to do:"
        )
        return await self._call_haiku(
            "audience", user_msg, f"elm:{state}", event.speaker_id
        )

    async def _general_prompt(
        self,
        participant: WindowClassification | None,
        user: ProfileSnapshot | None,
        recent_transcript: list[dict[str, str]] | None = None,
    ) -> CoachingPrompt | None:
        """Self-layer general cadence prompt with conversation context."""
        # Prefer ProfileSnapshot archetype (behavioral data) over the static constructor value
        user_type = (
            user.archetype if user and user.archetype != "Undetermined"
            else self._user_archetype
        )
        context = user.context if user else "meeting"
        counterpart_type = participant.superpower if participant else "Unknown"
        counterpart_name = self._resolve_speaker_name(participant.speaker_id) if participant else ""

        # Mention context shift when the user's style differs by meeting type
        shift_note = ""
        if user and user.context_shifts:
            shift_note = (
                f" (you typically show as {user.core_archetype} "
                f"in other contexts)"
            )

        # Flexibility-aware coaching note (~12 words, added to Haiku input)
        # Descriptive (not prescriptive) — lets Haiku decide the coaching action.
        flex_note = ""
        if _FLEX_NOTE_ENABLED and user and (user.focus_variance + user.stance_variance) > 0:
            total_var = user.focus_variance + user.stance_variance
            if total_var > 500:  # high variance = genuinely flexes across contexts
                flex_note = "This person adapts their style across different contexts."
            elif total_var < 100 and user.core_sessions >= 5:
                flex_note = "This person tends to use the same style regardless of context."

        # Build recent conversation snippet for context
        transcript_section = ""
        if recent_transcript:
            lines = []
            for u in recent_transcript[-8:]:
                speaker = u.get("speaker", "?")
                text = u.get("text", "")[:120]
                if speaker == self._user_speaker:
                    label = "You"
                else:
                    resolved = self._resolve_speaker_name(speaker)
                    label = resolved if resolved else speaker
                lines.append(f"  {label}: {text}")
            transcript_section = (
                "Recent conversation:\n" + "\n".join(lines) + "\n\n"
            )

        # Build participant roster for the prompt — with behavioral fingerprints
        participants_section = ""
        if self._participants:
            roster = []
            for p in self._participants:
                name = p.get("name", "Unknown")
                arch = p.get("archetype", "Unknown")
                pairing = self._enriched_pairing_advice(arch, name)
                fp = p.get("fingerprint")
                if fp:
                    sessions = fp.get("sessions_observed", 0)
                    patterns = fp.get("patterns", [])
                    summary = f"{name} ({arch}, {sessions} sessions)"
                    if patterns:
                        summary += f": {patterns[0]}"
                    roster.append(f"  - {summary}. Approach: {pairing}")
                else:
                    roster.append(f"  - {name} ({arch}): {pairing}")
            participants_section = (
                "Meeting participants and how to reach them:\n"
                + "\n".join(roster) + "\n\n"
            )

        # Include learned coaching context from prior sessions (ACE bullet store)
        playbook_section = ""
        self._last_bullet_ids = ""
        if self._user_id:
            ctx, bullet_ids = await self._load_coaching_context(
                counterpart_type
            )
            if ctx:
                playbook_section = f"{ctx}\n\n"
            if bullet_ids:
                self._last_bullet_ids = ",".join(bullet_ids)

        user_msg = (
            f"{transcript_section}"
            f"{participants_section}"
            f"{playbook_section}"
            f"Meeting context: {context}\n"
            f"You are a {user_type}{shift_note}.\n"
            + (f"{flex_note}\n" if flex_note else "")
            + f"Primary counterpart: {f'{counterpart_name} ({counterpart_type})' if counterpart_name else counterpart_type}\n\n"
            "Read the conversation flow. Is anyone defensive, checked out, or "
            "just going along to be polite? Give ONE coaching tip in plain English that "
            "names the specific person and tells me exactly what to do right now:"
        )
        return await self._call_haiku("self", user_msg, "cadence:self", "")

    # ------------------------------------------------------------------
    # Effectiveness-enriched advice
    # ------------------------------------------------------------------

    def _enriched_pairing_advice(
        self, counterpart_type: str, participant_name: str = "",
    ) -> str:
        """Pairing advice annotated with effectiveness + behavioral fingerprint."""
        base = _archetype_pairing_advice(self._user_archetype, counterpart_type)
        eff = self._effectiveness.get((self._user_archetype, counterpart_type))
        if eff is not None:
            if eff > 0.6:
                base = f"{base} (this approach has been working well for you)"
            elif eff < 0.3:
                base = f"{base} (this hasn't been landing — try a different angle)"

        # Enrich with behavioral fingerprint if available
        fp = self._get_fingerprint(participant_name)
        if fp:
            patterns = fp.get("patterns", [])
            if patterns:
                base = f"{base}. Behavioral intel: {patterns[0]}"
            elm = fp.get("elm_tendencies", {})
            if elm.get("ego_threat", 0) >= 2:
                base = f"{base}. WARNING: frequently defensive — lead with acknowledgment"
        return base

    def _get_fingerprint(self, name: str) -> dict | None:
        """Look up fingerprint data for a participant by name."""
        if not name:
            return None
        for p in self._participants:
            if p.get("name", "").lower() == name.lower():
                return p.get("fingerprint")
        return None

    # ------------------------------------------------------------------
    # Speaker name + archetype resolution
    # ------------------------------------------------------------------

    def _resolve_speaker_name(self, speaker_id: str) -> str:
        """
        Resolve a speaker_id to a human name from the participants list.

        Tries: (1) direct speaker_id match, (2) index-based lookup.
        Returns "" if no name can be resolved.
        """
        if not self._participants:
            return ""
        # Direct match by speaker_id field
        for p in self._participants:
            if p.get("speaker_id") == speaker_id:
                return p.get("name", "")
        # Index-based matching
        try:
            if speaker_id.startswith("counterpart_"):
                idx = int(speaker_id.replace("counterpart_", ""))
            else:
                idx = int(speaker_id.replace("speaker_", "")) - 1
            if 0 <= idx < len(self._participants):
                return self._participants[idx].get("name", "")
        except (ValueError, IndexError):
            pass
        return ""

    def _lookup_participant(self, speaker_id: str) -> str:
        """Look up a participant's archetype from pre-seeded data by speaker ID or index."""
        if not self._participants:
            return "Unknown"
        # Try matching by speaker_id field
        for p in self._participants:
            if p.get("speaker_id") == speaker_id:
                return p.get("archetype", "Unknown")
        # Fall back to index-based matching
        try:
            if speaker_id.startswith("counterpart_"):
                idx = int(speaker_id.replace("counterpart_", ""))
            else:
                # Legacy speaker_N format: speaker_0 is user, counterparts start at 1
                idx = int(speaker_id.replace("speaker_", "")) - 1
            if 0 <= idx < len(self._participants):
                return self._participants[idx].get("archetype", "Unknown")
        except (ValueError, IndexError):
            pass
        # Default to first participant's archetype as best guess
        return self._participants[0].get("archetype", "Unknown")

    # ------------------------------------------------------------------
    # Context loading (ACE bullet store with legacy fallback)
    # ------------------------------------------------------------------

    async def _load_coaching_context(
        self,
        counterpart_archetype: str | None = None,
        elm_state: str | None = None,
    ) -> tuple[str, list[str]]:
        """
        Load coaching context from the ACE bullet store.

        Falls back to the legacy markdown playbook if the bullet store
        is unavailable or empty.

        Returns (formatted_text, list_of_bullet_ids).
        """
        try:
            from backend.coaching_bullets import get_coaching_context
            from backend.database import get_db_session

            async with get_db_session() as db:
                return await get_coaching_context(
                    db, self._user_id,
                    counterpart_archetype=counterpart_archetype,
                    elm_state=elm_state,
                )
        except Exception:
            # Fallback to legacy sync playbook
            ctx = _get_legacy_coaching_context(
                self._user_id, counterpart_archetype, elm_state
            )
            return (ctx, [])

    # ------------------------------------------------------------------
    # Haiku call with timeout + fallback
    # ------------------------------------------------------------------

    async def _call_haiku(
        self,
        layer: CoachingLayer,
        user_msg: str,
        triggered_by: str,
        speaker_id: str,
    ) -> CoachingPrompt | None:
        """
        Fire the Haiku API call with a hard timeout.

        On success  → return fresh CoachingPrompt (is_fallback=False).
        On timeout  → return last cached prompt for this layer (is_fallback=True),
                      or None if no cache exists yet.
        On any other exception → same fallback behaviour.
        """
        try:
            response = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._model,
                    max_tokens=_MAX_TOKENS,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                ),
                timeout=self._timeout,
            )
            text = response.content[0].text.strip()
            return CoachingPrompt(
                layer=layer,
                text=text,
                is_fallback=False,
                triggered_by=triggered_by,
                speaker_id=speaker_id,
                bullet_ids_used=self._last_bullet_ids,
            )
        except Exception:
            cached = self._cache.get(layer)
            if cached:
                return CoachingPrompt(
                    layer=layer,
                    text=cached.text,
                    is_fallback=True,
                    triggered_by=triggered_by,
                    speaker_id=speaker_id,
                )
            return None

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def last_prompt_time(self) -> float:
        """Monotonic timestamp of the last prompt emitted (0.0 if none yet)."""
        return self._last_prompt_time

    def reset(self) -> None:
        """Clear cadence state and prompt cache. Call between sessions."""
        self._last_prompt_time = 0.0
        self._cache.clear()
