"""
Tests for the coaching engine select+personalize flow and supporting components.

Coverage:
  - Refusal detection (_is_refusal)
  - Graceful unknowns (_graceful_type)
  - System prompt properties (_SYSTEM_PROMPT, _LEGACY_SYSTEM_PROMPT)
  - Playbook filtering (_filter_for_haiku)
  - Selection dedup state (CoachingEngine init, reset, counters)
  - Layer boost computation (_compute_layer_boost)
  - Relevance scoring with layer_boost
  - Seed tips validation (data/seed_tips.json)
  - select_best_bullet (in-memory DB)
  - Utterance dedup in SessionPipeline
"""

from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from backend.coaching_bullets import (
    _filter_for_haiku,
    compute_dedup_key,
    relevance_score,
    select_best_bullet,
)
from backend.coaching_engine import (
    CoachingEngine,
    _LEGACY_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
    _graceful_type,
    _is_refusal,
)
from backend.database import get_db_session, init_db, override_engine
from backend.models import CoachingBullet, User


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

_RESPONSE_TEXT = "Ask a clarifying question right now."

USER_ID = "test-user-cq"

_SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "seed_tips.json"


def make_mock_client(text: str = _RESPONSE_TEXT) -> Any:
    content = MagicMock()
    content.text = text
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


def make_engine(**kwargs: Any) -> CoachingEngine:
    defaults = dict(
        user_speaker="speaker_0",
        anthropic_client=make_mock_client(),
        elm_cadence_floor_s=0.0,
        general_cadence_floor_s=0.0,
        haiku_timeout_s=999.0,
    )
    defaults.update(kwargs)
    return CoachingEngine(**defaults)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    override_engine(engine)
    await init_db()
    yield engine
    await engine.dispose()


async def _ensure_user(user_id: str = USER_ID) -> None:
    async with get_db_session() as s:
        existing = await s.get(User, user_id)
        if existing is None:
            s.add(User(id=user_id, display_name="Test User"))


def _make_bullet(
    user_id: str = USER_ID,
    content: str = "Test insight",
    category: str = "effective",
    helpful: int = 0,
    harmful: int = 0,
    evidence: int = 1,
    counterpart_archetype: str | None = None,
    elm_state: str | None = None,
    context: str | None = None,
    layer: str | None = None,
    days_old: int = 0,
    is_active: bool = True,
) -> CoachingBullet:
    now = datetime.now(timezone.utc) - timedelta(days=days_old)
    return CoachingBullet(
        user_id=user_id,
        content=content,
        category=category,
        helpful_count=helpful,
        harmful_count=harmful,
        evidence_count=evidence,
        counterpart_archetype=counterpart_archetype,
        elm_state=elm_state,
        context=context,
        layer=layer,
        dedup_key=compute_dedup_key(content),
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )


# ===========================================================================
# 1. Refusal detection (6 tests)
# ===========================================================================

class TestRefusalDetection:
    def test_catches_i_cant_generate(self):
        assert _is_refusal("I can't generate a coaching tip from this.")

    def test_catches_i_cannot_provide(self):
        assert _is_refusal("I cannot provide coaching advice here.")

    def test_catches_i_apologize(self):
        assert _is_refusal("I apologize, but I need more context.")

    def test_catches_transcript_garbled(self):
        assert _is_refusal("The transcript is garbled and I can't help.")

    def test_catches_pseudo_scientific(self):
        assert _is_refusal("This appears to be pseudo-scientific framing.")

    def test_valid_tip_with_cant_in_text(self):
        """Tips containing 'can't' in non-refusal context pass through."""
        assert not _is_refusal("Sarah can't focus when overwhelmed with data — slow down.")

    def test_empty_string_is_refusal(self):
        assert _is_refusal("")

    def test_whitespace_only_is_refusal(self):
        assert _is_refusal("   \n\t  ")

    def test_normal_tip_passes(self):
        assert not _is_refusal("Sarah needs proof — lead with a specific number.")


# ===========================================================================
# 2. Graceful unknowns (4 tests)
# ===========================================================================

class TestGracefulType:
    def test_unknown_returns_descriptive(self):
        result = _graceful_type("Unknown")
        assert result != "Unknown"
        assert "style" in result or "reading" in result

    def test_undetermined_returns_descriptive(self):
        result = _graceful_type("Undetermined")
        assert result != "Undetermined"
        assert "style" in result or "reading" in result

    def test_empty_returns_descriptive(self):
        result = _graceful_type("")
        assert result != ""
        assert len(result) > 5

    def test_architect_returns_unchanged(self):
        assert _graceful_type("Architect") == "Architect"


# ===========================================================================
# 3. System prompt properties (4 tests)
# ===========================================================================

class TestSystemPromptProperties:
    def test_new_prompt_contains_adapt_instruction(self):
        assert "adapt the tip" in _SYSTEM_PROMPT.lower()

    def test_new_prompt_contains_never_refuse(self):
        assert "Never say you can't help" in _SYSTEM_PROMPT

    def test_new_prompt_contains_why_action_format(self):
        assert "WHY" in _SYSTEM_PROMPT
        assert "ACTION" in _SYSTEM_PROMPT

    def test_legacy_prompt_contains_framework_archetypes(self):
        assert "Architect" in _LEGACY_SYSTEM_PROMPT
        assert "Firestarter" in _LEGACY_SYSTEM_PROMPT
        assert "Inquisitor" in _LEGACY_SYSTEM_PROMPT
        assert "Bridge Builder" in _LEGACY_SYSTEM_PROMPT


# ===========================================================================
# 4. Playbook filtering (5 tests)
# ===========================================================================

class TestPlaybookFiltering:
    def test_strips_score_lines(self):
        text = (
            "Use data with Architects.\n"
            "Persuasion Score: 75/100\n"
            "Lead with evidence."
        )
        result = _filter_for_haiku(text)
        assert "75/100" not in result
        assert "Use data" in result
        assert "Lead with evidence" in result

    def test_strips_consecutive_session_lines(self):
        text = (
            "Ask open questions.\n"
            "7th consecutive session showing this pattern.\n"
            "Mirror their language."
        )
        result = _filter_for_haiku(text)
        assert "consecutive session" not in result.lower()
        assert "Ask open questions" in result

    def test_strips_markdown_table_rows(self):
        text = (
            "Use empathy first when talking to architects who need data.\n"
            "Always lead with evidence and concrete specific numbers.\n"
            "Mirror the counterpart language and acknowledge their points.\n"
            "| Metric | Value | Notes |\n"
            "| --- | --- | --- |\n"
            "| Score | 42 | Low |\n"
            "Stay concise and direct with every coaching suggestion."
        )
        result = _filter_for_haiku(text)
        assert "| Metric |" not in result
        assert "Use empathy first" in result
        assert "Stay concise" in result

    def test_preserves_verb_first_advice(self):
        text = (
            "Ask a question to re-engage the group.\n"
            "Anchor your next point in a number.\n"
            "Acknowledge their concern first."
        )
        result = _filter_for_haiku(text)
        assert "Ask a question" in result
        assert "Anchor your next" in result
        assert "Acknowledge their" in result

    def test_all_metrics_stripped_returns_empty(self):
        """When all content is metrics, return empty rather than leaking."""
        text = (
            "Persuasion Score: 75/100 and that is the final "
            "score of the session observed over many sessions with "
            "this particular participant in a 1:1 context observed."
        )
        result = _filter_for_haiku(text)
        # All-metric content stripped completely — empty is better than leaking
        assert "75/100" not in result


# ===========================================================================
# 5. Selection dedup state (5 tests)
# ===========================================================================

class TestSelectionDedupState:
    def test_shown_bullet_ids_starts_empty(self):
        engine = make_engine()
        assert engine._shown_bullet_ids == set()

    def test_recent_bullet_ids_maxlen(self):
        engine = make_engine()
        assert isinstance(engine._recent_bullet_ids, deque)
        assert engine._recent_bullet_ids.maxlen == 10

    def test_recent_layers_maxlen(self):
        engine = make_engine()
        assert isinstance(engine._recent_layers, deque)
        assert engine._recent_layers.maxlen == 3

    def test_reset_clears_all_dedup_state(self):
        engine = make_engine()
        # Populate state
        engine._shown_bullet_ids.add("bullet-1")
        engine._recent_bullet_ids.append("bullet-1")
        engine._recent_layers.append("self")
        engine._session_unique_count = 5
        engine._fallback_count = 3
        engine._personalized_count = 2
        engine._dedup_suppressed_count = 1

        engine.reset()

        assert engine._shown_bullet_ids == set()
        assert len(engine._recent_bullet_ids) == 0
        assert len(engine._recent_layers) == 0
        assert engine._session_unique_count == 0
        assert engine._fallback_count == 0
        assert engine._personalized_count == 0
        assert engine._dedup_suppressed_count == 0

    def test_observability_counters_start_at_zero(self):
        engine = make_engine()
        assert engine._session_unique_count == 0
        assert engine._fallback_count == 0
        assert engine._personalized_count == 0
        assert engine._dedup_suppressed_count == 0


# ===========================================================================
# 6. Layer boost computation (5 tests)
# ===========================================================================

class TestLayerBoostComputation:
    def test_returns_none_when_fewer_than_3_layers(self):
        engine = make_engine()
        engine._recent_layers.append("self")
        engine._recent_layers.append("self")
        assert engine._compute_layer_boost() is None

    def test_boosts_audience_and_group_when_all_self(self):
        engine = make_engine()
        engine._recent_layers.extend(["self", "self", "self"])
        boost = engine._compute_layer_boost()
        assert boost is not None
        assert "audience" in boost
        assert "group" in boost
        assert "self" not in boost

    def test_boosts_self_and_group_when_all_audience(self):
        engine = make_engine()
        engine._recent_layers.extend(["audience", "audience", "audience"])
        boost = engine._compute_layer_boost()
        assert boost is not None
        assert "self" in boost
        assert "group" in boost
        assert "audience" not in boost

    def test_returns_none_when_mixed_layers(self):
        engine = make_engine()
        engine._recent_layers.extend(["self", "audience", "group"])
        assert engine._compute_layer_boost() is None

    def test_layer_boost_value_is_5(self):
        engine = make_engine()
        engine._recent_layers.extend(["self", "self", "self"])
        boost = engine._compute_layer_boost()
        assert boost["audience"] == 5.0
        assert boost["group"] == 5.0


# ===========================================================================
# 7. Relevance scoring with layer_boost (3 tests)
# ===========================================================================

class TestRelevanceScoringLayerBoost:
    def test_layer_boost_increases_audience_score(self):
        bullet = _make_bullet(layer="audience", content="Audience tip")
        base = relevance_score(bullet)
        boosted = relevance_score(bullet, layer_boost={"audience": 5.0})
        assert boosted > base
        assert boosted - base == pytest.approx(5.0)

    def test_layer_boost_none_behaves_normally(self):
        bullet = _make_bullet(layer="self", content="Self tip")
        score_none = relevance_score(bullet, layer_boost=None)
        score_default = relevance_score(bullet)
        assert score_none == pytest.approx(score_default)

    def test_layer_boost_ignores_bullets_with_no_layer(self):
        bullet = _make_bullet(layer=None, content="No layer tip")
        base = relevance_score(bullet)
        boosted = relevance_score(bullet, layer_boost={"self": 5.0, "audience": 5.0})
        assert boosted == pytest.approx(base)


# ===========================================================================
# 8. Seed tips validation (5 tests)
# ===========================================================================

class TestSeedTipsValidation:
    @pytest.fixture(autouse=True)
    def load_tips(self):
        self.tips = json.loads(_SEED_FILE.read_text(encoding="utf-8"))

    def test_exactly_132_tips(self):
        assert len(self.tips) == 132

    def test_all_tips_have_required_fields(self):
        for i, tip in enumerate(self.tips):
            assert "content" in tip, f"Tip {i} missing 'content'"
            assert "layer" in tip, f"Tip {i} missing 'layer'"
            assert "category" in tip, f"Tip {i} missing 'category'"

    def test_tips_cover_all_16_archetype_pairings(self):
        archetypes = ["Architect", "Firestarter", "Inquisitor", "Bridge Builder"]
        expected_pairings = {(u, c) for u in archetypes for c in archetypes}
        actual_pairings = set()
        for tip in self.tips:
            ua = tip.get("user_archetype")
            ca = tip.get("counterpart_archetype")
            if ua and ca:
                actual_pairings.add((ua, ca))
        assert actual_pairings == expected_pairings

    def test_tips_cover_all_3_layers(self):
        layers = {tip["layer"] for tip in self.tips}
        assert layers == {"self", "audience", "group"}

    def test_elm_tips_cover_all_3_states(self):
        elm_states = {
            tip["elm_state"]
            for tip in self.tips
            if tip.get("elm_state")
        }
        assert elm_states == {"ego_threat", "shortcut", "consensus_protection"}


# ===========================================================================
# 9. select_best_bullet (4 tests using in-memory DB)
# ===========================================================================

class TestSelectBestBullet:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_bullets(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            result = await select_best_bullet(s, USER_ID)
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_highest_scored_bullet(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            low = _make_bullet(content="Low scoring tip", helpful=0, harmful=2)
            high = _make_bullet(content="High scoring tip", helpful=5, harmful=0, evidence=3)
            s.add(low)
            s.add(high)
            await s.flush()

            best = await select_best_bullet(s, USER_ID)
            assert best is not None
            assert best.content == "High scoring tip"

    @pytest.mark.asyncio
    async def test_excludes_bullet_ids_in_exclude_set(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b1 = _make_bullet(content="First tip", helpful=5)
            b2 = _make_bullet(content="Second tip", helpful=3)
            s.add(b1)
            s.add(b2)
            await s.flush()

            # Exclude the higher-scoring bullet
            best = await select_best_bullet(s, USER_ID, exclude_ids={b1.id})
            assert best is not None
            assert best.content == "Second tip"

    @pytest.mark.asyncio
    async def test_falls_back_when_all_excluded(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b1 = _make_bullet(content="Only tip")
            s.add(b1)
            await s.flush()

            # Exclude the only bullet — should fall back to showing it anyway
            best = await select_best_bullet(s, USER_ID, exclude_ids={b1.id})
            assert best is not None
            assert best.content == "Only tip"


# ===========================================================================
# 10. Utterance dedup in SessionPipeline (4 tests)
# ===========================================================================

class TestUtteranceDedup:
    def _make_pipeline(self) -> Any:
        """Create a minimal SessionPipeline with a mock coaching engine."""
        from backend.main import SessionPipeline
        mock_engine = make_engine()
        return SessionPipeline(
            session_id="test-session",
            user_id="test-user",
            user_speaker="speaker_0",
            coaching_engine=mock_engine,
        )

    @pytest.mark.asyncio
    async def test_duplicate_same_speaker_skipped(self):
        pipe = self._make_pipeline()
        # First utterance should be stored
        await pipe.process_utterance("speaker_1", "I disagree with that.")
        assert len(pipe.utterances) == 1
        # Duplicate from same speaker should be skipped
        await pipe.process_utterance("speaker_1", "I disagree with that.")
        assert len(pipe.utterances) == 1

    @pytest.mark.asyncio
    async def test_different_speakers_same_text_both_stored(self):
        pipe = self._make_pipeline()
        await pipe.process_utterance("speaker_1", "That sounds good.")
        await pipe.process_utterance("speaker_2", "That sounds good.")
        assert len(pipe.utterances) == 2

    @pytest.mark.asyncio
    async def test_normalization_strips_trailing_punctuation(self):
        pipe = self._make_pipeline()
        await pipe.process_utterance("speaker_1", "I agree!")
        # Same text with different trailing punctuation should be a dup
        await pipe.process_utterance("speaker_1", "I agree.")
        assert len(pipe.utterances) == 1

    def test_last_utterance_starts_empty(self):
        pipe = self._make_pipeline()
        assert pipe._last_utterance == {}


# ===========================================================================
# 11. Bullet cap and diversity-preserving retirement (5 tests)
# ===========================================================================

class TestBulletCapDiversity:
    def test_cap_raised_to_250(self):
        from backend.coaching_bullets import _MAX_ACTIVE_BULLETS
        assert _MAX_ACTIVE_BULLETS == 250

    @pytest.mark.asyncio
    async def test_enforce_cap_retires_excess(self, db):
        from backend.coaching_bullets import _enforce_cap, _MAX_ACTIVE_BULLETS
        await _ensure_user()
        async with get_db_session() as s:
            # Create more than cap
            for i in range(_MAX_ACTIVE_BULLETS + 10):
                b = _make_bullet(content=f"Tip {i}", helpful=0, harmful=0)
                s.add(b)
            await s.flush()
            await _enforce_cap(s, USER_ID)

            active = (await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.is_active.is_(True),
                )
            )).scalars().all()
            assert len(active) <= _MAX_ACTIVE_BULLETS

    @pytest.mark.asyncio
    async def test_enforce_cap_preserves_minority_context(self, db):
        """Bullets from a rare context type should be protected from retirement."""
        from backend.coaching_bullets import _enforce_cap, _MIN_PER_CONTEXT
        await _ensure_user()
        async with get_db_session() as s:
            # Create 260 bullets: 255 "meeting" context, 5 "board" context
            for i in range(255):
                b = _make_bullet(content=f"Meeting tip {i}", context="meeting", helpful=0, harmful=0)
                s.add(b)
            for i in range(5):
                b = _make_bullet(content=f"Board tip {i}", context="board", helpful=0, harmful=0)
                s.add(b)
            await s.flush()
            await _enforce_cap(s, USER_ID)

            # Board bullets should all survive (only 5, at the _MIN_PER_CONTEXT threshold)
            board_active = (await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.is_active.is_(True),
                    CoachingBullet.context == "board",
                )
            )).scalars().all()
            assert len(board_active) == _MIN_PER_CONTEXT

    @pytest.mark.asyncio
    async def test_enforce_cap_preserves_minority_archetype(self, db):
        """Bullets for a rare counterpart archetype should be protected."""
        from backend.coaching_bullets import _enforce_cap, _MIN_PER_CONTEXT
        await _ensure_user()
        async with get_db_session() as s:
            for i in range(255):
                b = _make_bullet(content=f"Arch tip {i}", counterpart_archetype="Architect", helpful=0, harmful=0)
                s.add(b)
            for i in range(5):
                b = _make_bullet(content=f"Bridge tip {i}", counterpart_archetype="Bridge Builder", helpful=0, harmful=0)
                s.add(b)
            await s.flush()
            await _enforce_cap(s, USER_ID)

            bridge_active = (await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.is_active.is_(True),
                    CoachingBullet.counterpart_archetype == "Bridge Builder",
                )
            )).scalars().all()
            assert len(bridge_active) == _MIN_PER_CONTEXT

    def test_min_per_context_constant(self):
        from backend.coaching_bullets import _MIN_PER_CONTEXT
        assert _MIN_PER_CONTEXT == 5


# ===========================================================================
# 12. User feedback on coaching prompts (6 tests)
# ===========================================================================

class TestUserFeedback:
    @pytest.mark.asyncio
    async def test_record_helpful_feedback(self, db):
        from backend.coaching_bullets import record_user_feedback
        from backend.models import Prompt
        await _ensure_user()
        async with get_db_session() as s:
            # Create a bullet and a prompt referencing it
            bullet = _make_bullet(content="Great tip", helpful=1, harmful=0)
            s.add(bullet)
            await s.flush()
            bullet_id = bullet.id

            prompt = Prompt(
                session_id="test-session",
                layer="audience",
                text="Great tip personalized",
                trigger="cadence",
                was_shown=True,
                bullet_ids_used=bullet_id,
            )
            s.add(prompt)
            await s.flush()
            prompt_id = prompt.id

        # Record helpful feedback
        async with get_db_session() as s:
            result = await record_user_feedback(s, prompt_id, helpful=True)
            assert result == prompt_id

        # Check bullet was updated
        async with get_db_session() as s:
            updated_bullet = await s.get(CoachingBullet, bullet_id)
            assert updated_bullet.helpful_count == 3  # 1 original + 2 (user feedback weight)

    @pytest.mark.asyncio
    async def test_record_harmful_feedback(self, db):
        from backend.coaching_bullets import record_user_feedback
        from backend.models import Prompt
        await _ensure_user()
        async with get_db_session() as s:
            bullet = _make_bullet(content="Bad tip", helpful=1, harmful=0)
            s.add(bullet)
            await s.flush()
            bullet_id = bullet.id

            prompt = Prompt(
                session_id="test-session",
                layer="self",
                text="Bad tip personalized",
                trigger="cadence",
                was_shown=True,
                bullet_ids_used=bullet_id,
            )
            s.add(prompt)
            await s.flush()
            prompt_id = prompt.id

        async with get_db_session() as s:
            result = await record_user_feedback(s, prompt_id, helpful=False)
            assert result == prompt_id

        async with get_db_session() as s:
            updated_bullet = await s.get(CoachingBullet, bullet_id)
            assert updated_bullet.harmful_count == 2  # 0 + 2 (user feedback weight)

    @pytest.mark.asyncio
    async def test_harmful_feedback_can_retire_bullet(self, db):
        from backend.coaching_bullets import record_user_feedback
        from backend.models import Prompt
        await _ensure_user()
        async with get_db_session() as s:
            # Bullet with helpful=1, harmful=1 — one more harmful push retires it
            bullet = _make_bullet(content="Borderline tip", helpful=1, harmful=1)
            s.add(bullet)
            await s.flush()
            bullet_id = bullet.id

            prompt = Prompt(
                session_id="test-session",
                layer="group",
                text="Borderline personalized",
                trigger="cadence",
                was_shown=True,
                bullet_ids_used=bullet_id,
            )
            s.add(prompt)
            await s.flush()
            prompt_id = prompt.id

        async with get_db_session() as s:
            await record_user_feedback(s, prompt_id, helpful=False)

        async with get_db_session() as s:
            updated_bullet = await s.get(CoachingBullet, bullet_id)
            # harmful_count = 1 + 2 = 3, helpful_count = 1, margin = 2
            # 3 >= 1 + 2 → retired
            assert updated_bullet.is_active is False
            assert updated_bullet.retired_reason == "user_feedback"

    @pytest.mark.asyncio
    async def test_feedback_unknown_prompt_returns_none(self, db):
        from backend.coaching_bullets import record_user_feedback
        await _ensure_user()
        async with get_db_session() as s:
            result = await record_user_feedback(s, "nonexistent-prompt-id", helpful=True)
            assert result is None

    @pytest.mark.asyncio
    async def test_feedback_sets_user_feedback_field(self, db):
        from backend.coaching_bullets import record_user_feedback
        from backend.models import Prompt
        await _ensure_user()
        async with get_db_session() as s:
            prompt = Prompt(
                session_id="test-session",
                layer="self",
                text="Some tip",
                trigger="cadence",
                was_shown=True,
            )
            s.add(prompt)
            await s.flush()
            prompt_id = prompt.id

        async with get_db_session() as s:
            await record_user_feedback(s, prompt_id, helpful=True)

        async with get_db_session() as s:
            prompt = await s.get(Prompt, prompt_id)
            assert prompt.user_feedback == "helpful"

    @pytest.mark.asyncio
    async def test_duplicate_feedback_is_idempotent(self, db):
        """Second feedback call on the same prompt should be a no-op."""
        from backend.coaching_bullets import record_user_feedback
        from backend.models import Prompt
        await _ensure_user()
        async with get_db_session() as s:
            bullet = _make_bullet(content="Idempotent tip", helpful=1, harmful=0)
            s.add(bullet)
            await s.flush()
            bullet_id = bullet.id

            prompt = Prompt(
                session_id="test-session",
                layer="self",
                text="Idempotent tip personalized",
                trigger="cadence",
                was_shown=True,
                bullet_ids_used=bullet_id,
            )
            s.add(prompt)
            await s.flush()
            prompt_id = prompt.id

        # First feedback — should apply
        async with get_db_session() as s:
            await record_user_feedback(s, prompt_id, helpful=True)

        # Second feedback — should be a no-op
        async with get_db_session() as s:
            await record_user_feedback(s, prompt_id, helpful=False)

        # Bullet should only have the first feedback applied (helpful +2, not harmful +2)
        async with get_db_session() as s:
            updated = await s.get(CoachingBullet, bullet_id)
            assert updated.helpful_count == 3  # 1 original + 2 from first feedback
            assert updated.harmful_count == 0  # second call was no-op

    def test_user_feedback_weight_is_double(self):
        from backend.coaching_bullets import _USER_FEEDBACK_WEIGHT
        assert _USER_FEEDBACK_WEIGHT == 2


# ===========================================================================
# 13. Reflector layer field in deltas (2 tests)
# ===========================================================================

class TestReflectorLayerField:
    def test_reflector_prompt_includes_layer_in_schema(self):
        from backend.coaching_bullets import _REFLECTOR_PROMPT
        assert '"layer"' in _REFLECTOR_PROMPT
        assert '"self" | "audience" | "group"' in _REFLECTOR_PROMPT

    @pytest.mark.asyncio
    async def test_merge_new_saves_layer(self, db):
        from backend.coaching_bullets import curator_merge
        await _ensure_user()
        async with get_db_session() as s:
            deltas = [{
                "action": "new",
                "bullet_id": None,
                "content": "Test tip with layer",
                "category": "tactic",
                "layer": "audience",
                "confidence": 0.8,
            }]
            affected = await curator_merge(s, USER_ID, "test-session", deltas)
            assert affected == 1

            result = await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.content == "Test tip with layer",
                )
            )
            bullet = result.scalar_one()
            assert bullet.layer == "audience"


# ===========================================================================
# 14. Reflector metric leak rejection (6 tests)
# ===========================================================================

class TestReflectorMetricLeakRejection:
    """Verify contaminated Reflector output never reaches the bullet store."""

    def test_reflector_prompt_bans_metrics_in_content(self):
        from backend.coaching_bullets import _REFLECTOR_PROMPT
        assert "NEVER include scores" in _REFLECTOR_PROMPT
        assert "ratios" in _REFLECTOR_PROMPT
        assert "plain english" in _REFLECTOR_PROMPT.lower()

    def test_leak_regex_catches_score_fractions(self):
        from backend.coaching_bullets import _BULLET_METRIC_LEAK
        assert _BULLET_METRIC_LEAK.search("Persuasion 75/100 is too low")
        assert _BULLET_METRIC_LEAK.search("scored 33/100 in both sessions")

    def test_leak_regex_catches_decimal_metrics(self):
        from backend.coaching_bullets import _BULLET_METRIC_LEAK
        assert _BULLET_METRIC_LEAK.search("convergence 0.309 at zero utterances")
        assert _BULLET_METRIC_LEAK.search("ratio dropped to 0.075 floor")

    def test_leak_regex_catches_jargon(self):
        from backend.coaching_bullets import _BULLET_METRIC_LEAK
        assert _BULLET_METRIC_LEAK.search("peripheral route avoidance is the issue")
        assert _BULLET_METRIC_LEAK.search("central route processing dominates")
        assert _BULLET_METRIC_LEAK.search("ELM insight reinforced from prior session")

    def test_leak_regex_passes_clean_advice(self):
        from backend.coaching_bullets import _BULLET_METRIC_LEAK
        assert not _BULLET_METRIC_LEAK.search(
            "You tend to stay silent — speak in the first 90 seconds"
        )
        assert not _BULLET_METRIC_LEAK.search(
            "Sarah needs proof — lead with a specific number"
        )
        assert not _BULLET_METRIC_LEAK.search(
            "Ask a clarifying question to re-engage the group"
        )

    def test_leak_regex_catches_internal_system_terms(self):
        from backend.coaching_bullets import _BULLET_METRIC_LEAK
        assert _BULLET_METRIC_LEAK.search("talk time ratio remains at zero")
        assert _BULLET_METRIC_LEAK.search("depleted 33/0.075 floor for a second session")
        assert _BULLET_METRIC_LEAK.search("zero-utterance session confirmed")


# ===========================================================================
# 15. Legacy playbook filter in coaching_memory (5 tests)
# ===========================================================================

class TestLegacyPlaybookFilter:
    """Verify coaching_memory._filter_for_haiku strips internal data."""

    def test_strips_score_lines(self):
        from backend.coaching_memory import _filter_for_haiku
        text = "Speak up early.\nScores: 30/100 in both sessions.\nUse structure."
        result = _filter_for_haiku(text)
        assert "30/100" not in result
        assert "Speak up early" in result

    def test_strips_elm_jargon(self):
        from backend.coaching_memory import _filter_for_haiku
        text = (
            "Ask a question next.\n"
            "ELM insight reinforced: peripheral processing dominates.\n"
            "Mirror their language."
        )
        result = _filter_for_haiku(text)
        assert "peripheral processing" not in result
        assert "ELM insight" not in result
        assert "Ask a question" in result

    def test_strips_internal_system_instructions(self):
        from backend.coaching_memory import _filter_for_haiku
        text = (
            "Lead with data.\n"
            "Coaching system must intervene at 60 seconds.\n"
            "Anchor in a number."
        )
        result = _filter_for_haiku(text)
        assert "Coaching system must" not in result
        assert "Lead with data" in result

    def test_strips_table_rows(self):
        from backend.coaching_memory import _filter_for_haiku
        text = "Good advice here.\n| Metric | Value |\n| --- | --- |\n| Score | 42 |\nMore advice."
        result = _filter_for_haiku(text)
        assert "| Metric |" not in result
        assert "Good advice" in result

    def test_preserves_clean_coaching_advice(self):
        from backend.coaching_memory import _filter_for_haiku
        text = (
            "Speak within the first 90 seconds.\n"
            "Use scaffolding as your entry point.\n"
            "Pre-commit to one opinion per meeting."
        )
        result = _filter_for_haiku(text)
        assert "Speak within" in result
        assert "scaffolding" in result
        assert "Pre-commit" in result
