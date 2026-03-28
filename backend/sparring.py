"""
AI sparring partner — text-based practice mode (no audio required).

Architecture
────────────
  User text input
       │
       ▼
  SparringSession
       │
       ├── OpponentEngine  ──► Claude Opus (streams opponent's response)
       │                         Role: play a counterpart of the requested type
       │
       └── CoachEngine     ──► Claude Haiku (short coaching tip after each turn)
                                 Role: observe the user's message and coach
       │
       ▼
  SparringTurn (yielded via async generator)

Usage
─────
    session = SparringSession(
        user_archetype="Inquisitor",
        opponent_archetype="Firestarter",
        scenario="Pitch a new product roadmap to a skeptical VP of Engineering",
    )

    async for turn in session.run():
        print(f"[{turn.role}] {turn.text}")
        if turn.coaching_tip:
            print(f"  ↳ coaching: {turn.coaching_tip}")

Latency target (from CLAUDE.md)
────────────────────────────────
  <3 s total round-trip: user turn → opponent response → coaching tip.
  Opponent response is streamed so the first token appears in <1 s.
  Coaching tip is generated in parallel with opponent streaming.

Coaching cadence
────────────────
  A coaching tip is generated for every user turn (no floor in sparring mode —
  the user is explicitly practising, so frequency is welcome).
  The tip is suppressed if the user's message is fewer than 5 words (too short
  to coach meaningfully).

Session limits
──────────────
  max_turns (default 10): total user turns before auto-end.
  The session can also be ended early by calling session.end().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

from anthropic import AsyncAnthropic

from backend.pre_seeding import SuperpowerType


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

TurnRole = Literal["user", "opponent", "coaching"]

_DEFAULT_OPPONENT_MODEL = "claude-opus-4-6"
_DEFAULT_COACHING_MODEL = "claude-haiku-4-5-20251001"

_MIN_WORDS_FOR_COACHING = 5
_DEFAULT_MAX_TURNS = 10

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_ARCHETYPE_DESCRIPTIONS: dict[str, str] = {
    "Architect": (
        "an Architect communicator: analytical, data-first, systematic. "
        "You build arguments like blueprints — step-by-step logic, evidence at each node. "
        "You are skeptical of hand-waving and push back on vague claims by asking for specifics."
    ),
    "Firestarter": (
        "a Firestarter communicator: energetic, narrative-driven, inspiring. "
        "You lead with vision and emotion. You paint pictures of possibilities and challenge "
        "others to think bigger. You can become impatient with slow, data-heavy reasoning."
    ),
    "Inquisitor": (
        "an Inquisitor communicator: question-driven, evidence-demanding, adversarial by default. "
        "You probe every assumption. You are not convinced without data and push back persistently "
        "on any claim you can't verify. You rarely give an inch without good reason."
    ),
    "Bridge Builder": (
        "a Bridge Builder communicator: consensus-oriented, empathetic, harmony-seeking. "
        "You scan for disagreement and try to synthesise. You can be evasive when pushed into "
        "a corner and may suppress your own views to keep the peace."
    ),
}

_OPPONENT_SYSTEM = """\
You are roleplaying as {archetype_desc} in a professional meeting scenario.

Scenario: {scenario}

Rules:
- Stay in character at all times. Do NOT break role or explain your reasoning.
- Respond as if this is a real, high-stakes meeting — not a rehearsal.
- Keep responses concise: 2-4 sentences maximum.
- React authentically to what the user says: if they make a strong point, concede ground
  appropriately. If they are vague, push back.
- Never lecture. Never list bullet points. Speak naturally.
"""

_COACHING_SYSTEM = """\
You are an expert real-time conversation coach observing a practice scenario.
Output EXACTLY ONE short coaching tip (≤15 words) for the USER.
Rules: verb-first imperative, positive action (what TO do), no preamble, no quotes.
Output only the tip itself.
"""

_COACHING_USER_TEMPLATE = """\
Scenario: {scenario}
User archetype: {user_archetype}
Opponent archetype: {opponent_archetype}
User just said: "{user_text}"
Coaching tip:"""

_INTRO_SYSTEM = """\
You are roleplaying as {archetype_desc} in a professional meeting.

Scenario: {scenario}

Open the conversation in character. Take your initial position, make your opening ask,
or pose your opening challenge — whatever your archetype would do to kick things off.
Do NOT use meta-phrases like "let's get started" or "shall we begin".
Speak as if the meeting is already in motion. 2-3 sentences maximum.
"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SparringTurn:
    """One turn in a sparring session."""
    role: TurnRole           # "user" | "opponent" | "coaching"
    text: str
    turn_number: int         # 0-based index of the USER turn this belongs to
    coaching_tip: str = ""   # non-empty only on "opponent" turns (delivered alongside)
    is_final: bool = True    # False while opponent is still streaming


@dataclass
class SparringSession:
    """
    Manages one AI sparring session.

    Parameters
    ----------
    user_archetype:
        Communicator Superpower of the person being coached.
    opponent_archetype:
        Communicator Superpower the AI opponent should play.
    scenario:
        Short description of the meeting context / goal.
    max_turns:
        Maximum user turns before the session ends automatically.
    anthropic_client:
        Injectable Anthropic client (defaults to AsyncAnthropic()).
    opponent_model / coaching_model:
        Model IDs (injectable for testing).
    """

    user_archetype: SuperpowerType
    opponent_archetype: SuperpowerType
    scenario: str
    max_turns: int = _DEFAULT_MAX_TURNS
    anthropic_client: AsyncAnthropic | None = None
    opponent_model: str = _DEFAULT_OPPONENT_MODEL
    coaching_model: str = _DEFAULT_COACHING_MODEL

    # Internal state
    _history: list[dict[str, str]] = field(default_factory=list, repr=False)
    _turn_count: int = field(default=0, repr=False)
    _ended: bool = field(default=False, repr=False)
    _client: AsyncAnthropic | None = field(default=None, repr=False, init=False)

    def __post_init__(self) -> None:
        self._client = self.anthropic_client or AsyncAnthropic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def end(self) -> None:
        """Signal the session to stop after the current turn completes."""
        self._ended = True

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def is_ended(self) -> bool:
        return self._ended

    async def intro(self) -> AsyncIterator[SparringTurn]:
        """Stream the opponent's opening statement before the user's first turn."""
        return self._generate_intro()

    async def send(self, user_text: str) -> AsyncIterator[SparringTurn]:
        """
        Submit one user message and stream back:
          1. SparringTurn(role="user", ...)  — echoed immediately
          2. SparringTurn(role="opponent", is_final=False, ...)  — streaming chunks
          3. SparringTurn(role="opponent", is_final=True, ...)   — final opponent text
          4. SparringTurn(role="coaching", ...)  — coaching tip (if long enough)

        Returns an async generator. Callers iterate it to receive turns.
        """
        return self._generate_turns(user_text)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _generate_intro(self) -> AsyncIterator[SparringTurn]:
        """Generate the opponent's opening statement, seeding history for coherent follow-ups."""
        archetype_desc = _ARCHETYPE_DESCRIPTIONS.get(
            self.opponent_archetype,
            f"a {self.opponent_archetype} communicator",
        )
        system = _INTRO_SYSTEM.format(
            archetype_desc=archetype_desc,
            scenario=self.scenario,
        )
        # Prime history with a synthetic user cue so the messages list is valid for the API.
        self._history.append({"role": "user", "content": "Begin."})

        full_text = ""
        async with self._client.messages.stream(
            model=self.opponent_model,
            max_tokens=150,
            system=system,
            messages=list(self._history),
        ) as stream:
            async for chunk in stream.text_stream:
                full_text += chunk
                yield SparringTurn(
                    role="opponent",
                    text=chunk,
                    turn_number=-1,
                    is_final=False,
                )

        self._history.append({"role": "assistant", "content": full_text})
        yield SparringTurn(
            role="opponent",
            text=full_text,
            turn_number=-1,
            is_final=True,
        )

    async def _generate_turns(
        self,
        user_text: str,
    ) -> AsyncIterator[SparringTurn]:
        if self._ended or self._turn_count >= self.max_turns:
            self._ended = True
            return

        turn_number = self._turn_count
        self._turn_count += 1

        # Echo user turn
        yield SparringTurn(role="user", text=user_text, turn_number=turn_number)
        self._history.append({"role": "user", "content": user_text})

        # Build system prompt for opponent
        archetype_desc = _ARCHETYPE_DESCRIPTIONS.get(
            self.opponent_archetype,
            f"a {self.opponent_archetype} communicator",
        )
        opponent_system = _OPPONENT_SYSTEM.format(
            archetype_desc=archetype_desc,
            scenario=self.scenario,
        )

        # Kick off coaching in parallel (fire-and-forget task)
        coaching_tip = ""
        coaching_task: asyncio.Task | None = None
        word_count = len(user_text.split())
        if word_count >= _MIN_WORDS_FOR_COACHING:
            coaching_task = asyncio.ensure_future(
                self._get_coaching_tip(user_text)
            )

        # Stream opponent response
        full_opponent_text = ""
        async with self._client.messages.stream(
            model=self.opponent_model,
            max_tokens=200,
            system=opponent_system,
            messages=list(self._history),
        ) as stream:
            async for chunk in stream.text_stream:
                full_opponent_text += chunk
                yield SparringTurn(
                    role="opponent",
                    text=chunk,
                    turn_number=turn_number,
                    is_final=False,
                )

        # Finalize opponent message
        self._history.append({"role": "assistant", "content": full_opponent_text})
        yield SparringTurn(
            role="opponent",
            text=full_opponent_text,
            turn_number=turn_number,
            is_final=True,
        )

        # Collect coaching tip
        if coaching_task is not None:
            try:
                coaching_tip = await coaching_task
            except Exception:
                coaching_tip = ""

        if coaching_tip:
            yield SparringTurn(
                role="coaching",
                text=coaching_tip,
                turn_number=turn_number,
                coaching_tip=coaching_tip,
            )

        # Auto-end when max_turns reached
        if self._turn_count >= self.max_turns:
            self._ended = True

    async def _get_coaching_tip(self, user_text: str) -> str:
        """Request a single coaching tip from Haiku."""
        user_msg = _COACHING_USER_TEMPLATE.format(
            scenario=self.scenario,
            user_archetype=self.user_archetype,
            opponent_archetype=self.opponent_archetype,
            user_text=user_text,
        )
        response = await self._client.messages.create(
            model=self.coaching_model,
            max_tokens=40,
            system=_COACHING_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()

    def history_snapshot(self) -> list[dict[str, str]]:
        """Return a copy of the conversation history (role/content dicts)."""
        return list(self._history)
