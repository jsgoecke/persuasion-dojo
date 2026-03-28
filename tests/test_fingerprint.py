"""
Integration tests for the behavioral fingerprint system.

Exercises the full pipeline: profiler utterance logging → ELM episode tracking →
identity resolution → evidence collection → fingerprint assembly → pattern
derivation. Verifies that profiles evolve correctly across multiple sessions.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from backend.database import get_db_session, init_db, override_engine
from backend.elm_detector import ELMDetector
from backend.fingerprint import BehavioralFingerprint, assemble_fingerprint, _derive_patterns
from backend.identity import resolve_speaker
from backend.models import (
    BehavioralEvidence,
    MeetingSession,
    Participant,
    ParticipantContextProfile,
    SessionParticipantObservation,
    User,
    apply_participant_observation,
)
from backend.profiler import ParticipantProfiler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    override_engine(engine)
    await init_db()
    yield engine
    await engine.dispose()


USER_ID = "test-user-001"


async def _ensure_user(user_id: str = USER_ID) -> None:
    async with get_db_session() as s:
        existing = await s.get(User, user_id)
        if not existing:
            s.add(User(id=user_id))


async def _create_session(title: str = "test", context: str = "team") -> str:
    async with get_db_session() as s:
        session = MeetingSession(user_id=USER_ID, context=context, title=title)
        s.add(session)
        await s.flush()
        return session.id


async def _create_participant(name: str) -> str:
    async with get_db_session() as s:
        p = Participant(user_id=USER_ID, name=name, ps_state="active")
        s.add(p)
        await s.flush()
        return p.id


# ---------------------------------------------------------------------------
# Sample utterances — designed to trigger specific signals
# ---------------------------------------------------------------------------

ARCHITECT_UTTERANCES = [
    "Let me walk through the data — retention improved 40% quarter over quarter.",
    "The metrics show a clear trend. Specifically, our conversion rate increased by 12 basis points.",
    "Based on the evidence, I recommend we benchmark against the Q3 numbers before deciding.",
    "Can you show me the statistical significance? I need to validate these numbers.",
    "The framework should include KPIs for each milestone — we need to measure outcomes precisely.",
]

FIRESTARTER_UTTERANCES = [
    "Imagine what this could mean for the company — we'd be the first to market!",
    "I'm so excited about this direction. When I was at my last company, we transformed the industry.",
    "Let me paint a picture — visualize our customers experiencing this for the first time.",
    "We should absolutely move forward. I'm passionate about making this happen!",
    "The vision is clear — we need to commit now and lead the charge.",
]

DEFENSIVE_UTTERANCES = [
    "I disagree with that assessment completely.",
    "That's not right — we've always done it this way and it works.",
    "I don't think you understand the situation here.",
    "With all due respect, that doesn't make sense for our team.",
    "I take issue with that — your data is misleading.",
]

BUILDING_ON_UTTERANCES = [
    "Building on that idea, we could also add self-serve onboarding.",
    "To your point, the data supports this direction completely.",
    "That makes sense. Let's move forward with a pilot.",
    "Great idea — I'm on board with this approach.",
    "Agreed. Let's coordinate our resources for a joint launch.",
]

RESISTANCE_UTTERANCES = [
    "But that won't work for our current infrastructure.",
    "However I think we need a completely different direction here.",
    "I don't think the data supports this conclusion at all.",
    "My concern is that this will take too long and cost too much.",
    "I'm not convinced this is the right approach for the team.",
]


# ---------------------------------------------------------------------------
# Profiler: utterance log + key evidence
# ---------------------------------------------------------------------------

class TestProfilerUtteranceLog:
    def test_log_accumulates_all_utterances(self):
        profiler = ParticipantProfiler()
        for utt in ARCHITECT_UTTERANCES:
            profiler.add_utterance("alice", utt)

        all_signals = profiler.get_all_signals("alice")
        assert len(all_signals) == len(ARCHITECT_UTTERANCES)

    def test_key_evidence_ranked_by_strength(self):
        profiler = ParticipantProfiler()
        # Mix weak and strong signal utterances
        profiler.add_utterance("bob", "Hello, how are you?")
        profiler.add_utterance("bob", "The metrics show a clear trend. Specifically, our KPIs improved.")
        profiler.add_utterance("bob", "Ok sure.")

        evidence = profiler.get_key_evidence("bob", top_n=2)
        assert len(evidence) == 2
        # Strongest signal first
        assert evidence[0]["strength"] >= evidence[1]["strength"]
        assert evidence[0]["strength"] > 0

    def test_key_evidence_includes_text_and_signals(self):
        profiler = ParticipantProfiler()
        profiler.add_utterance("alice", "The data shows a 40% increase in retention metrics.")

        evidence = profiler.get_key_evidence("alice", top_n=1)
        assert len(evidence) == 1
        assert "data" in evidence[0]["text"].lower()
        assert "logic" in evidence[0]["signals"]
        assert "narrative" in evidence[0]["signals"]

    def test_log_survives_window_eviction(self):
        """Utterance log keeps all utterances even when the 5-utterance window evicts."""
        profiler = ParticipantProfiler(window_size=3)
        for utt in ARCHITECT_UTTERANCES:  # 5 utterances > window of 3
            profiler.add_utterance("alice", utt)

        all_signals = profiler.get_all_signals("alice")
        assert len(all_signals) == 5  # All preserved in log

        # Window only has 3
        cls = profiler.get_classification("alice")
        assert cls is not None
        assert cls.utterance_count == 3

    def test_reset_clears_log(self):
        profiler = ParticipantProfiler()
        profiler.add_utterance("alice", "Some data here.")
        profiler.reset()
        assert profiler.get_all_signals("alice") == []
        assert profiler.get_key_evidence("alice") == []

    def test_unknown_speaker_returns_empty(self):
        profiler = ParticipantProfiler()
        assert profiler.get_key_evidence("nonexistent") == []
        assert profiler.get_all_signals("nonexistent") == []


# ---------------------------------------------------------------------------
# ELM detector: episode history
# ---------------------------------------------------------------------------

class TestELMEpisodeHistory:
    def test_ego_threat_logged(self):
        detector = ELMDetector(user_speaker="speaker_0")
        for utt in DEFENSIVE_UTTERANCES[:3]:
            detector.process_utterance("speaker_1", utt)

        history = detector.get_episode_history("speaker_1")
        assert "ego_threat" in history

    def test_no_episodes_for_neutral(self):
        detector = ELMDetector(user_speaker="speaker_0")
        detector.process_utterance("speaker_1", "Let me think about that.")
        detector.process_utterance("speaker_1", "Can you explain more about the timeline?")

        history = detector.get_episode_history("speaker_1")
        assert history == []

    def test_shortcut_logged_after_streak(self):
        detector = ELMDetector(user_speaker="speaker_0")
        # 3 consecutive pure agreements → shortcut
        detector.process_utterance("speaker_1", "Yes, agreed.")
        detector.process_utterance("speaker_1", "Sure, sounds good.")
        detector.process_utterance("speaker_1", "Absolutely, makes sense.")

        history = detector.get_episode_history("speaker_1")
        assert "shortcut" in history

    def test_reset_clears_history(self):
        detector = ELMDetector(user_speaker="speaker_0")
        detector.process_utterance("speaker_1", "I disagree completely.")
        detector.reset()
        assert detector.get_episode_history("speaker_1") == []


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------

class TestIdentityResolution:
    @pytest.mark.asyncio
    async def test_exact_match(self, db):
        await _ensure_user()
        pid = await _create_participant("Sarah Chen")

        async with get_db_session() as s:
            resolved = await resolve_speaker(s, USER_ID, "Sarah Chen")
            assert resolved is not None
            assert resolved.id == pid

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self, db):
        await _ensure_user()
        pid = await _create_participant("Sarah Chen")

        async with get_db_session() as s:
            resolved = await resolve_speaker(s, USER_ID, "sarah chen")
            assert resolved is not None
            assert resolved.id == pid

    @pytest.mark.asyncio
    async def test_fuzzy_match(self, db):
        await _ensure_user()
        pid = await _create_participant("Sarah Chen")

        async with get_db_session() as s:
            # "Sara Chen" fuzzy matches "Sarah Chen" (ratio > 0.85)
            resolved = await resolve_speaker(s, USER_ID, "Sara Chen")
            assert resolved is not None
            assert resolved.id == pid

    @pytest.mark.asyncio
    async def test_no_match_for_different_name(self, db):
        await _ensure_user()
        await _create_participant("Sarah Chen")

        async with get_db_session() as s:
            resolved = await resolve_speaker(s, USER_ID, "Bob Johnson")
            assert resolved is None

    @pytest.mark.asyncio
    async def test_speaker_n_returns_none(self, db):
        await _ensure_user()
        await _create_participant("Sarah Chen")

        async with get_db_session() as s:
            resolved = await resolve_speaker(s, USER_ID, "speaker_1")
            assert resolved is None

    @pytest.mark.asyncio
    async def test_empty_name_returns_none(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            assert await resolve_speaker(s, USER_ID, "") is None


# ---------------------------------------------------------------------------
# Behavioral evidence storage
# ---------------------------------------------------------------------------

class TestBehavioralEvidence:
    @pytest.mark.asyncio
    async def test_evidence_stored_with_key_utterances(self, db):
        await _ensure_user()
        pid = await _create_participant("Alice")
        sid = await _create_session()

        profiler = ParticipantProfiler()
        for utt in ARCHITECT_UTTERANCES:
            profiler.add_utterance("speaker_1", utt)

        key_ev = profiler.get_key_evidence("speaker_1", top_n=3)

        async with get_db_session() as s:
            s.add(BehavioralEvidence(
                session_id=sid,
                participant_id=pid,
                key_utterances=json.dumps(key_ev),
                elm_states=json.dumps(["ego_threat"]),
                uptake_count=3,
                resistance_count=1,
                question_types=json.dumps({"challenging": 2, "clarifying": 1, "confirmatory": 0}),
                convergence_direction=0.15,
                pronoun_shift=0.1,
                context="team",
            ))

        async with get_db_session() as s:
            p = await s.get(Participant, pid)
            assert p is not None
            # Evidence relationship should be accessible
            from sqlalchemy import select
            result = await s.execute(
                select(BehavioralEvidence).where(BehavioralEvidence.participant_id == pid)
            )
            ev = result.scalar_one()
            assert ev.uptake_count == 3
            assert ev.resistance_count == 1
            assert json.loads(ev.elm_states) == ["ego_threat"]
            assert len(json.loads(ev.key_utterances)) == 3
            assert json.loads(ev.question_types)["challenging"] == 2


# ---------------------------------------------------------------------------
# Pattern derivation
# ---------------------------------------------------------------------------

class TestPatternDerivation:
    def _make_evidence(self, **kwargs) -> BehavioralEvidence:
        defaults = dict(
            session_id="s1", participant_id="p1",
            key_utterances="[]", elm_states="[]",
            uptake_count=0, resistance_count=0,
            question_types='{"challenging":0,"clarifying":0,"confirmatory":0}',
            convergence_direction=0.0, pronoun_shift=0.0,
            context="team",
        )
        defaults.update(kwargs)
        return BehavioralEvidence(**defaults)

    def test_high_uptake_produces_collaborator_pattern(self):
        evidence = [
            self._make_evidence(uptake_count=5, resistance_count=1),
            self._make_evidence(uptake_count=4, resistance_count=0),
        ]
        patterns = _derive_patterns(evidence, [], [])
        assert any("build on ideas" in p for p in patterns)

    def test_high_resistance_produces_pushback_pattern(self):
        evidence = [
            self._make_evidence(uptake_count=0, resistance_count=5),
            self._make_evidence(uptake_count=1, resistance_count=4),
        ]
        patterns = _derive_patterns(evidence, [], [])
        assert any("pushes back" in p or "evidence" in p for p in patterns)

    def test_ego_threat_tendency_produces_warning(self):
        evidence = [
            self._make_evidence(elm_states='["ego_threat"]'),
            self._make_evidence(elm_states='["ego_threat"]'),
            self._make_evidence(elm_states='[]'),
        ]
        patterns = _derive_patterns(evidence, [], [])
        assert any("defensive" in p for p in patterns)

    def test_shortcut_tendency_produces_warning(self):
        evidence = [
            self._make_evidence(elm_states='["shortcut"]'),
            self._make_evidence(elm_states='["shortcut"]'),
            self._make_evidence(elm_states='[]'),
        ]
        patterns = _derive_patterns(evidence, [], [])
        assert any("agree" in p.lower() and "quickly" in p.lower() for p in patterns)

    def test_challenging_questions_produce_pattern(self):
        evidence = [
            self._make_evidence(question_types='{"challenging":5,"clarifying":1,"confirmatory":0}'),
            self._make_evidence(question_types='{"challenging":3,"clarifying":0,"confirmatory":1}'),
        ]
        patterns = _derive_patterns(evidence, [], [])
        assert any("challenging" in p for p in patterns)

    def test_positive_convergence_produces_patience_pattern(self):
        evidence = [
            self._make_evidence(convergence_direction=0.2),
            self._make_evidence(convergence_direction=0.15),
        ]
        patterns = _derive_patterns(evidence, [], [])
        assert any("patience" in p or "converges" in p for p in patterns)

    def test_negative_convergence_produces_early_action_pattern(self):
        evidence = [
            self._make_evidence(convergence_direction=-0.2),
            self._make_evidence(convergence_direction=-0.15),
        ]
        patterns = _derive_patterns(evidence, [], [])
        assert any("diverge" in p or "early" in p for p in patterns)

    def test_no_evidence_returns_empty(self):
        assert _derive_patterns([], [], []) == []

    def test_insufficient_data_no_spurious_patterns(self):
        """One session with low counts shouldn't generate uptake/resistance patterns."""
        evidence = [self._make_evidence(uptake_count=1, resistance_count=0)]
        patterns = _derive_patterns(evidence, [], [])
        # Should not have uptake pattern (total < 3)
        assert not any("build on" in p for p in patterns)


# ---------------------------------------------------------------------------
# Full fingerprint assembly (DB integration)
# ---------------------------------------------------------------------------

class TestFingerprintAssembly:
    @pytest.mark.asyncio
    async def test_basic_fingerprint(self, db):
        await _ensure_user()
        pid = await _create_participant("Alice")
        sid = await _create_session()

        async with get_db_session() as s:
            p = await s.get(Participant, pid)
            p.obs_archetype = "Architect"
            p.obs_confidence = 0.75
            p.obs_focus = 60.0
            p.obs_stance = -10.0
            p.obs_sessions = 3

            s.add(BehavioralEvidence(
                session_id=sid, participant_id=pid,
                key_utterances=json.dumps([
                    {"text": "The data shows 40% improvement.", "signals": {"logic": 3}, "strength": 3}
                ]),
                elm_states='[]',
                uptake_count=4, resistance_count=1,
                question_types='{"challenging":1,"clarifying":2,"confirmatory":0}',
                convergence_direction=0.12,
                context="team",
            ))

        async with get_db_session() as s:
            fp = await assemble_fingerprint(s, pid)

        assert fp is not None
        assert fp.name == "Alice"
        assert fp.archetype == "Architect"
        assert fp.sessions_observed == 3
        assert fp.avg_convergence > 0
        assert len(fp.notable_utterances) == 1
        assert fp.notable_utterances[0].text == "The data shows 40% improvement."

    @pytest.mark.asyncio
    async def test_fingerprint_nonexistent_participant(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            fp = await assemble_fingerprint(s, "nonexistent-id")
        assert fp is None

    @pytest.mark.asyncio
    async def test_to_dict_serializable(self, db):
        await _ensure_user()
        pid = await _create_participant("Bob")

        async with get_db_session() as s:
            fp = await assemble_fingerprint(s, pid)

        assert fp is not None
        d = fp.to_dict()
        # Should be JSON-serializable
        serialized = json.dumps(d)
        assert "Bob" in serialized

    @pytest.mark.asyncio
    async def test_coaching_summary(self, db):
        await _ensure_user()
        pid = await _create_participant("Carol")
        sid = await _create_session()

        async with get_db_session() as s:
            p = await s.get(Participant, pid)
            p.obs_archetype = "Firestarter"
            p.obs_sessions = 5

            s.add(BehavioralEvidence(
                session_id=sid, participant_id=pid,
                key_utterances="[]",
                elm_states='["ego_threat"]',
                uptake_count=1, resistance_count=5,
                question_types='{"challenging":4,"clarifying":1,"confirmatory":0}',
                convergence_direction=-0.1,
                context="board",
            ))

        async with get_db_session() as s:
            fp = await assemble_fingerprint(s, pid)

        assert fp is not None
        summary = fp.coaching_summary()
        assert "Firestarter" in summary


# ---------------------------------------------------------------------------
# Profile evolution across sessions
# ---------------------------------------------------------------------------

class TestProfileEvolution:
    """
    Simulates multiple sessions with the same participant to verify
    that the fingerprint evolves: EWMA axes shift, evidence accumulates,
    and patterns update.
    """

    async def _run_session(
        self,
        participant_id: str,
        session_context: str,
        utterances: list[str],
        user_speaker: str = "speaker_0",
    ) -> str:
        """Simulate a session: run profiler + ELM + store evidence."""
        sid = await _create_session(context=session_context)

        profiler = ParticipantProfiler()
        elm = ELMDetector(user_speaker=user_speaker)

        for utt in utterances:
            profiler.add_utterance("speaker_1", utt)
            elm.process_utterance("speaker_1", utt)

        cls = profiler.get_classification("speaker_1")
        key_ev = profiler.get_key_evidence("speaker_1", top_n=3)
        elm_episodes = elm.get_episode_history("speaker_1")

        async with get_db_session() as s:
            p = await s.get(Participant, participant_id)

            # EWMA update
            from sqlalchemy import select
            ctx_result = await s.execute(
                select(ParticipantContextProfile).where(
                    ParticipantContextProfile.participant_id == participant_id
                )
            )
            ctx_map = {cp.context: cp for cp in ctx_result.scalars()}

            if session_context not in ctx_map:
                cp = ParticipantContextProfile(
                    participant_id=participant_id, context=session_context
                )
                s.add(cp)
                await s.flush()
                ctx_map[session_context] = cp

            apply_participant_observation(
                p, ctx_map,
                focus_score=cls.focus_score,
                stance_score=cls.stance_score,
                confidence=cls.confidence,
                context=session_context,
            )

            # Audit trail
            s.add(SessionParticipantObservation(
                session_id=sid, participant_id=participant_id,
                focus_score=cls.focus_score, stance_score=cls.stance_score,
                confidence=cls.confidence, archetype=cls.superpower,
                utterance_count=cls.utterance_count, context=session_context,
            ))

            # Behavioral evidence
            # Count uptake/resistance
            from backend.signals import _tokenize_text_for_phrases, _UPTAKE_PHRASES, _RESISTANCE_PHRASES
            up, res = 0, 0
            for utt in utterances:
                tok = _tokenize_text_for_phrases(utt)
                if any(tok.startswith(p) or (", " + p) in tok for p in _UPTAKE_PHRASES):
                    up += 1
                if any(tok.startswith(p) or (", " + p) in tok for p in _RESISTANCE_PHRASES):
                    res += 1

            s.add(BehavioralEvidence(
                session_id=sid, participant_id=participant_id,
                key_utterances=json.dumps(key_ev),
                elm_states=json.dumps(list(set(elm_episodes))),
                uptake_count=up,
                resistance_count=res,
                question_types='{"challenging":0,"clarifying":0,"confirmatory":0}',
                convergence_direction=0.0,
                context=session_context,
            ))

        return sid

    @pytest.mark.asyncio
    async def test_archetype_evolves_across_sessions(self, db):
        """
        Session 1: Alice speaks as Architect (logic-heavy).
        Session 2: Alice speaks as Firestarter (narrative-heavy).
        The EWMA axes should shift between sessions.
        """
        await _ensure_user()
        pid = await _create_participant("Alice")

        # Session 1: Architect behavior
        await self._run_session(pid, "team", ARCHITECT_UTTERANCES)

        async with get_db_session() as s:
            p = await s.get(Participant, pid)
            focus_after_s1 = p.obs_focus
            assert focus_after_s1 is not None
            assert focus_after_s1 > 0  # Logic-positive

        # Session 2: Firestarter behavior
        await self._run_session(pid, "board", FIRESTARTER_UTTERANCES)

        async with get_db_session() as s:
            p = await s.get(Participant, pid)
            focus_after_s2 = p.obs_focus
            assert focus_after_s2 is not None
            # Should have shifted toward narrative (lower focus)
            assert focus_after_s2 < focus_after_s1
            assert p.obs_sessions == 2

    @pytest.mark.asyncio
    async def test_evidence_accumulates_across_sessions(self, db):
        """Multiple sessions produce multiple BehavioralEvidence rows."""
        await _ensure_user()
        pid = await _create_participant("Bob")

        await self._run_session(pid, "team", ARCHITECT_UTTERANCES)
        await self._run_session(pid, "board", FIRESTARTER_UTTERANCES)
        await self._run_session(pid, "team", BUILDING_ON_UTTERANCES)

        async with get_db_session() as s:
            from sqlalchemy import select
            result = await s.execute(
                select(BehavioralEvidence).where(BehavioralEvidence.participant_id == pid)
            )
            evidence_rows = list(result.scalars())
            assert len(evidence_rows) == 3

    @pytest.mark.asyncio
    async def test_context_variations_appear_in_fingerprint(self, db):
        """Different contexts produce context_variations in the fingerprint."""
        await _ensure_user()
        pid = await _create_participant("Carol")

        await self._run_session(pid, "team", ARCHITECT_UTTERANCES)
        await self._run_session(pid, "board", FIRESTARTER_UTTERANCES)

        async with get_db_session() as s:
            fp = await assemble_fingerprint(s, pid)

        assert fp is not None
        assert len(fp.context_variations) == 2
        contexts = {cv.context for cv in fp.context_variations}
        assert "team" in contexts
        assert "board" in contexts

    @pytest.mark.asyncio
    async def test_elm_tendencies_accumulate(self, db):
        """ELM episodes across sessions accumulate in the fingerprint."""
        await _ensure_user()
        pid = await _create_participant("Dave")

        # Session 1: defensive
        await self._run_session(pid, "team", DEFENSIVE_UTTERANCES)
        # Session 2: also defensive
        await self._run_session(pid, "board", DEFENSIVE_UTTERANCES[:3] + ARCHITECT_UTTERANCES[:2])

        async with get_db_session() as s:
            fp = await assemble_fingerprint(s, pid)

        assert fp is not None
        assert fp.elm_tendencies.get("ego_threat", 0) >= 1

    @pytest.mark.asyncio
    async def test_patterns_evolve_with_more_data(self, db):
        """
        After enough sessions with consistent behavior, patterns should emerge.
        """
        await _ensure_user()
        pid = await _create_participant("Eve")

        # 3 sessions of building-on behavior
        for ctx in ["team", "board", "1:1"]:
            await self._run_session(pid, ctx, BUILDING_ON_UTTERANCES)

        async with get_db_session() as s:
            fp = await assemble_fingerprint(s, pid)

        assert fp is not None
        # High uptake across sessions should produce a collaborator pattern
        assert any("build on" in p or "collaborat" in p for p in fp.patterns)

    @pytest.mark.asyncio
    async def test_notable_utterances_ranked_across_sessions(self, db):
        """Notable utterances are ranked by strength across all sessions."""
        await _ensure_user()
        pid = await _create_participant("Frank")

        # Session 1: weak signals
        await self._run_session(pid, "team", ["Ok.", "Sure.", "Hello.", "Fine.", "Alright."])
        # Session 2: strong architect signals
        await self._run_session(pid, "board", ARCHITECT_UTTERANCES)

        async with get_db_session() as s:
            fp = await assemble_fingerprint(s, pid)

        assert fp is not None
        if fp.notable_utterances:
            # Strongest utterance should be from the architect session
            assert fp.notable_utterances[0].strength > 0

    @pytest.mark.asyncio
    async def test_resistance_dominant_participant(self, db):
        """A participant who consistently resists should have pushback pattern."""
        await _ensure_user()
        pid = await _create_participant("Grace")

        for ctx in ["team", "board", "1:1"]:
            await self._run_session(pid, ctx, RESISTANCE_UTTERANCES)

        async with get_db_session() as s:
            fp = await assemble_fingerprint(s, pid)

        assert fp is not None
        assert fp.avg_uptake_ratio < 0.3  # Resistance-dominant
        assert any("push" in p or "evidence" in p for p in fp.patterns)

    @pytest.mark.asyncio
    async def test_identity_resolution_across_sessions(self, db):
        """
        When a slightly different name appears in a later session,
        identity resolution should match to the existing participant.
        """
        await _ensure_user()
        pid = await _create_participant("Sarah Chen")

        # Resolve with exact name
        async with get_db_session() as s:
            resolved = await resolve_speaker(s, USER_ID, "Sarah Chen")
            assert resolved is not None
            assert resolved.id == pid

        # Resolve with fuzzy name
        async with get_db_session() as s:
            resolved = await resolve_speaker(s, USER_ID, "Sara Chen")
            assert resolved is not None
            assert resolved.id == pid

        # Resolve with case difference
        async with get_db_session() as s:
            resolved = await resolve_speaker(s, USER_ID, "SARAH CHEN")
            assert resolved is not None
            assert resolved.id == pid
