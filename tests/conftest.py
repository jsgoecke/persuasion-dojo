"""
Shared test fixtures for the Persuasion Dojo backend test suite.

Fixtures defined here are available to every test file without import.
Individual test files can still define their own local fixtures — these
shared fixtures complement (not replace) existing inline helpers.

Migration note: existing test files are NOT required to switch to these
fixtures immediately. They exist as a stable foundation for new tests
and gradual migration of older files.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.database import init_db, override_engine
from backend.models import (
    Base,
    CoachingBullet,
    ContextProfile,
    User,
    SessionObservation,
    SELF_ASSESSMENT_PRIOR_CONFIDENCE,
)
from backend.coaching_bullets import compute_dedup_key


# ---------------------------------------------------------------------------
# Marker registration
# ---------------------------------------------------------------------------

def pytest_configure(config):
    """Register custom markers used throughout the test suite."""
    config.addinivalue_line("markers", "integration: requires live API keys (Deepgram, Anthropic)")
    config.addinivalue_line("markers", "slow: tests that take >5s")
    config.addinivalue_line("markers", "eval: LLM eval tests that cost money")


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_engine():
    """
    Provide an isolated in-memory SQLite engine for each test.

    Calls override_engine() so all database.py functions target this engine,
    then disposes it after the test.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    override_engine(engine)
    await init_db()
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Provide an async session bound to the test engine with tables created."""
    from sqlalchemy.ext.asyncio import AsyncSession
    async with AsyncSession(db_engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Fake Anthropic clients
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_anthropic_client():
    """
    MagicMock Anthropic client — messages.create returns a fixed text response.

    Usage in tests:
        engine = CoachingEngine(..., anthropic_client=fake_anthropic_client)
    """
    content = MagicMock()
    content.text = "She needs data — anchor your next point in a number."
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


@pytest.fixture
def fake_sync_anthropic_client():
    """
    MagicMock synchronous Anthropic client for pre_seeding.classify().

    Returns a JSON response matching the expected schema.
    """
    import json
    content = MagicMock()
    content.text = json.dumps({
        "type": "Architect",
        "confidence": 0.82,
        "state": "active",
        "reasoning": "Data-first language with systematic framing indicates Architect.",
    })
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create = MagicMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# Factory fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def make_utterance():
    """Factory for utterance dicts matching the WebSocket protocol."""
    def _factory(
        speaker: str = "speaker_0",
        text: str = "test utterance",
        is_final: bool = True,
        start: float = 0.0,
        end: float = 1.0,
    ) -> dict:
        return {
            "speaker_id": speaker,
            "text": text,
            "is_final": is_final,
            "start": start,
            "end": end,
        }
    return _factory


@pytest.fixture
def make_user():
    """Factory for User ORM objects with sensible defaults."""
    def _factory(**kwargs) -> User:
        defaults = dict(
            id="user-test",
            core_focus=0.0,
            core_stance=0.0,
            core_confidence=SELF_ASSESSMENT_PRIOR_CONFIDENCE,
            core_sessions=0,
            sa_completed_at=None,
        )
        defaults.update(kwargs)
        return User(**defaults)
    return _factory


@pytest.fixture
def make_context_profile():
    """Factory for ContextProfile objects."""
    def _factory(
        context: str = "team",
        sessions: int = 0,
        focus: float = 0.0,
        stance: float = 0.0,
    ) -> ContextProfile:
        return ContextProfile(
            id=f"ctx-{context}",
            user_id="user-test",
            context=context,
            focus_score=focus,
            stance_score=stance,
            sessions=sessions,
        )
    return _factory


@pytest.fixture
def make_observation():
    """Factory for SessionObservation dataclasses."""
    def _factory(
        context: str = "team",
        focus: float = 50.0,
        stance: float = 50.0,
        utterance_count: int = 20,
        obs_confidence: float = 1.0,
    ) -> SessionObservation:
        return SessionObservation(
            session_id="sess-test",
            context=context,
            focus_score=focus,
            stance_score=stance,
            utterance_count=utterance_count,
            obs_confidence=obs_confidence,
        )
    return _factory


@pytest.fixture
def make_bullet():
    """Factory for CoachingBullet ORM objects."""
    def _factory(
        user_id: str = "user-test",
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
    return _factory
