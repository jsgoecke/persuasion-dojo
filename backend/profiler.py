"""
Participant Superpower profiler + User behavioral observer.

Two responsibilities
────────────────────
1.  ParticipantProfiler
    Rule-based, per-speaker sliding window (default: 5 utterances).
    Classifies each COUNTERPART (not the app user) into one of the four
    Communicator Superpower types based on their observed speech patterns.

    Signals detected per utterance:
        Logic signals     → positive focus axis  → Architect / Inquisitor
        Narrative signals → negative focus axis  → Firestarter / Bridge Builder
        Advocacy signals  → positive stance axis → Firestarter / Inquisitor
        Analysis signals  → negative stance axis → Architect / Bridge Builder

    Carry-forward: once a speaker has at least one utterance, a classification
    is always available. As new utterances push old ones out of the window,
    the classification updates in place rather than reverting to "Undetermined".

2.  UserBehaviorObserver
    Accumulates ALL user utterances for the session and produces a
    SessionObservation at session end.  The observation's focus_score /
    stance_score feed apply_session_observation() → Layer 1 + Layer 2 update.

    obs_confidence grows with utterance count (exponential saturation, similar
    to confidence_from_sessions). A session with < 5 user utterances contributes
    low weight to the EWMA, preventing sparse sessions from corrupting the aggregate.

Signal-to-Archetype mapping (AND-based neutral band, looser than
    self_assessment.map_to_archetype which uses OR logic):
    focus > 0, stance > 0  →  Inquisitor      (Logic + Advocacy)
    focus < 0, stance > 0  →  Firestarter     (Narrative + Advocacy)
    focus > 0, stance < 0  →  Architect       (Logic + Analysis)
    focus < 0, stance < 0  →  Bridge Builder  (Narrative + Analysis)
    |focus| ≤ band AND |stance| ≤ band → Undetermined
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from math import exp
from typing import Literal

from backend.models import SessionObservation
from backend.pre_seeding import SuperpowerType
from backend.self_assessment import map_to_archetype  # noqa: F401 — used in docstring reference

# Profiler-specific neutral band — tighter than self-assessment (15) because
# regex-based signal detection on real speech is sparser and noisier than
# Likert-scale self-assessment questions.
_PROFILER_NEUTRAL_BAND = 10


# ---------------------------------------------------------------------------
# Signal pattern regexes (compiled once at import)
# ---------------------------------------------------------------------------

# Logic axis — data-driven, systematic, evidence-based language
_LOGIC_RE = re.compile(
    r"\b("
    r"\d+(?:\.\d+)?(?:%|percent)?"           # numbers / percentages
    r"|data|metric[s]?|kpi[s]?"
    r"|measure[ds]?|measurement[s]?"
    r"|benchmark[s]?|baseline"
    r"|evidence|proof|research|study|studies"
    r"|analysis|analyses|analytic[s]?"
    r"|because|therefore|consequently|thus|hence"
    r"|specifically|precisely|exactly"
    r"|in\s+fact|in\s+reality"
    r"|hypothesis|test(?:ing|ed)?|valid(?:ate|ation)"
    r"|statistic[s]?|chart[s]?|graph[s]?|report[s]?"
    r"|root\s+cause|correlation|causal"
    r")\b",
    re.IGNORECASE,
)

# Narrative axis — story-based, experiential, inspirational language
_NARRATIVE_RE = re.compile(
    r"\b("
    r"imagine|picture|envision|visualize"
    r"|story|stories|narrative"
    r"|example[s]?|scenario[s]?|analogy"
    r"|when\s+(?:I|we)\b|let\s+me\s+tell"
    r"|I\s+remember|I\s+once"
    r"|excited|excit(?:ing|ed)|passion(?:ate)?"
    r"|inspir(?:e|ed|ing|ation)"
    r"|vision|dream|journey"
    r"|feel[s]?|feeling|emotion[s]?"
    r"|like\s+(?:a|an|the)\b"    # simile ("like a rocket")
    r"|as\s+if|as\s+though"
    r")\b",
    re.IGNORECASE,
)

# Advocacy axis — directive, decisive, assertive language
_ADVOCACY_RE = re.compile(
    r"\b("
    r"we\s+should|we\s+need(?:\s+to)?|we\s+must|we\s+have\s+to"
    r"|let['\u2019]?s\b|let\s+us\b"
    r"|I\s+recommend|I\s+propose|I\s+suggest"
    r"|I\s+believe\s+we|I\s+think\s+we"
    r"|the\s+answer\s+is|the\s+solution\s+is|the\s+right\s+(?:move|path|choice)"
    r"|(?:clearly|obviously|definitely)\s+we"
    r"|move\s+forward|next\s+step[s]?|action\s+(?:item[s]?|plan)"
    r"|commit(?:ting|ted)?|decide[ds]?|decision\s+(?:is|was)"
    r")\b",
    re.IGNORECASE,
)

# Analysis axis — questioning, exploratory, consensus-building language
_ANALYSIS_RE = re.compile(
    r"\b("
    r"what\s+do\s+you\s+think|what\s+are\s+your\s+thoughts?"
    r"|how\s+do\s+you\s+(?:see|feel|view)"
    r"|could\s+you|would\s+you|have\s+you\s+considered"
    r"|I(?:'m|'d)?\s+curious|I\s+wonder"
    r"|I(?:'d)?\s+like\s+to\s+understand"
    r"|perhaps|it\s+depends|on\s+the\s+other\s+hand|alternatively"
    r"|explore[ds]?|consider(?:ing|ed)?|reflect(?:ing|ed)?"
    r"|open\s+to|what\s+does\s+(?:the\s+)?(?:group|team|everyone|anybody)"
    r"|perspective[s]?|input|feedback|thoughts?"
    r")\b",
    re.IGNORECASE,
)

# Question mark — standalone question detector (complements _ANALYSIS_RE)
_QUESTION_RE = re.compile(r"\?")

# Saturation half-life for user observation confidence (utterances)
_OBS_CONF_HALF_LIFE = 12.0


# ---------------------------------------------------------------------------
# Pure signal primitives
# ---------------------------------------------------------------------------

@dataclass
class UtteranceSignals:
    """Raw signal hit counts for one utterance. All values ≥ 0."""
    logic: int = 0
    narrative: int = 0
    advocacy: int = 0
    analysis: int = 0

    @property
    def total(self) -> int:
        return self.logic + self.narrative + self.advocacy + self.analysis


def _score_utterance(text: str) -> UtteranceSignals:
    """
    Extract signal hit counts from a single utterance.
    Pure function — no side effects.

    Each regex match counts as one hit. Question marks count as analysis hits
    (in addition to any _ANALYSIS_RE matches).
    """
    return UtteranceSignals(
        logic=len(_LOGIC_RE.findall(text)),
        narrative=len(_NARRATIVE_RE.findall(text)),
        advocacy=len(_ADVOCACY_RE.findall(text)),
        analysis=len(_ANALYSIS_RE.findall(text)) + len(_QUESTION_RE.findall(text)),
    )


def _aggregate_signals(
    signals: list[UtteranceSignals],
) -> tuple[float, float, float]:
    """
    Aggregate a list of UtteranceSignals into (focus_score, stance_score, confidence).

    focus_score  : -100…+100, Logic positive / Narrative negative
    stance_score : -100…+100, Advocacy positive / Analysis negative
    confidence   : 0.0–0.9

    Confidence formula:
        base      = min(0.6, n / 5 × 0.6)           — grows with utterance count
        signal_bonus = min(0.3, total_signals / 15 × 0.3)  — grows with signal density
        confidence   = min(0.9, base + signal_bonus)
    """
    if not signals:
        return 0.0, 0.0, 0.0

    total_logic = sum(s.logic for s in signals)
    total_narrative = sum(s.narrative for s in signals)
    total_advocacy = sum(s.advocacy for s in signals)
    total_analysis = sum(s.analysis for s in signals)

    focus_hits = total_logic + total_narrative
    stance_hits = total_advocacy + total_analysis
    total_signals = focus_hits + stance_hits

    focus_score = (
        (total_logic - total_narrative) / focus_hits * 100.0 if focus_hits else 0.0
    )
    stance_score = (
        (total_advocacy - total_analysis) / stance_hits * 100.0 if stance_hits else 0.0
    )

    n = len(signals)
    base_conf = min(0.6, n / 5.0 * 0.6)
    signal_bonus = min(0.3, total_signals / 15.0 * 0.3)
    confidence = round(min(0.9, base_conf + signal_bonus), 3)

    return round(focus_score, 1), round(stance_score, 1), confidence


def classify_from_scores(focus: float, stance: float) -> SuperpowerType:
    """Map focus/stance scores to a Superpower archetype.

    Assumes scores are outside the neutral band (caller must check).
    """
    logic = focus > 0
    advocacy = stance > 0
    if logic and advocacy:
        return "Inquisitor"
    elif not logic and advocacy:
        return "Firestarter"
    elif logic and not advocacy:
        return "Architect"
    else:
        return "Bridge Builder"


def _obs_confidence(utterance_count: int) -> float:
    """
    Map user utterance count to observation confidence for SessionObservation.

    Exponential saturation: 1.0 − exp(−n / 12.0), clamped to [0.0, 0.95].

    Calibration:
        0 utterances  → 0.0
        5 utterances  → ≈ 0.34
        12 utterances → ≈ 0.63
        24 utterances → ≈ 0.86
        36 utterances → ≈ 0.95 (ceiling)
    """
    if utterance_count <= 0:
        return 0.0
    return round(min(0.95, 1.0 - exp(-utterance_count / _OBS_CONF_HALF_LIFE)), 4)


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------

@dataclass
class WindowClassification:
    """
    Current Superpower classification for one speaker's utterance window.

    Returned by ParticipantProfiler.add_utterance() on every update.
    Carry-forward: once any utterance exists, this is always populated.
    """
    speaker_id: str
    superpower: SuperpowerType | Literal["Undetermined"]
    confidence: float           # 0.0–0.9
    focus_score: float          # -100…+100 (Logic positive, Narrative negative)
    stance_score: float         # -100…+100 (Advocacy positive, Analysis negative)
    utterance_count: int        # utterances currently in the window


# ---------------------------------------------------------------------------
# Participant profiler (counterpart classification)
# ---------------------------------------------------------------------------

class ParticipantProfiler:
    """
    Sliding-window Superpower classifier for meeting counterparts.

    Maintains a deque of up to ``window_size`` UtteranceSignals per speaker.
    Each call to add_utterance() returns the updated WindowClassification.

    Usage:
        profiler = ParticipantProfiler()
        for utt in transcript:
            if utt["speaker"] != user_speaker_id:
                result = profiler.add_utterance(utt["speaker"], utt["text"])
                # result.superpower → coaching context for the next prompt
        # At session end:
        profiler.reset()
    """

    def __init__(
        self,
        window_size: int = 5,
        neutral_band: int = _PROFILER_NEUTRAL_BAND,
    ) -> None:
        self._window_size = window_size
        self._neutral_band = neutral_band
        self._windows: dict[str, deque[UtteranceSignals]] = {}
        # Full-session utterance log for behavioral evidence capture
        self._utterance_log: dict[str, list[tuple[str, UtteranceSignals]]] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def add_utterance(self, speaker_id: str, text: str) -> WindowClassification:
        """
        Process one utterance from a counterpart speaker.

        Appends the scored signals to the speaker's window (evicting the oldest
        entry when the window is full) and returns the updated classification.
        """
        if speaker_id not in self._windows:
            self._windows[speaker_id] = deque(maxlen=self._window_size)
        signals = _score_utterance(text)
        self._windows[speaker_id].append(signals)
        # Keep full-session log for behavioral evidence
        self._utterance_log.setdefault(speaker_id, []).append((text, signals))
        return self._classify(speaker_id)

    def get_classification(self, speaker_id: str) -> WindowClassification | None:
        """
        Return the current classification for a speaker, or None if unseen.
        """
        window = self._windows.get(speaker_id)
        if not window:
            return None
        return self._classify(speaker_id)

    def all_classifications(self) -> dict[str, WindowClassification]:
        """Return classifications for every speaker with at least one utterance."""
        return {
            sid: self._classify(sid)
            for sid, window in self._windows.items()
            if window
        }

    def speakers(self) -> list[str]:
        """Return IDs of all speakers with at least one utterance."""
        return [sid for sid, w in self._windows.items() if w]

    def get_key_evidence(
        self, speaker_id: str, top_n: int = 3,
    ) -> list[dict]:
        """
        Return the top *top_n* utterances for *speaker_id* ranked by signal
        strength (total signal hits).  Each entry is::

            {"text": str, "signals": {"logic": int, ...}, "strength": int}
        """
        log = self._utterance_log.get(speaker_id, [])
        ranked = sorted(log, key=lambda pair: pair[1].total, reverse=True)
        return [
            {
                "text": text[:300],
                "signals": {
                    "logic": sig.logic,
                    "narrative": sig.narrative,
                    "advocacy": sig.advocacy,
                    "analysis": sig.analysis,
                },
                "strength": sig.total,
            }
            for text, sig in ranked[:top_n]
        ]

    def get_all_signals(self, speaker_id: str) -> list[UtteranceSignals]:
        """Return every UtteranceSignals recorded this session for *speaker_id*."""
        return [sig for _, sig in self._utterance_log.get(speaker_id, [])]

    def reset(self) -> None:
        """Clear all speaker windows. Call between sessions."""
        self._windows.clear()
        self._utterance_log.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify(self, speaker_id: str) -> WindowClassification:
        window = list(self._windows[speaker_id])
        focus, stance, confidence = _aggregate_signals(window)

        # AND-based neutral band: both axes must be ambiguous for Undetermined.
        # This prevents single-axis speakers (e.g., pure Logic with no
        # advocacy/analysis signals) from being stuck as Undetermined.
        # self_assessment.map_to_archetype uses OR logic (stricter) which is
        # appropriate for structured assessments but too aggressive for
        # sparse real-speech regex signals.
        focus_in_band = abs(focus) <= self._neutral_band
        stance_in_band = abs(stance) <= self._neutral_band

        if focus_in_band and stance_in_band:
            superpower: SuperpowerType | Literal["Undetermined"] = "Undetermined"
        else:
            superpower = classify_from_scores(focus, stance)

        return WindowClassification(
            speaker_id=speaker_id,
            superpower=superpower,
            confidence=confidence,
            focus_score=focus,
            stance_score=stance,
            utterance_count=len(window),
        )


# ---------------------------------------------------------------------------
# User behavioral observer
# ---------------------------------------------------------------------------

class UserBehaviorObserver:
    """
    Accumulates the app user's utterances across a session and produces a
    SessionObservation for apply_session_observation().

    Non-user utterances are silently ignored — only the user's own speech
    contributes to their behavioral profile update.

    Usage:
        observer = UserBehaviorObserver(user_speaker="speaker_0")
        for utt in transcript:
            observer.add_utterance(utt["speaker"], utt["text"])
        obs = observer.get_observation(session_id="sess-abc", context="board")
        # obs → apply_session_observation(user, ctx_profiles, obs)
        observer.reset()
    """

    def __init__(self, user_speaker: str) -> None:
        self._user_speaker = user_speaker
        self._signals: list[UtteranceSignals] = []

    @property
    def utterance_count(self) -> int:
        """Number of user utterances processed so far in this session."""
        return len(self._signals)

    def add_utterance(self, speaker_id: str, text: str) -> None:
        """
        Process one utterance.  Non-user utterances are ignored.
        """
        if speaker_id != self._user_speaker:
            return
        self._signals.append(_score_utterance(text))

    def get_observation(self, session_id: str, context: str) -> SessionObservation:
        """
        Produce a SessionObservation from all user utterances seen so far.

        If no user utterances were recorded the observation has
        obs_confidence=0.0, giving it zero weight in the EWMA update.
        """
        focus_score, stance_score, _ = _aggregate_signals(self._signals)
        return SessionObservation(
            session_id=session_id,
            context=context,
            focus_score=focus_score,
            stance_score=stance_score,
            utterance_count=len(self._signals),
            obs_confidence=_obs_confidence(len(self._signals)),
        )

    def reset(self) -> None:
        """Clear accumulated signals. Call between sessions."""
        self._signals.clear()
