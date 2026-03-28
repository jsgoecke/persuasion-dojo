"""
ELM state detector — classifies audience emotional/processing state in real time.

Background
──────────
The Elaboration Likelihood Model (Petty & Cacioppo, 1986) describes two routes
to attitude change:

  Central route:    careful, deliberate processing of arguments (high elaboration)
  Peripheral route: quick, heuristic processing — shortcuts instead of reasoning

This detector identifies three audience states that signal persuasion is blocked:

  ego_threat
    Audience member feels defensive or identity-threatened.
    Signs: hostile pushback, dismissive challenges, "we've always done it this way."
    → Coaching: back off, acknowledge feelings, ask questions instead of asserting.

  shortcut
    Audience agreeing too quickly — peripheral route, no real engagement.
    Signs: N consecutive pure-agreement utterances with no questions or substance.
    → Coaching: invite pushback, ask "what's your biggest concern?", deepen.

  consensus_protection
    Group is suppressing dissent to maintain harmony (groupthink risk).
    Signs: premature closure language, "we all agree", "let's not debate this."
    → Coaching: explicitly invite dissent — "What's the strongest argument against?"

ego_threat_events (int) counts distinct ego-threat episodes in the session.
This feeds _score_ego_safety() in scoring.py as the ELM penalty.

State transitions
─────────────────
  neutral ──[ego signals]──────────────────► ego_threat
           ──[consensus signals]────────────► consensus_protection
           ──[N pure agreements]────────────► shortcut

  ego_threat         ──[2 neutral utts]──► neutral (debounced)
  consensus_protection ──[2 neutral utts]──► neutral (debounced)
  shortcut           ──[question / substantive]──► neutral (immediate)
  any state          ──[ego signals]──────► ego_threat (overrides)

Debounce (ego_threat / consensus_protection):
  2 consecutive neutral utterances from the speaker before the episode resets.
  Prevents a single agreeable "yes" from resetting genuine hostility.

Shortcut detection:
  3 consecutive pure-agreement utterances (≤15 words, no ?, no ego-threat signals).
  Resets immediately on any question or substantive utterance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ELMState = Literal["neutral", "ego_threat", "shortcut", "consensus_protection"]

# Consecutive neutral utterances required before ego_threat/consensus episode resets
_DEBOUNCE_NEUTRALS = 2

# Consecutive pure-agreement utterances required to trigger shortcut
_SHORTCUT_STREAK_THRESHOLD = 3

# Maximum word count for an utterance to qualify as "pure agreement"
_PURE_AGREEMENT_MAX_WORDS = 15


# ---------------------------------------------------------------------------
# Signal pattern regexes
# ---------------------------------------------------------------------------

# Ego threat — defensive, dismissive, identity-protecting language
_EGO_THREAT_RE = re.compile(
    r"\b("
    r"I\s+disagree"
    r"|I\s+don'?t\s+(?:think|agree|see\s+how|understand|believe\s+that)"
    r"|I'?m\s+not\s+(?:sure\s+about\s+that|convinced|buying\s+(?:it|this|that))"
    r"|why\s+would\s+(?:you|we)\b"
    r"|that\s+doesn'?t\s+(?:make\s+sense|work|add\s+up)"
    r"|that'?s\s+(?:not\s+right|wrong|incorrect|not\s+how\s+(?:we|it))"
    r"|we'?ve\s+always\s+(?:done|handled|worked)"
    r"|in\s+my\s+(?:experience|opinion)(?:\s+that|\s+this)?"
    r"|not\s+(?:convinced|buying\s+(?:it|this|that))"
    r"|I\s+challenge\s+(?:that|this|your)"
    r"|but\s+hold\s+on\b"
    r"|actually\s+(?:no\b|that'?s\s+not)"
    r"|you\s+don'?t\s+understand"
    r"|I\s+take\s+issue"
    r"|that'?s\s+a\s+(?:stretch|reach|bit\s+much)"
    r"|with\s+all\s+due\s+respect"
    r")\b",
    re.IGNORECASE,
)

# Shortcut / pure agreement — peripheral-route heuristic agreement
_SHORTCUT_RE = re.compile(
    r"\b("
    r"yes\b|yeah\b|yep\b|yup\b"
    r"|sure\b|certainly\b"
    r"|of\s+course\b"
    r"|absolutely\b|definitely\b|totally\b"
    r"|exactly\b|precisely\b"
    r"|sounds\s+good\b|looks\s+good\b"
    r"|makes\s+sense\b"
    r"|agreed\b|I\s+agree\b"
    r"|I\s+think\s+so\b"
    r"|got\s+it\b|understood\b|noted\b"
    r"|great\b|perfect\b|fantastic\b"
    r"|fair\s+enough\b"
    r"|that\s+works\s+for\s+me\b"
    r")\b",
    re.IGNORECASE,
)

# Consensus protection — group suppressing dissent / premature closure
_CONSENSUS_RE = re.compile(
    r"\b("
    r"I\s+think\s+we\s+(?:all\s+)?agree\b"
    r"|we'?re\s+(?:all\s+)?(?:aligned\b|on\s+the\s+same\s+page\b)"
    r"|everyone(?:'?s?)?\s+(?:on\s+board\b|in\s+agreement\b|aligned\b)"
    r"|let'?s\s+(?:just\s+)?(?:move\s+on\b|decide\b|wrap\s+(?:up\b|this\s+up\b)|close\s+this\b)"
    r"|let'?s\s+not\s+(?:get\s+into\s+that\b|debate\s+this\b|go\s+down\s+that\b)"
    r"|we\s+don'?t\s+need\s+to\s+debate\b"
    r"|we'?ve\s+(?:all\s+)?(?:agreed\b|decided\b|settled\s+this\b)"
    r"|no\s+need\s+to\s+(?:debate\b|argue\s+about\b)"
    r"|I\s+don'?t\s+think\s+we\s+need\s+to\s+discuss"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ELMEvent:
    """
    Emitted when a speaker transitions into a non-neutral ELM state.

    One event is emitted per episode — not per utterance. Consecutive hostile
    utterances within the same episode do not generate additional events.

    speaker_id:  the counterpart who triggered the event
    state:       the state being entered (ego_threat / shortcut / consensus_protection)
    evidence:    matched signal phrases from the triggering utterance
    utterance:   full text of the triggering utterance
    """
    speaker_id: str
    state: ELMState
    evidence: list[str]
    utterance: str


# ---------------------------------------------------------------------------
# Internal per-speaker state record
# ---------------------------------------------------------------------------

@dataclass
class _SpeakerRecord:
    state: ELMState = "neutral"
    in_episode: bool = False        # True when inside ego_threat or consensus episode
    neutral_streak: int = 0         # consecutive neutral utts since episode started (debounce)
    agreement_streak: int = 0       # consecutive pure-agreement utts (shortcut tracker)


# ---------------------------------------------------------------------------
# ELMDetector
# ---------------------------------------------------------------------------

class ELMDetector:
    """
    Real-time ELM state detector for meeting counterparts.

    Usage:
        detector = ELMDetector(user_speaker="speaker_0")
        for utt in transcript:
            event = detector.process_utterance(utt["speaker"], utt["text"])
            if event and event.state == "ego_threat":
                # trigger coaching prompt immediately
                ...
        # At session end, pass count to scoring:
        score = compute_persuasion_score(
            utterances, user_speaker,
            ego_threat_events=detector.ego_threat_events,
        )
        detector.reset()
    """

    def __init__(self, user_speaker: str) -> None:
        self._user_speaker = user_speaker
        self._speakers: dict[str, _SpeakerRecord] = {}
        self._ego_threat_count = 0
        self._shortcut_count = 0
        self._consensus_count = 0
        # Per-speaker episode log for behavioral fingerprinting
        self._episode_log: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Core processor
    # ------------------------------------------------------------------

    def process_utterance(self, speaker_id: str, text: str) -> ELMEvent | None:
        """
        Analyse one utterance and update the speaker's ELM state.

        Returns an ELMEvent when a new ELM episode begins, or None otherwise.
        User utterances are ignored (only audience members are tracked).

        Priority when multiple signals co-occur:
            ego_threat > consensus_protection > shortcut
        """
        if speaker_id == self._user_speaker:
            return None

        record = self._speakers.setdefault(speaker_id, _SpeakerRecord())

        ego_matches = _EGO_THREAT_RE.findall(text)
        consensus_matches = _CONSENSUS_RE.findall(text)
        shortcut_matches = _SHORTCUT_RE.findall(text)
        has_question = "?" in text
        word_count = len(text.split())

        is_ego_threatening = len(ego_matches) > 0
        is_consensus_protecting = len(consensus_matches) > 0
        is_pure_agreement = (
            len(shortcut_matches) > 0
            and not is_ego_threatening
            and not has_question
            and word_count <= _PURE_AGREEMENT_MAX_WORDS
        )
        is_substantive = (
            not is_ego_threatening
            and not is_pure_agreement
            and not is_consensus_protecting
        )

        # ── Ego threat — highest priority ─────────────────────────────
        if is_ego_threatening:
            record.agreement_streak = 0
            if not (record.in_episode and record.state == "ego_threat"):
                # New episode (first occurrence, or after debounce reset)
                record.state = "ego_threat"
                record.in_episode = True
                record.neutral_streak = 0
                self._ego_threat_count += 1
                self._episode_log.setdefault(speaker_id, []).append("ego_threat")
                return ELMEvent(
                    speaker_id=speaker_id,
                    state="ego_threat",
                    evidence=ego_matches,
                    utterance=text,
                )
            # Already in ego_threat episode — debounce counter is interrupted
            record.neutral_streak = 0
            return None

        # ── Consensus protection ───────────────────────────────────────
        if is_consensus_protecting:
            record.agreement_streak = 0
            record.neutral_streak = 0
            if not (record.in_episode and record.state == "consensus_protection"):
                record.state = "consensus_protection"
                record.in_episode = True
                self._consensus_count += 1
                self._episode_log.setdefault(speaker_id, []).append("consensus_protection")
                return ELMEvent(
                    speaker_id=speaker_id,
                    state="consensus_protection",
                    evidence=consensus_matches,
                    utterance=text,
                )
            return None

        # ── Pure agreement (potential shortcut) ───────────────────────
        if is_pure_agreement and not record.in_episode:
            record.agreement_streak += 1
            record.neutral_streak = 0
            if (
                record.agreement_streak >= _SHORTCUT_STREAK_THRESHOLD
                and record.state != "shortcut"
            ):
                record.state = "shortcut"
                self._shortcut_count += 1
                self._episode_log.setdefault(speaker_id, []).append("shortcut")
                return ELMEvent(
                    speaker_id=speaker_id,
                    state="shortcut",
                    evidence=shortcut_matches,
                    utterance=text,
                )
            return None

        # ── Neutral / substantive / question ──────────────────────────
        # Exit shortcut immediately on any engagement
        if record.state == "shortcut":
            record.state = "neutral"
            record.agreement_streak = 0
            return None

        # Debounce exit for ego_threat / consensus_protection episodes
        if record.in_episode:
            record.neutral_streak += 1
            record.agreement_streak = 0
            if record.neutral_streak >= _DEBOUNCE_NEUTRALS:
                record.state = "neutral"
                record.in_episode = False
                record.neutral_streak = 0
        else:
            record.agreement_streak = 0

        return None

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def ego_threat_events(self) -> int:
        """Count of distinct ego-threat episodes detected this session."""
        return self._ego_threat_count

    @property
    def shortcut_events(self) -> int:
        """Count of shortcut-mode episodes detected this session."""
        return self._shortcut_count

    @property
    def consensus_events(self) -> int:
        """Count of consensus-protection episodes detected this session."""
        return self._consensus_count

    def current_state(self, speaker_id: str) -> ELMState:
        """Return the current ELM state for a speaker (neutral if unseen)."""
        record = self._speakers.get(speaker_id)
        return record.state if record else "neutral"

    def all_states(self) -> dict[str, ELMState]:
        """Return current states for all tracked speakers."""
        return {sid: r.state for sid, r in self._speakers.items()}

    def get_episode_history(self, speaker_id: str) -> list[str]:
        """Return ELM episode types triggered for a speaker this session."""
        return list(self._episode_log.get(speaker_id, []))

    def reset(self) -> None:
        """Clear all state. Call between sessions."""
        self._speakers.clear()
        self._episode_log.clear()
        self._ego_threat_count = 0
        self._shortcut_count = 0
        self._consensus_count = 0
