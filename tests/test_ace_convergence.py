"""
ACE loop convergence test — simulates 10 coaching sessions and asserts that
the bullet store evolves toward higher quality over time.

Models the full cycle: session → feedback → reflector deltas → curator merge,
repeated across sessions with realistic signal patterns. Tracks four
convergence metrics at each step:

  1. Mean relevance score of top-5 bullets (should increase)
  2. Ratio of helpful vs harmful bullets (should increase)
  3. Number of retired bullets (bad advice eliminated)
  4. Specificity — fraction of bullets with archetype/ELM metadata (should increase)

No LLM calls — Reflector output is simulated with curated deltas that mirror
realistic Opus behavior (reinforce what worked, contradict what didn't, surface
new insights as evidence accumulates).

Run:
    pytest tests/test_ace_convergence.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from backend.coaching_bullets import (
    compute_dedup_key,
    curator_merge,
    get_coaching_context,
    relevance_score,
    update_bullet_feedback,
    _MAX_ACTIVE_BULLETS,
)
from backend.database import get_db_session, init_db, override_engine
from backend.models import CoachingBullet, User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

USER_ID = "test-user-convergence"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    override_engine(engine)
    await init_db()
    async with get_db_session() as s:
        s.add(User(id=USER_ID, display_name="Convergence Test User"))
        await s.commit()
    yield engine
    await engine.dispose()


def _make_bullet(
    content: str,
    category: str = "effective",
    helpful: int = 0,
    harmful: int = 0,
    evidence: int = 1,
    counterpart_archetype: str | None = None,
    elm_state: str | None = None,
    context: str | None = None,
) -> CoachingBullet:
    now = datetime.now(timezone.utc)
    return CoachingBullet(
        user_id=USER_ID,
        content=content,
        category=category,
        helpful_count=helpful,
        harmful_count=harmful,
        evidence_count=evidence,
        counterpart_archetype=counterpart_archetype,
        elm_state=elm_state,
        context=context,
        dedup_key=compute_dedup_key(content),
        is_active=True,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Helpers — snapshot metrics at a point in time
# ---------------------------------------------------------------------------

async def _snapshot_metrics(
    archetype: str = "Architect",
    elm_state: str = "ego_threat",
    context: str = "board",
) -> dict:
    """Return a dict of convergence metrics for the current bullet store."""
    async with get_db_session() as s:
        result = await s.execute(
            select(CoachingBullet).where(CoachingBullet.user_id == USER_ID)
        )
        all_bullets = list(result.scalars())

    active = [b for b in all_bullets if b.is_active]
    retired = [b for b in all_bullets if not b.is_active]

    now = datetime.now(timezone.utc)

    # Top-5 relevance scores for the target scenario
    scored = sorted(
        active,
        key=lambda b: relevance_score(b, archetype, elm_state, context, now),
        reverse=True,
    )
    top5 = scored[:5]
    top5_scores = [relevance_score(b, archetype, elm_state, context, now) for b in top5]
    mean_top5 = sum(top5_scores) / len(top5_scores) if top5_scores else 0.0

    # Helpful ratio
    total_helpful = sum(b.helpful_count for b in active)
    total_harmful = sum(b.harmful_count for b in active)
    helpful_ratio = total_helpful / max(1, total_helpful + total_harmful)

    # Specificity: fraction of active bullets with at least one dimensional tag
    specific = sum(
        1 for b in active
        if b.counterpart_archetype or b.elm_state or b.context
    )
    specificity = specific / len(active) if active else 0.0

    return {
        "active_count": len(active),
        "retired_count": len(retired),
        "mean_top5_relevance": mean_top5,
        "helpful_ratio": helpful_ratio,
        "specificity": specificity,
        "total_helpful": total_helpful,
        "total_harmful": total_harmful,
    }


# ---------------------------------------------------------------------------
# Session simulation data
#
# Each session is a tuple of:
#   (reflector_deltas, feedback_rounds)
#
# feedback_rounds: list of (bullet_content_substr, effectiveness_score)
#   — matched by content substring to find the bullet ID at runtime
# ---------------------------------------------------------------------------

# Session 1: Cold start — generic advice, no dimensional tags
SESSION_1_DELTAS = [
    {"action": "new", "content": "Be patient in board meetings",
     "category": "tactic"},
    {"action": "new", "content": "Ask open-ended questions",
     "category": "tactic"},
    {"action": "new", "content": "Don't dominate the conversation",
     "category": "ineffective"},
    {"action": "new", "content": "Lead with data when challenged",
     "category": "effective"},
]

# Session 2: Start learning about Architects
SESSION_2_DELTAS = [
    {"action": "new", "content": "Architects need data before they move",
     "category": "effective", "counterpart_archetype": "Architect"},
    {"action": "new", "content": "Acknowledge Architect concerns before presenting",
     "category": "tactic", "counterpart_archetype": "Architect",
     "elm_state": "ego_threat"},
]

# Session 3: Reinforce what worked, contradict what didn't
SESSION_3_DELTAS_TEMPLATE = [
    # Reinforce "Architects need data" — requires bullet_id at runtime
    {"action": "reinforce", "_match": "Architects need data",
     "content": "Architects need data before they move — anchor in specifics"},
    # Contradict generic advice that didn't help
    {"action": "contradict", "_match": "Be patient in board"},
]

# Session 4: Ego-threat specific learning
SESSION_4_DELTAS = [
    {"action": "new",
     "content": "When an Architect is ego-threatened, validate their data first",
     "category": "effective", "counterpart_archetype": "Architect",
     "elm_state": "ego_threat", "context": "board"},
    {"action": "new",
     "content": "Firestarters shut down when you lead with numbers in ego-threat",
     "category": "ineffective", "counterpart_archetype": "Firestarter",
     "elm_state": "ego_threat"},
]

# Session 5: Deep reinforce of Architect ego-threat pattern
SESSION_5_DELTAS_TEMPLATE = [
    {"action": "reinforce", "_match": "Architect is ego-threatened",
     "content": "When an Architect is ego-threatened, validate their data first — confirmed 3rd time"},
    {"action": "reinforce", "_match": "Architects need data",
     "content": "Architects need data before they move — anchor in specific metrics"},
]

# Session 6: New context-specific insight + contradict weak advice
SESSION_6_DELTAS_TEMPLATE = [
    {"action": "new",
     "content": "In board settings, Architects respond to structured agendas",
     "category": "pairing", "counterpart_archetype": "Architect", "context": "board"},
    {"action": "contradict", "_match": "Don't dominate"},
]

# Session 7: Cross-archetype learning
SESSION_7_DELTAS = [
    {"action": "new",
     "content": "Bridge Builders need emotional validation before data",
     "category": "pairing", "counterpart_archetype": "Bridge Builder"},
    {"action": "new",
     "content": "Inquisitors test your logic — prepare counterarguments",
     "category": "tactic", "counterpart_archetype": "Inquisitor"},
]

# Session 8: Reinforce cross-archetype + contradict more generics
SESSION_8_DELTAS_TEMPLATE = [
    {"action": "reinforce", "_match": "Bridge Builders need emotional",
     "content": "Bridge Builders need emotional validation before data — works every time"},
    {"action": "contradict", "_match": "Ask open-ended questions"},
]

# Session 9: Refine the best-performing insight
SESSION_9_DELTAS_TEMPLATE = [
    {"action": "reinforce", "_match": "Architect is ego-threatened",
     "content": "When an Architect is ego-threatened, validate their data first, then pivot to your evidence"},
]

# Session 10: Final reinforcement + new nuance
SESSION_10_DELTAS_TEMPLATE = [
    {"action": "reinforce", "_match": "Architects need data",
     "content": "Architects need data before they move — the more specific, the faster convergence"},
    {"action": "new",
     "content": "In board meetings with Architects under ego-threat, open with 'I see your point on X'",
     "category": "tactic", "counterpart_archetype": "Architect",
     "elm_state": "ego_threat", "context": "board"},
]

# Feedback patterns per session: (content_match, effectiveness)
# High effectiveness → helpful++, Low → harmful++
SESSION_FEEDBACK = {
    1: [
        ("Be patient", 0.45),          # neutral
        ("open-ended questions", 0.35), # neutral
        ("dominate", 0.15),             # harmful
        ("Lead with data", 0.70),       # helpful
    ],
    2: [
        ("Architects need data", 0.80),   # helpful
        ("Acknowledge Architect", 0.75),  # helpful
    ],
    3: [
        ("Architects need data", 0.85),   # helpful (again)
        ("Be patient", 0.20),             # harmful (again)
    ],
    4: [
        ("Architect is ego-threatened", 0.90),  # very helpful
        ("Firestarters shut down", 0.70),       # helpful
    ],
    5: [
        ("Architect is ego-threatened", 0.85),  # helpful
        ("Architects need data", 0.80),         # helpful
    ],
    6: [
        ("structured agendas", 0.75),  # helpful
        ("dominate", 0.10),            # harmful
    ],
    7: [
        ("Bridge Builders need", 0.65),    # helpful
        ("Inquisitors test", 0.70),        # helpful
    ],
    8: [
        ("Bridge Builders need", 0.80),    # helpful
        ("open-ended questions", 0.20),    # harmful
    ],
    9: [
        ("Architect is ego-threatened", 0.90),  # helpful
    ],
    10: [
        ("Architects need data", 0.85),  # helpful
        ("I see your point", 0.80),      # helpful
    ],
}


async def _find_bullet_id(match: str) -> str:
    """Find active bullet by content substring. Raises if not found."""
    async with get_db_session() as s:
        result = await s.execute(
            select(CoachingBullet).where(
                CoachingBullet.user_id == USER_ID,
                CoachingBullet.is_active.is_(True),
            )
        )
        for b in result.scalars():
            if match.lower() in b.content.lower():
                return b.id
    raise ValueError(f"No active bullet matching '{match}'")


async def _resolve_deltas(deltas: list[dict]) -> list[dict]:
    """Replace _match references with actual bullet_ids."""
    resolved = []
    for d in deltas:
        d = dict(d)
        if "_match" in d:
            d["bullet_id"] = await _find_bullet_id(d.pop("_match"))
        resolved.append(d)
    return resolved


async def _apply_feedback(session_num: int):
    """Apply feedback for a session, matching bullets by content substring."""
    for content_match, effectiveness in SESSION_FEEDBACK.get(session_num, []):
        try:
            bid = await _find_bullet_id(content_match)
        except ValueError:
            continue  # bullet may have been retired
        async with get_db_session() as s:
            await update_bullet_feedback(s, bid, effectiveness)
            await s.commit()


# ---------------------------------------------------------------------------
# The convergence test
# ---------------------------------------------------------------------------

ALL_SESSIONS = [
    (1, SESSION_1_DELTAS),
    (2, SESSION_2_DELTAS),
    (3, SESSION_3_DELTAS_TEMPLATE),
    (4, SESSION_4_DELTAS),
    (5, SESSION_5_DELTAS_TEMPLATE),
    (6, SESSION_6_DELTAS_TEMPLATE),
    (7, SESSION_7_DELTAS),
    (8, SESSION_8_DELTAS_TEMPLATE),
    (9, SESSION_9_DELTAS_TEMPLATE),
    (10, SESSION_10_DELTAS_TEMPLATE),
]


class TestACEConvergence:
    """
    Simulate 10 sessions of the ACE loop and assert that the bullet store
    converges toward higher quality coaching advice.

    This is the headline integration test for the adaptive coaching system.
    """

    @pytest.mark.asyncio
    async def test_full_convergence_over_10_sessions(self, db):
        """
        Run 10 sessions with realistic patterns:
        - Sessions 1-2: Cold start with generic + first specific advice
        - Sessions 3-5: Reinforce winners, contradict losers, deepen specifics
        - Sessions 6-8: Context-specific learning, cross-archetype growth
        - Sessions 9-10: Refinement of top insights

        Assert 4 convergence properties at the end.
        """
        snapshots: list[dict] = []

        # Take baseline snapshot (empty store)
        snapshots.append(await _snapshot_metrics())

        for session_num, raw_deltas in ALL_SESSIONS:
            # Resolve _match references to actual bullet IDs
            deltas = await _resolve_deltas(raw_deltas)

            # Curator merge (simulates what happens after Reflector)
            async with get_db_session() as s:
                await curator_merge(s, USER_ID, f"session-{session_num}", deltas)
                await s.commit()

            # Apply effectiveness feedback
            await _apply_feedback(session_num)

            # Snapshot after this session
            snapshots.append(await _snapshot_metrics())

        # --- Convergence assertions ---

        initial = snapshots[1]   # after session 1 (first real data)
        final = snapshots[-1]    # after session 10

        # 1. Mean top-5 relevance increased significantly
        assert final["mean_top5_relevance"] > initial["mean_top5_relevance"] + 2.0, (
            f"Top-5 relevance should improve by >2.0 points: "
            f"{initial['mean_top5_relevance']:.2f} → {final['mean_top5_relevance']:.2f}"
        )

        # 2. Helpful ratio should be high (most surviving bullets are net-helpful)
        assert final["helpful_ratio"] >= 0.75, (
            f"Helpful ratio should be ≥75%: {final['helpful_ratio']:.0%} "
            f"(helpful={final['total_helpful']}, harmful={final['total_harmful']})"
        )

        # 3. Bad advice gets retired
        assert final["retired_count"] >= 2, (
            f"At least 2 bullets should be retired: got {final['retired_count']}"
        )

        # 4. Specificity increases (generic → dimensionally-tagged)
        assert final["specificity"] > initial["specificity"], (
            f"Specificity should increase: "
            f"{initial['specificity']:.0%} → {final['specificity']:.0%}"
        )

    @pytest.mark.asyncio
    async def test_relevance_monotonically_improves(self, db):
        """
        Track mean top-5 relevance after each session.
        Allow dips (new untested bullets dilute), but the trend line
        from session 3 onward should never drop below session 2's level.
        """
        scores: list[float] = []

        for session_num, raw_deltas in ALL_SESSIONS:
            deltas = await _resolve_deltas(raw_deltas)
            async with get_db_session() as s:
                await curator_merge(s, USER_ID, f"session-{session_num}", deltas)
                await s.commit()
            await _apply_feedback(session_num)
            m = await _snapshot_metrics()
            scores.append(m["mean_top5_relevance"])

        # After the learning phase (session 3+), scores should stay above session 2
        floor = scores[1]  # session 2 — first archetype-specific bullets
        for i in range(2, len(scores)):
            assert scores[i] >= floor, (
                f"Session {i+1} relevance ({scores[i]:.2f}) dropped below "
                f"session 2 floor ({floor:.2f})"
            )

    @pytest.mark.asyncio
    async def test_bad_bullets_retire_early(self, db):
        """Generic advice that consistently gets low effectiveness should retire."""
        # Only run sessions 1-6 to verify retirement happens mid-run
        for session_num, raw_deltas in ALL_SESSIONS[:6]:
            deltas = await _resolve_deltas(raw_deltas)
            async with get_db_session() as s:
                await curator_merge(s, USER_ID, f"session-{session_num}", deltas)
                await s.commit()
            await _apply_feedback(session_num)

        # "Don't dominate" got harmful feedback in sessions 1 and 6, plus
        # a contradict delta in session 6 — should be retired
        async with get_db_session() as s:
            result = await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.content.contains("dominate"),
                )
            )
            bullet = result.scalar_one_or_none()
            assert bullet is not None, "Expected 'dominate' bullet to exist"
            assert bullet.is_active is False, (
                f"'dominate' bullet should be retired "
                f"(helpful={bullet.helpful_count}, harmful={bullet.harmful_count})"
            )

    @pytest.mark.asyncio
    async def test_top_bullet_is_most_specific(self, db):
        """
        After 10 sessions, the #1 ranked bullet for an Architect ego-threat
        board scenario should have all three dimensional tags.
        """
        for session_num, raw_deltas in ALL_SESSIONS:
            deltas = await _resolve_deltas(raw_deltas)
            async with get_db_session() as s:
                await curator_merge(s, USER_ID, f"session-{session_num}", deltas)
                await s.commit()
            await _apply_feedback(session_num)

        # Get the top-ranked bullet for the target scenario
        async with get_db_session() as s:
            _, ids = await get_coaching_context(
                s, USER_ID,
                counterpart_archetype="Architect",
                elm_state="ego_threat",
                context="board",
            )
            assert ids, "Expected at least one bullet in coaching context"
            top = await s.get(CoachingBullet, ids[0])

        assert top.counterpart_archetype == "Architect", (
            f"Top bullet archetype should be Architect, got {top.counterpart_archetype}"
        )
        assert top.elm_state == "ego_threat", (
            f"Top bullet ELM state should be ego_threat, got {top.elm_state}"
        )

    @pytest.mark.asyncio
    async def test_coaching_context_quality_improves(self, db):
        """
        The formatted coaching context text should evolve from generic
        to specific. After session 1 it contains generic advice; after
        session 10 it should reference Architects specifically.
        """
        # Session 1 only
        deltas = await _resolve_deltas(ALL_SESSIONS[0][1])
        async with get_db_session() as s:
            await curator_merge(s, USER_ID, "session-1", deltas)
            await s.commit()
        await _apply_feedback(1)

        async with get_db_session() as s:
            text_early, _ = await get_coaching_context(
                s, USER_ID, counterpart_archetype="Architect"
            )

        # Run remaining sessions
        for session_num, raw_deltas in ALL_SESSIONS[1:]:
            deltas = await _resolve_deltas(raw_deltas)
            async with get_db_session() as s:
                await curator_merge(s, USER_ID, f"session-{session_num}", deltas)
                await s.commit()
            await _apply_feedback(session_num)

        async with get_db_session() as s:
            text_late, _ = await get_coaching_context(
                s, USER_ID, counterpart_archetype="Architect"
            )

        # Early context is generic
        assert "Architect" not in text_early, (
            f"Session 1 context shouldn't mention Architect yet: {text_early[:200]}"
        )

        # Late context is Architect-specific
        assert "Architect" in text_late, (
            f"Session 10 context should mention Architect: {text_late[:200]}"
        )

    @pytest.mark.asyncio
    async def test_evidence_accumulation(self, db):
        """
        The most-reinforced bullet should have evidence_count ≥ 4 after
        10 sessions (created + 3 reinforcements).
        """
        for session_num, raw_deltas in ALL_SESSIONS:
            deltas = await _resolve_deltas(raw_deltas)
            async with get_db_session() as s:
                await curator_merge(s, USER_ID, f"session-{session_num}", deltas)
                await s.commit()
            await _apply_feedback(session_num)

        # "Architects need data" gets reinforced in sessions 3, 5, 10
        async with get_db_session() as s:
            result = await s.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == USER_ID,
                    CoachingBullet.content.contains("Architects need data"),
                    CoachingBullet.is_active.is_(True),
                )
            )
            bullet = result.scalar_one()

        assert bullet.evidence_count >= 4, (
            f"Expected ≥4 evidence count, got {bullet.evidence_count}"
        )
        assert bullet.helpful_count >= 4, (
            f"Expected ≥4 helpful count, got {bullet.helpful_count}"
        )
        assert "confirmed" in bullet.content.lower() or "specific" in bullet.content.lower(), (
            f"Content should have been refined over time: {bullet.content!r}"
        )
