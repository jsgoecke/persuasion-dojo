"""
Tests for Phase 1: Per-Person Real-Time Coaching Signal Chain.

Coverage:
  - _resolve_speaker_name: valid indices, out-of-range, empty list, direct speaker_id match
  - System prompt includes "Always name the specific person"
  - ELM prompt includes counterpart name when available
  - General prompt uses resolved names in transcript section
  - User archetype auto-detection mid-session
  - Session debrief includes per-participant data
  - Retro debrief has same 5-section structure
"""

from __future__ import annotations

import asyncio
import collections
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.coaching_engine import CoachingEngine, CoachingPrompt, _SYSTEM_PROMPT
from backend.elm_detector import ELMEvent
from backend.profiler import (
    ParticipantProfiler,
    UserBehaviorObserver,
    WindowClassification,
    _aggregate_signals,
    _PROFILER_NEUTRAL_BAND,
    classify_from_scores,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RESPONSE_TEXT = "Sarah's defensive — ask what data would change her mind."


def make_mock_client(text: str = _RESPONSE_TEXT) -> Any:
    content = MagicMock()
    content.text = text
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


def make_engine(
    client: Any = None,
    user_archetype: str = "Firestarter",
    participants: list[dict] | None = None,
) -> CoachingEngine:
    return CoachingEngine(
        user_speaker="user",
        anthropic_client=client or make_mock_client(),
        elm_cadence_floor_s=0.0,
        general_cadence_floor_s=0.0,
        haiku_timeout_s=999.0,
        user_archetype=user_archetype,
        participants=participants,
    )


# ---------------------------------------------------------------------------
# _resolve_speaker_name tests
# ---------------------------------------------------------------------------

class TestResolveSpeakerName:
    """Tests for CoachingEngine._resolve_speaker_name."""

    def test_empty_participants_returns_empty(self):
        engine = make_engine(participants=[])
        assert engine._resolve_speaker_name("counterpart_0") == ""

    def test_none_participants_returns_empty(self):
        engine = make_engine(participants=None)
        assert engine._resolve_speaker_name("counterpart_0") == ""

    def test_counterpart_index_lookup(self):
        engine = make_engine(participants=[
            {"name": "Sarah Chen", "archetype": "Architect"},
            {"name": "Mike Johnson", "archetype": "Firestarter"},
        ])
        assert engine._resolve_speaker_name("counterpart_0") == "Sarah Chen"
        assert engine._resolve_speaker_name("counterpart_1") == "Mike Johnson"

    def test_speaker_index_lookup(self):
        """speaker_1 maps to participants[0] (speaker_0 is user)."""
        engine = make_engine(participants=[
            {"name": "Sarah Chen", "archetype": "Architect"},
        ])
        assert engine._resolve_speaker_name("speaker_1") == "Sarah Chen"

    def test_out_of_range_returns_empty(self):
        engine = make_engine(participants=[
            {"name": "Sarah Chen", "archetype": "Architect"},
        ])
        assert engine._resolve_speaker_name("counterpart_5") == ""

    def test_invalid_format_returns_empty(self):
        engine = make_engine(participants=[
            {"name": "Sarah Chen", "archetype": "Architect"},
        ])
        assert engine._resolve_speaker_name("unknown_speaker") == ""

    def test_direct_speaker_id_match(self):
        engine = make_engine(participants=[
            {"name": "Sarah Chen", "archetype": "Architect", "speaker_id": "counterpart_0"},
        ])
        assert engine._resolve_speaker_name("counterpart_0") == "Sarah Chen"

    def test_missing_name_field_returns_empty(self):
        engine = make_engine(participants=[
            {"archetype": "Architect"},
        ])
        assert engine._resolve_speaker_name("counterpart_0") == ""


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    """Verify system prompt instructs Haiku to name specific people."""

    def test_system_prompt_names_instruction(self):
        assert "Always name the specific person" in _SYSTEM_PROMPT

    def test_system_prompt_example_uses_name(self):
        assert "Sarah" in _SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# ELM prompt includes counterpart name
# ---------------------------------------------------------------------------

class TestELMPromptWithName:
    """Verify ELM prompts include counterpart name when available."""

    @pytest.mark.asyncio
    async def test_elm_prompt_includes_name(self):
        client = make_mock_client()
        engine = make_engine(
            client=client,
            user_archetype="Firestarter",
            participants=[
                {"name": "Sarah Chen", "archetype": "Architect"},
            ],
        )
        event = ELMEvent(
            speaker_id="counterpart_0",
            state="ego_threat",
            utterance="I disagree completely",
            evidence=["I disagree completely"],
        )
        profile = WindowClassification(
            speaker_id="counterpart_0",
            superpower="Architect",
            confidence=0.7,
            focus_score=50.0,
            stance_score=-30.0,
            utterance_count=5,
        )
        await engine.process(
            elm_event=event,
            participant_profile=profile,
            user_is_speaking=False,
        )
        # Check the user message sent to Haiku includes "Sarah Chen"
        call_args = client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "Sarah Chen" in user_msg
        assert "Sarah Chen (Architect)" in user_msg

    @pytest.mark.asyncio
    async def test_elm_prompt_without_name_still_works(self):
        client = make_mock_client()
        engine = make_engine(client=client, participants=[])
        event = ELMEvent(
            speaker_id="counterpart_0",
            state="ego_threat",
            utterance="I disagree completely",
            evidence=["I disagree completely"],
        )
        await engine.process(elm_event=event, user_is_speaking=False)
        call_args = client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "the counterpart" in user_msg


# ---------------------------------------------------------------------------
# General prompt uses resolved names in transcript
# ---------------------------------------------------------------------------

class TestGeneralPromptNames:
    """Verify _general_prompt uses resolved names in the transcript section."""

    @pytest.mark.asyncio
    async def test_transcript_labels_use_resolved_names(self):
        client = make_mock_client()
        engine = make_engine(
            client=client,
            participants=[
                {"name": "Sarah Chen", "archetype": "Architect"},
            ],
        )
        transcript = [
            {"speaker": "user", "text": "Let me explain the plan"},
            {"speaker": "counterpart_0", "text": "I need to see the data first"},
        ]
        await engine.process(
            recent_transcript=transcript,
            user_is_speaking=False,
        )
        call_args = client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "Sarah Chen: I need to see the data first" in user_msg
        assert "You: Let me explain the plan" in user_msg
        # Should NOT contain raw speaker IDs
        assert "counterpart_0" not in user_msg


# ---------------------------------------------------------------------------
# User archetype auto-detection
# ---------------------------------------------------------------------------

class TestUserArchetypeAutoDetection:
    """Test mid-session user archetype classification."""

    def test_aggregate_signals_produces_archetype(self):
        """Verify the profiler's signal aggregation can classify from accumulated data."""
        from backend.profiler import _score_utterance

        # Feed data-driven utterances (Logic + Analysis = Architect)
        texts = [
            "The data shows a 15% increase in conversion",
            "Based on the metrics and our benchmark analysis",
            "What does the research tell us about this hypothesis?",
            "I'd like to understand the correlation between these variables",
            "Looking at the chart, the trend is specifically downward",
        ]
        signals = [_score_utterance(t) for t in texts]
        focus, stance, confidence = _aggregate_signals(signals)
        # Logic-heavy signals should give positive focus (Architect/Inquisitor territory)
        assert focus > _PROFILER_NEUTRAL_BAND

    def test_update_user_archetype_changes_engine(self):
        """Integration: SessionPipeline._update_user_archetype updates CoachingEngine."""
        # Import here to avoid circular issues
        from backend.main import SessionPipeline

        engine = make_engine(user_archetype="Unknown")
        pipeline = SessionPipeline(
            session_id="test-session",
            user_id="test-user",
            user_speaker="user",
            coaching_engine=engine,
        )

        # Feed data-heavy utterances as the user
        data_utterances = [
            "The data shows 15% improvement in our metrics",
            "Based on our benchmark analysis, the evidence is clear",
            "What does the research suggest about this hypothesis?",
            "I'd like to understand the statistical correlation here",
            "Looking at the chart specifically, the trend is measurably downward",
        ]
        for text in data_utterances:
            pipeline.observer.add_utterance("user", text)

        # Trigger the auto-detection
        pipeline._update_user_archetype()

        # Should have classified to something other than "Unknown"
        assert engine._user_archetype != "Unknown"
        # Logic-heavy + analysis-heavy signals → Architect
        assert engine._user_archetype == "Architect"


# ---------------------------------------------------------------------------
# Session debrief includes per-participant data
# ---------------------------------------------------------------------------

class TestSessionDebriefParticipants:
    """Verify _generate_session_debrief includes per-participant profiles in prompt."""

    @pytest.mark.asyncio
    async def test_debrief_prompt_includes_participant_profiles(self):
        """When participants are provided, the Opus prompt should name each one."""
        from backend.main import _generate_session_debrief

        utterances = [
            {"speaker": "user", "text": "Let me walk you through the proposal"},
            {"speaker": "counterpart_0", "text": "I need to see the numbers before I commit"},
            {"speaker": "counterpart_1", "text": "This is exciting, let's move fast"},
            {"speaker": "user", "text": "The data shows 20% growth"},
        ]
        scores = {
            "persuasion_score": 72,
            "timing_score": 22,
            "ego_safety_score": 25,
            "convergence_score": 25,
        }
        participants = [
            {
                "speaker_id": "counterpart_0",
                "name": "Sarah Chen",
                "archetype": "Architect",
                "confidence": 0.75,
                "focus_score": 60.0,
                "stance_score": -40.0,
                "utterance_count": 8,
                "key_evidence": [{"text": "I need to see the numbers", "signals": {}, "strength": 3}],
                "elm_episodes": ["ego_threat"],
            },
            {
                "speaker_id": "counterpart_1",
                "name": "Mike Johnson",
                "archetype": "Firestarter",
                "confidence": 0.65,
                "focus_score": -50.0,
                "stance_score": 45.0,
                "utterance_count": 6,
                "key_evidence": [{"text": "This is exciting", "signals": {}, "strength": 2}],
                "elm_episodes": [],
            },
        ]

        # Mock the Anthropic client
        with patch("backend.main._anthropic.AsyncAnthropic") as mock_cls:
            content = MagicMock()
            content.text = "PARTICIPANT MAP\nSarah Chen (Architect)..."
            response = MagicMock()
            response.content = [content]
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=response)
            mock_cls.return_value = mock_client

            with patch("backend.main._load_settings", return_value={"anthropic_api_key": "test-key"}):
                with patch("backend.main.get_db_session") as mock_db:
                    mock_session = AsyncMock()
                    mock_session.get = AsyncMock(return_value=MagicMock())
                    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                    mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

                    await _generate_session_debrief(
                        "test-session", utterances, scores,
                        participants=participants,
                        user_archetype="Inquisitor",
                    )

            # Verify the prompt sent to Opus includes participant names
            call_args = mock_client.messages.create.call_args
            prompt = call_args.kwargs["messages"][0]["content"]
            assert "Sarah Chen" in prompt
            assert "Mike Johnson" in prompt
            assert "Architect" in prompt
            assert "Firestarter" in prompt
            # Should have 5-section structure
            assert "PARTICIPANT MAP" in prompt
            assert "INTERACTION ANALYSIS" in prompt
            assert "KEY MOMENTS" in prompt
            assert "WHAT YOU DID WELL" in prompt
            assert "STRATEGIC PLAYBOOK" in prompt
            # Should include user archetype
            assert "Inquisitor" in prompt
            # Should include ELM episode data
            assert "ego_threat" in prompt

    @pytest.mark.asyncio
    async def test_debrief_without_participants_still_works(self):
        """Debrief should work when no participant profiles are available."""
        from backend.main import _generate_session_debrief

        utterances = [
            {"speaker": "user", "text": "Hello"},
            {"speaker": "counterpart_0", "text": "Hi there"},
        ]
        scores = {
            "persuasion_score": 50,
            "timing_score": 15,
            "ego_safety_score": 15,
            "convergence_score": 20,
        }

        with patch("backend.main._anthropic.AsyncAnthropic") as mock_cls:
            content = MagicMock()
            content.text = "Brief debrief."
            response = MagicMock()
            response.content = [content]
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=response)
            mock_cls.return_value = mock_client

            with patch("backend.main._load_settings", return_value={"anthropic_api_key": "test-key"}):
                with patch("backend.main.get_db_session") as mock_db:
                    mock_session = AsyncMock()
                    mock_session.get = AsyncMock(return_value=MagicMock())
                    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                    mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

                    # Should not raise even without participants
                    await _generate_session_debrief(
                        "test-session", utterances, scores,
                    )

            # Verify it still called Opus
            assert mock_client.messages.create.called


# ---------------------------------------------------------------------------
# Debrief reinforcement — "What You Did Well"
# ---------------------------------------------------------------------------

class TestDebriefReinforcement:
    """Verify the debrief prompt asks for reinforcement of good practices."""

    @pytest.mark.asyncio
    async def test_debrief_prompt_asks_for_reinforcement(self):
        from backend.main import _generate_session_debrief

        utterances = [{"speaker": "user", "text": "Test"}]
        scores = {"persuasion_score": 70, "timing_score": 20, "ego_safety_score": 20, "convergence_score": 30}

        with patch("backend.main._anthropic.AsyncAnthropic") as mock_cls:
            content = MagicMock()
            content.text = "Good job."
            response = MagicMock()
            response.content = [content]
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=response)
            mock_cls.return_value = mock_client

            with patch("backend.main._load_settings", return_value={"anthropic_api_key": "k"}):
                with patch("backend.main.get_db_session") as mock_db:
                    mock_session = AsyncMock()
                    mock_session.get = AsyncMock(return_value=MagicMock())
                    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                    mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

                    await _generate_session_debrief("s1", utterances, scores)

            prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
            # Prompt should explicitly ask for reinforcement of good practices
            assert "WHAT YOU DID WELL" in prompt
            assert "Reinforce good practices" in prompt


# ---------------------------------------------------------------------------
# classify_from_scores: all 4 quadrants
# ---------------------------------------------------------------------------

class TestClassifyFromScores:
    """Direct unit tests for the extracted classify_from_scores() function."""

    def test_logic_advocacy_is_inquisitor(self):
        assert classify_from_scores(50.0, 30.0) == "Inquisitor"

    def test_narrative_advocacy_is_firestarter(self):
        assert classify_from_scores(-40.0, 20.0) == "Firestarter"

    def test_logic_analysis_is_architect(self):
        assert classify_from_scores(60.0, -25.0) == "Architect"

    def test_narrative_analysis_is_bridge_builder(self):
        assert classify_from_scores(-30.0, -50.0) == "Bridge Builder"

    def test_zero_focus_positive_stance(self):
        # focus=0 means NOT logic (logic = focus > 0), so Firestarter
        assert classify_from_scores(0.0, 10.0) == "Firestarter"

    def test_zero_stance_positive_focus(self):
        # stance=0 means NOT advocacy (advocacy = stance > 0), so Architect
        assert classify_from_scores(10.0, 0.0) == "Architect"


# ---------------------------------------------------------------------------
# CoachingEngine.user_archetype property
# ---------------------------------------------------------------------------

class TestUserArchetypeProperty:
    """Verify user_archetype getter/setter encapsulation."""

    def test_getter(self):
        engine = CoachingEngine.__new__(CoachingEngine)
        engine._user_archetype = "Architect"
        assert engine.user_archetype == "Architect"

    def test_setter(self):
        engine = CoachingEngine.__new__(CoachingEngine)
        engine._user_archetype = "Unknown"
        engine.user_archetype = "Firestarter"
        assert engine.user_archetype == "Firestarter"
        assert engine._user_archetype == "Firestarter"


# ---------------------------------------------------------------------------
# Debrief participant cap at 10
# ---------------------------------------------------------------------------

class TestDebriefParticipantCap:
    """Verify debrief prompt caps participants at 10, sorted by utterance_count."""

    @pytest.mark.asyncio
    async def test_session_debrief_caps_at_10_participants(self):
        from backend.main import _generate_session_debrief

        # Create 15 participants with varying utterance counts
        participants = [
            {
                "speaker_id": f"spk_{i}",
                "name": f"Person {i}",
                "archetype": "Architect",
                "confidence": 0.8,
                "focus_score": 50.0,
                "stance_score": -20.0,
                "utterance_count": i * 3,  # 0,3,6,...,42
                "key_evidence": [],
                "elm_episodes": [],
            }
            for i in range(15)
        ]

        utterances = [{"speaker": "user", "text": "Test"}]
        scores = {"persuasion_score": 70, "timing_score": 20, "ego_safety_score": 20, "convergence_score": 30}

        with patch("backend.main._anthropic.AsyncAnthropic") as mock_cls:
            content = MagicMock()
            content.text = "Debrief."
            response = MagicMock()
            response.content = [content]
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=response)
            mock_cls.return_value = mock_client

            with patch("backend.main._load_settings", return_value={"anthropic_api_key": "k"}):
                with patch("backend.main.get_db_session") as mock_db:
                    mock_session = AsyncMock()
                    mock_session.get = AsyncMock(return_value=MagicMock())
                    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                    mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

                    await _generate_session_debrief(
                        "s1", utterances, scores,
                        participants=participants, user_archetype="Architect",
                    )

            prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
            # Person 14 (42 utts) should be included (highest), Person 0 (0 utts) should not
            assert "Person 14" in prompt
            assert "Person 13" in prompt
            # Person 0 through Person 4 have the fewest utterances and should be dropped
            assert "Person 0" not in prompt

    @pytest.mark.asyncio
    async def test_debrief_pairing_section_present(self):
        """When user archetype is known and participants exist, pairing advice appears."""
        from backend.main import _generate_session_debrief

        participants = [
            {
                "speaker_id": "spk_0",
                "name": "Sarah Chen",
                "archetype": "Inquisitor",
                "confidence": 0.8,
                "focus_score": 50.0,
                "stance_score": 20.0,
                "utterance_count": 10,
                "key_evidence": [],
                "elm_episodes": [],
            },
        ]
        utterances = [{"speaker": "user", "text": "Test"}]
        scores = {"persuasion_score": 70, "timing_score": 20, "ego_safety_score": 20, "convergence_score": 30}

        with patch("backend.main._anthropic.AsyncAnthropic") as mock_cls:
            content = MagicMock()
            content.text = "Debrief."
            response = MagicMock()
            response.content = [content]
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=response)
            mock_cls.return_value = mock_client

            with patch("backend.main._load_settings", return_value={"anthropic_api_key": "k"}):
                with patch("backend.main.get_db_session") as mock_db:
                    mock_session = AsyncMock()
                    mock_session.get = AsyncMock(return_value=MagicMock())
                    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                    mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

                    await _generate_session_debrief(
                        "s1", utterances, scores,
                        participants=participants, user_archetype="Architect",
                    )

            prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
            assert "ARCHETYPE PAIRING DYNAMICS" in prompt
            assert "Sarah Chen" in prompt


# ---------------------------------------------------------------------------
# _update_retro_coaching_bullets
# ---------------------------------------------------------------------------

class TestRetroCoachingBullets:
    """Tests for _update_retro_coaching_bullets function."""

    @pytest.mark.asyncio
    async def test_calls_update_coaching_bullets(self):
        from backend.main import _update_retro_coaching_bullets

        utterances = [
            {"speaker_id": "user", "text": "I think we should proceed."},
            {"speaker_id": "counterpart_0", "text": "Agreed."},
            {"speaker_id": "user", "text": "Let me check."},
        ]
        scores = {
            "persuasion_score": 70,
            "timing_score": 20,
            "ego_safety_score": 20,
            "convergence_score": 30,
            "ego_threat_events": 1,
        }

        with patch("backend.main._load_settings", return_value={"anthropic_api_key": "test-key"}):
            with patch("backend.main.get_db_session") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

                with patch("backend.coaching_bullets.update_coaching_bullets", new_callable=AsyncMock) as mock_update:
                    await _update_retro_coaching_bullets("s1", "Architect", scores, utterances)

                    mock_update.assert_called_once()
                    call_kwargs = mock_update.call_args.kwargs
                    assert call_kwargs["user_archetype"] == "Architect"
                    assert call_kwargs["session_id"] == "s1"
                    summary = call_kwargs["session_summary"]
                    assert summary["context"] == "retro"
                    assert summary["persuasion_score"] == 70
                    # 2 user utterances out of 3 total
                    assert summary["talk_time_ratio"] == pytest.approx(0.67, abs=0.01)

    @pytest.mark.asyncio
    async def test_exception_is_swallowed(self):
        """Errors in retro bullet update should not propagate."""
        from backend.main import _update_retro_coaching_bullets

        with patch("backend.main._load_settings", return_value={"anthropic_api_key": "k"}):
            with patch("backend.main.get_db_session", side_effect=RuntimeError("db error")):
                # Should not raise
                await _update_retro_coaching_bullets("s1", "Unknown", {}, [])


# ---------------------------------------------------------------------------
# Echo filter (transcript-level dedup for ScreenCaptureKit echo)
# ---------------------------------------------------------------------------

class TestIsEcho:
    """Tests for backend.main.is_echo — prevents user voice on system audio."""

    def test_exact_duplicate_is_echo(self):
        from backend.main import is_echo
        mic = collections.deque(["the quick brown fox jumps"], maxlen=10)
        assert is_echo("the quick brown fox jumps", mic) is True

    def test_high_overlap_is_echo(self):
        from backend.main import is_echo
        mic = collections.deque(["the quick brown fox jumps over"], maxlen=10)
        # 4 of 5 words overlap = 80%
        assert is_echo("the quick brown fox leaps", mic) is True

    def test_low_overlap_is_not_echo(self):
        from backend.main import is_echo
        mic = collections.deque(["the quick brown fox"], maxlen=10)
        # Only 1 of 4 words overlap = 25%
        assert is_echo("something entirely different today", mic) is False

    def test_no_overlap_is_not_echo(self):
        from backend.main import is_echo
        mic = collections.deque(["hello world testing"], maxlen=10)
        assert is_echo("goodbye moon production", mic) is False

    def test_case_insensitive(self):
        from backend.main import is_echo
        mic = collections.deque(["The Quick Brown Fox"], maxlen=10)
        assert is_echo("the quick brown fox", mic) is True

    def test_short_utterance_not_filtered(self):
        """Utterances under 3 words are too short to match reliably."""
        from backend.main import is_echo
        mic = collections.deque(["hello world"], maxlen=10)
        assert is_echo("hello world", mic) is False

    def test_single_word_not_filtered(self):
        from backend.main import is_echo
        mic = collections.deque(["hello"], maxlen=10)
        assert is_echo("hello", mic) is False

    def test_empty_text_not_echo(self):
        from backend.main import is_echo
        mic = collections.deque(["hello world"], maxlen=10)
        assert is_echo("", mic) is False

    def test_whitespace_text_not_echo(self):
        from backend.main import is_echo
        mic = collections.deque(["hello world"], maxlen=10)
        assert is_echo("   \n  ", mic) is False

    def test_empty_deque_not_echo(self):
        from backend.main import is_echo
        mic: collections.deque[str] = collections.deque(maxlen=10)
        assert is_echo("hello world", mic) is False

    def test_matches_any_recent_mic(self):
        """Should match against any of the stored mic utterances."""
        from backend.main import is_echo
        mic = collections.deque([
            "completely different sentence here",
            "the quick brown fox jumps",
            "another unrelated phrase today",
        ], maxlen=10)
        assert is_echo("the quick brown fox jumps", mic) is True

    def test_custom_threshold(self):
        from backend.main import is_echo
        mic = collections.deque(["alpha beta gamma delta"], maxlen=10)
        # 2 of 4 = 50%, below default 0.6 but above 0.4
        assert is_echo("alpha beta other words", mic, threshold=0.4) is True
        assert is_echo("alpha beta other words", mic, threshold=0.6) is False

    def test_deque_maxlen_evicts_old(self):
        """Oldest entries should be evicted when deque is full."""
        from backend.main import is_echo
        mic: collections.deque[str] = collections.deque(maxlen=3)
        mic.append("alpha bravo charlie delta")
        mic.append("echo foxtrot golf hotel")
        mic.append("india juliet kilo lima")
        mic.append("mike november oscar papa")  # evicts "alpha bravo..."
        assert is_echo("alpha bravo charlie delta", mic) is False
        assert is_echo("mike november oscar papa", mic) is True


# ---------------------------------------------------------------------------
# Coaching prompt: plain English, no jargon
# ---------------------------------------------------------------------------

class TestCoachingPlainEnglish:
    """Verify coaching prompts use plain language, not academic jargon."""

    def test_system_prompt_bans_jargon(self):
        """System prompt explicitly forbids ELM terminology."""
        from backend.coaching_engine import _SYSTEM_PROMPT
        assert "ego safety" in _SYSTEM_PROMPT.lower() or "Never use terms like" in _SYSTEM_PROMPT
        assert "peripheral route" not in _SYSTEM_PROMPT.split("Never use terms like")[0]
        assert "central route" not in _SYSTEM_PROMPT.split("Never use terms like")[0]

    def test_system_prompt_requests_plain_english(self):
        from backend.coaching_engine import _SYSTEM_PROMPT
        assert "plain" in _SYSTEM_PROMPT.lower()
        assert "jargon" in _SYSTEM_PROMPT.lower()

    def test_elm_descriptions_no_jargon(self):
        from backend.coaching_engine import _ELM_STATE_DESCRIPTION
        for state, desc in _ELM_STATE_DESCRIPTION.items():
            assert "central route" not in desc.lower(), f"{state} uses 'central route'"
            assert "peripheral route" not in desc.lower(), f"{state} uses 'peripheral route'"
            assert "ego safety" not in desc.lower(), f"{state} uses 'ego safety'"

    def test_elm_goals_no_jargon(self):
        from backend.coaching_engine import _ELM_COACHING_GOAL
        for state, goal in _ELM_COACHING_GOAL.items():
            assert "psychological safety" not in goal.lower(), f"{state} goal uses jargon"
            assert "central route" not in goal.lower()

    def test_ego_threat_description_plain(self):
        from backend.coaching_engine import _ELM_STATE_DESCRIPTION
        desc = _ELM_STATE_DESCRIPTION["ego_threat"]
        assert "defensive" in desc.lower()

    def test_shortcut_description_plain(self):
        from backend.coaching_engine import _ELM_STATE_DESCRIPTION
        desc = _ELM_STATE_DESCRIPTION["shortcut"]
        assert "nodding" in desc.lower() or "not" in desc.lower()

    def test_consensus_protection_description_plain(self):
        from backend.coaching_engine import _ELM_STATE_DESCRIPTION
        desc = _ELM_STATE_DESCRIPTION["consensus_protection"]
        assert "disagreement" in desc.lower() or "shutting" in desc.lower()


# ── Initial Session Prompt ────────────────────────────────────────────


class TestInitialPrompt:
    """Verify the initial coaching prompt fires at session start."""

    def _make_engine(self, **kwargs):
        defaults = dict(
            user_speaker="user",
            anthropic_client=AsyncMock(),
            user_archetype="Architect",
            user_id="local-user",
        )
        defaults.update(kwargs)
        return CoachingEngine(**defaults)

    @pytest.mark.asyncio
    async def test_initial_prompt_returns_prompt(self):
        engine = self._make_engine()
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="Stay curious, ask questions early.")])
        )
        result = await engine.initial_prompt(user_display_name="Vish")
        assert result is not None
        assert result.text == "Stay curious, ask questions early."
        assert result.triggered_by == "session:start"
        assert result.layer == "self"

    @pytest.mark.asyncio
    async def test_initial_prompt_includes_user_name_in_query(self):
        engine = self._make_engine()
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="tip")])
        )
        await engine.initial_prompt(user_display_name="Vish")
        call_args = engine._client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "Vish" in user_msg

    @pytest.mark.asyncio
    async def test_initial_prompt_includes_meeting_title(self):
        engine = self._make_engine()
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="tip")])
        )
        await engine.initial_prompt(meeting_title="Q2 Board Review")
        call_args = engine._client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "Q2 Board Review" in user_msg

    @pytest.mark.asyncio
    async def test_initial_prompt_includes_participants(self):
        engine = self._make_engine(
            participants=[
                {"name": "Sarah", "archetype": "Inquisitor"},
                {"name": "Mike", "archetype": "Firestarter"},
            ]
        )
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="tip")])
        )
        await engine.initial_prompt()
        call_args = engine._client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "Sarah" in user_msg
        assert "Mike" in user_msg
        assert "Inquisitor" in user_msg

    @pytest.mark.asyncio
    async def test_initial_prompt_sets_last_prompt_time(self):
        engine = self._make_engine()
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="tip")])
        )
        assert engine._last_prompt_time == 0.0
        await engine.initial_prompt()
        assert engine._last_prompt_time > 0.0

    @pytest.mark.asyncio
    async def test_initial_prompt_no_participants_still_works(self):
        engine = self._make_engine(participants=[])
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="You got this.")])
        )
        result = await engine.initial_prompt(user_display_name="Vish")
        assert result is not None
        call_args = engine._client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        # New prompt rules say "give a readiness tip based on the user's archetype tendencies"
        assert "readiness tip" in user_msg.lower() or "archetype" in user_msg.lower()

    @pytest.mark.asyncio
    async def test_initial_prompt_fallback_on_api_error(self):
        engine = self._make_engine()
        engine._client.messages.create = AsyncMock(side_effect=Exception("API down"))
        result = await engine.initial_prompt()
        # No cache exists yet, so fallback returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_initial_prompt_welcome_when_no_name(self):
        engine = self._make_engine()
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="tip")])
        )
        await engine.initial_prompt(user_display_name="")
        call_args = engine._client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        # When no name given, the first-name extraction falls back to "there"
        assert "there" in user_msg.lower()

    @pytest.mark.asyncio
    async def test_initial_prompt_whitespace_only_name_safe(self):
        """Whitespace-only display name should not raise IndexError."""
        engine = self._make_engine()
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="tip")])
        )
        result = await engine.initial_prompt(user_display_name="   ")
        assert result is not None
        call_args = engine._client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "there" in user_msg.lower()

    @pytest.mark.asyncio
    async def test_initial_prompt_with_context_shifts(self):
        """When user_profile has context_shifts, prompt mentions the shift."""
        from backend.models import ProfileSnapshot

        profile = ProfileSnapshot(
            archetype="Firestarter",
            focus_score=0.7,
            stance_score=0.8,
            focus_variance=0.05,
            stance_variance=0.04,
            confidence=0.85,
            context="board",
            context_sessions=5,
            is_context_specific=True,
            core_archetype="Architect",
            core_sessions=10,
            context_shifts=True,
        )
        engine = self._make_engine()
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="tip")])
        )
        await engine.initial_prompt(user_profile=profile, user_display_name="Vish")
        call_args = engine._client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "Architect" in user_msg
        assert "board" in user_msg
        assert "shift" in user_msg.lower()

    @pytest.mark.asyncio
    async def test_initial_prompt_with_confidence_line(self):
        """When core_sessions >= 3, prompt includes session count."""
        from backend.models import ProfileSnapshot

        profile = ProfileSnapshot(
            archetype="Architect",
            focus_score=0.3,
            stance_score=0.2,
            focus_variance=0.03,
            stance_variance=0.02,
            confidence=0.9,
            context="general",
            context_sessions=4,
            is_context_specific=False,
            core_archetype="Architect",
            core_sessions=7,
            context_shifts=False,
        )
        engine = self._make_engine()
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="tip")])
        )
        await engine.initial_prompt(user_profile=profile, user_display_name="Vish")
        call_args = engine._client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "7 sessions" in user_msg

    @pytest.mark.asyncio
    async def test_initial_prompt_with_fingerprint_data(self):
        """Participant fingerprint patterns appear in the prompt."""
        engine = self._make_engine(
            participants=[
                {
                    "name": "Sarah",
                    "archetype": "Inquisitor",
                    "fingerprint": {
                        "sessions_observed": 4,
                        "patterns": ["Asks for data before committing"],
                    },
                },
            ]
        )
        engine._client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="tip")])
        )
        await engine.initial_prompt(user_display_name="Vish")
        call_args = engine._client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "4 prior sessions" in user_msg
        assert "Asks for data before committing" in user_msg
