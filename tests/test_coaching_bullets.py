"""
Tests for coaching_bullets.py — ACE-style structured coaching playbook.

Covers:
  - Dedup key computation
  - Relevance scoring
  - Curator merge (new, reinforce, contradict, cap enforcement)
  - Context selection
  - Feedback loop (helpful/harmful counter updates)
  - Reflector prompt formatting
  - Full pipeline integration (mocked Opus)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import select
from unittest.mock import AsyncMock, MagicMock, patch

from backend.coaching_bullets import (
    compute_dedup_key,
    curator_merge,
    get_coaching_context,
    relevance_score,
    update_bullet_feedback,
    _format_session_evidence,
    _read_legacy_playbook,
    _MAX_ACTIVE_BULLETS,
    _RETIRE_THRESHOLD_MARGIN,
    reflector_extract,
    update_coaching_bullets,
)
from backend.database import get_db_session, init_db, override_engine
from backend.models import CoachingBullet, Prompt, MeetingSession, User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

USER_ID = "test-user-cb"

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
            await s.commit()


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
        dedup_key=compute_dedup_key(content),
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Dedup key
# ---------------------------------------------------------------------------

class TestDedupKey:
    def test_basic_key(self):
        key = compute_dedup_key("Anchoring in numbers works well with Architects")
        assert "anchoring" in key
        assert "architects" in key
        assert "numbers" in key
        # Stop words removed
        assert "in" not in key.split()
        assert "with" not in key.split()

    def test_word_order_irrelevant(self):
        k1 = compute_dedup_key("Architects respond well to anchoring numbers")
        k2 = compute_dedup_key("anchoring numbers Architects respond well")
        # Same words, different order → same key
        assert k1 == k2

    def test_punctuation_stripped(self):
        k1 = compute_dedup_key("Lead with data, not vision.")
        k2 = compute_dedup_key("Lead with data not vision")
        assert k1 == k2

    def test_case_insensitive(self):
        k1 = compute_dedup_key("ARCHITECTS need DATA")
        k2 = compute_dedup_key("architects need data")
        assert k1 == k2

    def test_different_content_different_key(self):
        k1 = compute_dedup_key("Anchoring in numbers works with Architects")
        k2 = compute_dedup_key("Firestarters respond to emotional stories")
        assert k1 != k2

    def test_max_12_words(self):
        long = " ".join(f"word{i}" for i in range(20))
        key = compute_dedup_key(long)
        assert len(key.split()) <= 12

    def test_empty_content(self):
        key = compute_dedup_key("")
        assert key == ""

    def test_only_stop_words(self):
        key = compute_dedup_key("the a an is are in of to for with")
        assert key == ""


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

class TestRelevanceScore:
    def test_archetype_match_boosts_score(self):
        b = _make_bullet(counterpart_archetype="Architect")
        score_match = relevance_score(b, counterpart_archetype="Architect")
        score_mismatch = relevance_score(b, counterpart_archetype="Firestarter")
        assert score_match > score_mismatch

    def test_elm_match_boosts_score(self):
        b = _make_bullet(elm_state="ego_threat")
        score_match = relevance_score(b, elm_state="ego_threat")
        score_mismatch = relevance_score(b, elm_state="shortcut")
        assert score_match > score_mismatch

    def test_context_match_boosts_score(self):
        b = _make_bullet(context="board")
        score_match = relevance_score(b, context="board")
        score_mismatch = relevance_score(b, context="team")
        assert score_match > score_mismatch

    def test_helpful_count_boosts_score(self):
        b_helpful = _make_bullet(helpful=5, harmful=0)
        b_neutral = _make_bullet(helpful=0, harmful=0)
        assert relevance_score(b_helpful) > relevance_score(b_neutral)

    def test_harmful_count_reduces_score(self):
        b_harmful = _make_bullet(helpful=0, harmful=3)
        b_neutral = _make_bullet(helpful=0, harmful=0)
        assert relevance_score(b_harmful) < relevance_score(b_neutral)

    def test_recent_bullet_scores_higher(self):
        b_recent = _make_bullet(days_old=0)
        b_old = _make_bullet(days_old=60)
        assert relevance_score(b_recent) > relevance_score(b_old)

    def test_effective_category_bonus(self):
        b_effective = _make_bullet(category="effective")
        b_trend = _make_bullet(category="trend")
        assert relevance_score(b_effective) > relevance_score(b_trend)

    def test_evidence_breadth_bonus(self):
        b_multi = _make_bullet(evidence=5)
        b_single = _make_bullet(evidence=1)
        assert relevance_score(b_multi) > relevance_score(b_single)

    def test_general_bullet_no_penalty(self):
        """General bullets (no archetype) shouldn't be penalized for archetype mismatch."""
        b_general = _make_bullet(counterpart_archetype=None)
        b_specific_wrong = _make_bullet(counterpart_archetype="Firestarter")
        # General bullet scored against "Architect" shouldn't be penalized
        general_score = relevance_score(b_general, counterpart_archetype="Architect")
        wrong_score = relevance_score(b_specific_wrong, counterpart_archetype="Architect")
        assert general_score > wrong_score


# ---------------------------------------------------------------------------
# Curator merge
# ---------------------------------------------------------------------------

class TestCuratorMerge:
    @pytest.mark.asyncio
    async def test_new_creates_bullet(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            deltas = [{"action": "new", "content": "Anchor in numbers with Architects",
                        "category": "effective"}]
            affected = await curator_merge(s, USER_ID, "session-1", deltas)
            await s.commit()
            assert affected == 1

        async with get_db_session() as s:
            result = await s.execute(
                select(CoachingBullet).where(CoachingBullet.user_id == USER_ID)
            )
            bullets = list(result.scalars())
            assert len(bullets) == 1
            assert "Anchor in numbers" in bullets[0].content
            assert bullets[0].category == "effective"
            assert bullets[0].is_active is True

    @pytest.mark.asyncio
    async def test_new_dedup_reinforces_existing(self, db):
        await _ensure_user()
        # Create initial bullet
        async with get_db_session() as s:
            deltas = [{"action": "new", "content": "Anchor numbers Architects data",
                        "category": "effective"}]
            await curator_merge(s, USER_ID, "session-1", deltas)
            await s.commit()

        # Try to add duplicate (same distinctive words, different phrasing)
        async with get_db_session() as s:
            deltas = [{"action": "new", "content": "Architects data Anchor numbers",
                        "category": "effective"}]
            affected = await curator_merge(s, USER_ID, "session-2", deltas)
            await s.commit()
            assert affected == 1  # reinforced, not created

        async with get_db_session() as s:
            result = await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.is_active.is_(True),
                )
            )
            bullets = list(result.scalars())
            assert len(bullets) == 1
            assert bullets[0].helpful_count == 1  # incremented
            assert bullets[0].evidence_count == 2

    @pytest.mark.asyncio
    async def test_reinforce_increments_counters(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b = _make_bullet(content="Lead with data for Architects")
            s.add(b)
            await s.flush()
            bullet_id = b.id
            await s.commit()

        async with get_db_session() as s:
            deltas = [{"action": "reinforce", "bullet_id": bullet_id,
                        "content": "Lead with data for Architects"}]
            affected = await curator_merge(s, USER_ID, "session-2", deltas)
            await s.commit()
            assert affected == 1

        async with get_db_session() as s:
            b = await s.get(CoachingBullet, bullet_id)
            assert b.helpful_count == 1
            assert b.evidence_count == 2

    @pytest.mark.asyncio
    async def test_reinforce_missing_bullet_creates_new(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            deltas = [{"action": "reinforce", "bullet_id": "nonexistent-id",
                        "content": "Some new insight", "category": "tactic"}]
            affected = await curator_merge(s, USER_ID, "session-1", deltas)
            await s.commit()
            assert affected == 1

        async with get_db_session() as s:
            result = await s.execute(
                select(CoachingBullet).where(CoachingBullet.user_id == USER_ID)
            )
            bullets = list(result.scalars())
            assert len(bullets) == 1
            assert bullets[0].content == "Some new insight"

    @pytest.mark.asyncio
    async def test_contradict_increments_harmful(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b = _make_bullet(content="Lead with vision always")
            s.add(b)
            await s.flush()
            bullet_id = b.id
            await s.commit()

        async with get_db_session() as s:
            deltas = [{"action": "contradict", "bullet_id": bullet_id}]
            await curator_merge(s, USER_ID, "session-2", deltas)
            await s.commit()

        async with get_db_session() as s:
            b = await s.get(CoachingBullet, bullet_id)
            assert b.harmful_count == 1
            assert b.is_active is True  # Not yet retired (need margin of 2)

    @pytest.mark.asyncio
    async def test_contradict_retires_at_threshold(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b = _make_bullet(content="Bad advice", helpful=0, harmful=1)
            s.add(b)
            await s.flush()
            bullet_id = b.id
            await s.commit()

        # One more contradiction reaches threshold (harmful=2 >= helpful=0 + 2)
        async with get_db_session() as s:
            deltas = [{"action": "contradict", "bullet_id": bullet_id}]
            await curator_merge(s, USER_ID, "session-2", deltas)
            await s.commit()

        async with get_db_session() as s:
            b = await s.get(CoachingBullet, bullet_id)
            assert b.is_active is False
            assert b.retired_reason == "contradicted"

    @pytest.mark.asyncio
    async def test_contradict_missing_bullet_skipped(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            deltas = [{"action": "contradict", "bullet_id": "nonexistent"}]
            affected = await curator_merge(s, USER_ID, "session-1", deltas)
            await s.commit()
            assert affected == 0

    @pytest.mark.asyncio
    async def test_cap_enforcement(self, db):
        await _ensure_user()
        # Create bullets at the cap
        async with get_db_session() as s:
            for i in range(_MAX_ACTIVE_BULLETS + 5):
                b = _make_bullet(
                    content=f"Insight number {i} about communication",
                    helpful=i % 3,  # Varying helpfulness
                )
                s.add(b)
            await s.commit()

        # Merge one more — should trigger cap enforcement
        async with get_db_session() as s:
            deltas = [{"action": "new", "content": "Totally new insight xyz"}]
            await curator_merge(s, USER_ID, "session-x", deltas)
            await s.commit()

        async with get_db_session() as s:
            result = await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.is_active.is_(True),
                )
            )
            active = list(result.scalars())
            assert len(active) <= _MAX_ACTIVE_BULLETS

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            deltas = [{"action": "new", "content": "", "category": "effective"}]
            affected = await curator_merge(s, USER_ID, "session-1", deltas)
            await s.commit()
            assert affected == 0

    @pytest.mark.asyncio
    async def test_unknown_action_skipped(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            deltas = [{"action": "unknown", "content": "Something"}]
            affected = await curator_merge(s, USER_ID, "session-1", deltas)
            await s.commit()
            assert affected == 0


# ---------------------------------------------------------------------------
# Context selection
# ---------------------------------------------------------------------------

class TestContextSelection:
    @pytest.mark.asyncio
    async def test_returns_formatted_text_and_ids(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            s.add(_make_bullet(content="Anchor in numbers with Architects",
                               counterpart_archetype="Architect"))
            s.add(_make_bullet(content="Lead with empathy for Bridge Builders",
                               counterpart_archetype="Bridge Builder"))
            await s.commit()

        async with get_db_session() as s:
            text, ids = await get_coaching_context(
                s, USER_ID, counterpart_archetype="Architect"
            )
            assert "COACHING PLAYBOOK" in text
            assert len(ids) == 2

    @pytest.mark.asyncio
    async def test_relevant_bullets_ranked_first(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            s.add(_make_bullet(content="Architect-specific advice",
                               counterpart_archetype="Architect", helpful=3))
            s.add(_make_bullet(content="General advice", helpful=1))
            await s.commit()

        async with get_db_session() as s:
            text, ids = await get_coaching_context(
                s, USER_ID, counterpart_archetype="Architect"
            )
            # Architect-specific bullet should appear before general
            assert text.index("Architect-specific") < text.index("General advice")

    @pytest.mark.asyncio
    async def test_empty_store_returns_empty(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            text, ids = await get_coaching_context(s, USER_ID)
            assert text == ""
            assert ids == []

    @pytest.mark.asyncio
    async def test_word_budget_respected(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            for i in range(30):
                s.add(_make_bullet(
                    content=f"Long insight number {i} that provides detailed coaching advice about communication strategies and techniques",
                    helpful=i,
                ))
            await s.commit()

        async with get_db_session() as s:
            text, ids = await get_coaching_context(s, USER_ID, max_words=100)
            word_count = len(text.split())
            # Header adds some words, but total should be reasonable
            assert word_count < 150  # generous buffer for header

    @pytest.mark.asyncio
    async def test_inactive_bullets_excluded(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            s.add(_make_bullet(content="Active insight", is_active=True))
            s.add(_make_bullet(content="Retired insight", is_active=False))
            await s.commit()

        async with get_db_session() as s:
            text, ids = await get_coaching_context(s, USER_ID)
            assert "Active insight" in text
            assert "Retired insight" not in text
            assert len(ids) == 1


# ---------------------------------------------------------------------------
# Feedback loop
# ---------------------------------------------------------------------------

class TestFeedbackLoop:
    @pytest.mark.asyncio
    async def test_high_effectiveness_increments_helpful(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b = _make_bullet(content="Good advice")
            s.add(b)
            await s.flush()
            bullet_id = b.id
            await s.commit()

        async with get_db_session() as s:
            await update_bullet_feedback(s, bullet_id, 0.8)
            await s.commit()

        async with get_db_session() as s:
            b = await s.get(CoachingBullet, bullet_id)
            assert b.helpful_count == 1
            assert b.harmful_count == 0

    @pytest.mark.asyncio
    async def test_low_effectiveness_increments_harmful(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b = _make_bullet(content="Bad advice")
            s.add(b)
            await s.flush()
            bullet_id = b.id
            await s.commit()

        async with get_db_session() as s:
            await update_bullet_feedback(s, bullet_id, 0.1)
            await s.commit()

        async with get_db_session() as s:
            b = await s.get(CoachingBullet, bullet_id)
            assert b.helpful_count == 0
            assert b.harmful_count == 1

    @pytest.mark.asyncio
    async def test_mid_range_no_change(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b = _make_bullet(content="Meh advice")
            s.add(b)
            await s.flush()
            bullet_id = b.id
            await s.commit()

        async with get_db_session() as s:
            await update_bullet_feedback(s, bullet_id, 0.45)
            await s.commit()

        async with get_db_session() as s:
            b = await s.get(CoachingBullet, bullet_id)
            assert b.helpful_count == 0
            assert b.harmful_count == 0

    @pytest.mark.asyncio
    async def test_multiple_bullet_ids(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b1 = _make_bullet(content="First advice")
            b2 = _make_bullet(content="Second advice")
            s.add(b1)
            s.add(b2)
            await s.flush()
            ids_csv = f"{b1.id},{b2.id}"
            await s.commit()

        async with get_db_session() as s:
            await update_bullet_feedback(s, ids_csv, 0.8)
            await s.commit()

        async with get_db_session() as s:
            result = await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.is_active.is_(True),
                )
            )
            for b in result.scalars():
                assert b.helpful_count == 1

    @pytest.mark.asyncio
    async def test_none_effectiveness_no_op(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b = _make_bullet(content="Some advice")
            s.add(b)
            await s.flush()
            bullet_id = b.id
            await s.commit()

        async with get_db_session() as s:
            await update_bullet_feedback(s, bullet_id, None)
            await s.commit()

        async with get_db_session() as s:
            b = await s.get(CoachingBullet, bullet_id)
            assert b.helpful_count == 0
            assert b.harmful_count == 0

    @pytest.mark.asyncio
    async def test_feedback_triggers_retirement(self, db):
        await _ensure_user()
        async with get_db_session() as s:
            b = _make_bullet(content="Bad advice", harmful=1)
            s.add(b)
            await s.flush()
            bullet_id = b.id
            await s.commit()

        async with get_db_session() as s:
            await update_bullet_feedback(s, bullet_id, 0.1)
            await s.commit()

        async with get_db_session() as s:
            b = await s.get(CoachingBullet, bullet_id)
            assert b.is_active is False
            assert b.retired_reason == "contradicted"


# ---------------------------------------------------------------------------
# Session evidence formatting
# ---------------------------------------------------------------------------

class TestSessionEvidenceFormatting:
    def test_basic_formatting(self):
        summary = {
            "context": "board",
            "persuasion_score": 72,
            "timing_score": 0.8,
            "ego_safety_score": 0.7,
            "convergence_score": 0.75,
            "ego_threat_events": 1,
            "talk_time_ratio": 0.35,
            "total_utterances": 42,
            "prompt_results": [{
                "triggered_by": "elm:ego_threat",
                "counterpart_archetype": "Architect",
                "text": "She's defensive — lead with acknowledgment",
                "effectiveness_score": 0.7,
                "convergence_before": 0.3,
                "convergence_after": 0.6,
            }],
        }
        text = _format_session_evidence("Firestarter", summary)
        assert "Firestarter" in text
        assert "board" in text
        assert "72" in text
        assert "elm:ego_threat" in text
        assert "0.70" in text


# ---------------------------------------------------------------------------
# Integration: Full pipeline with mocked Opus
# ---------------------------------------------------------------------------

class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_reflector_curator_pipeline(self, db):
        """Simulate a full session: Reflector extracts deltas, Curator merges them."""
        await _ensure_user()

        # Pre-populate some bullets
        async with get_db_session() as s:
            b = _make_bullet(content="Lead with data for Architects",
                             counterpart_archetype="Architect", helpful=2)
            s.add(b)
            await s.flush()
            existing_id = b.id
            await s.commit()

        # Simulate Reflector output (what Opus would return)
        mock_deltas = [
            {
                "action": "reinforce",
                "bullet_id": existing_id,
                "content": "Lead with data for Architects — confirmed again",
                "category": "effective",
                "counterpart_archetype": "Architect",
            },
            {
                "action": "new",
                "content": "Firestarters respond well to storytelling openings",
                "category": "pairing",
                "counterpart_archetype": "Firestarter",
            },
            {
                "action": "new",
                "content": "Avoid leading with pure vision when ego-threat is present",
                "category": "ineffective",
                "elm_state": "ego_threat",
            },
        ]

        # Run curator merge
        async with get_db_session() as s:
            affected = await curator_merge(s, USER_ID, "session-99", mock_deltas)
            await s.commit()
            assert affected == 3

        # Verify state
        async with get_db_session() as s:
            result = await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.is_active.is_(True),
                )
            )
            bullets = list(result.scalars())
            assert len(bullets) == 3

            # Find the reinforced bullet
            reinforced = next(b for b in bullets if b.id == existing_id)
            assert reinforced.helpful_count == 3  # was 2, +1
            assert reinforced.evidence_count == 2

            # Verify context selection picks the right ones
            text, ids = await get_coaching_context(
                s, USER_ID, counterpart_archetype="Architect"
            )
            assert "Lead with data" in text
            assert existing_id in ids

    @pytest.mark.asyncio
    async def test_multi_session_evolution(self, db):
        """Bullets accumulate and evolve across multiple sessions."""
        await _ensure_user()

        # Session 1: New insights
        async with get_db_session() as s:
            deltas_1 = [
                {"action": "new", "content": "Patience works with Architects",
                 "category": "effective", "counterpart_archetype": "Architect"},
                {"action": "new", "content": "Ask questions to re-engage silent rooms",
                 "category": "tactic"},
            ]
            await curator_merge(s, USER_ID, "session-1", deltas_1)
            await s.commit()

        # Session 2: Reinforce + new + contradict
        async with get_db_session() as s:
            result = await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.content.contains("Patience"),
                )
            )
            patience_bullet = result.scalar_one()
            patience_id = patience_bullet.id

            deltas_2 = [
                {"action": "reinforce", "bullet_id": patience_id,
                 "content": "Patience works with Architects — they converge eventually"},
                {"action": "new", "content": "Bridge Builders need emotional validation",
                 "category": "pairing", "counterpart_archetype": "Bridge Builder"},
            ]
            await curator_merge(s, USER_ID, "session-2", deltas_2)
            await s.commit()

        # Session 3: More reinforcement
        async with get_db_session() as s:
            deltas_3 = [
                {"action": "reinforce", "bullet_id": patience_id,
                 "content": "Patience works with Architects — confirmed 3rd time"},
            ]
            await curator_merge(s, USER_ID, "session-3", deltas_3)
            await s.commit()

        # Verify evolution
        async with get_db_session() as s:
            b = await s.get(CoachingBullet, patience_id)
            assert b.helpful_count == 2  # reinforced twice
            assert b.evidence_count == 3  # original + 2 reinforcements
            assert "3rd time" in b.content  # content updated

            # Context selection should rank this highly
            text, ids = await get_coaching_context(
                s, USER_ID, counterpart_archetype="Architect"
            )
            assert patience_id == ids[0]  # Should be first (most relevant)
            assert "confirmed 3x" in text  # Evidence count shown

    @pytest.mark.asyncio
    async def test_bullet_retirement_through_feedback(self, db):
        """A bullet gets retired through negative feedback over multiple sessions."""
        await _ensure_user()

        async with get_db_session() as s:
            b = _make_bullet(content="Always lead with vision", category="tactic")
            s.add(b)
            await s.flush()
            bullet_id = b.id
            await s.commit()

        # Three rounds of negative feedback
        for _ in range(2):
            async with get_db_session() as s:
                await update_bullet_feedback(s, bullet_id, 0.1)
                await s.commit()

        async with get_db_session() as s:
            b = await s.get(CoachingBullet, bullet_id)
            assert b.is_active is False
            assert b.retired_reason == "contradicted"

            # Should not appear in context selection
            text, ids = await get_coaching_context(s, USER_ID)
            assert bullet_id not in ids
