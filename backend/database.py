"""
Async SQLite engine, session factory, and table initialisation.

Usage (FastAPI lifespan):
    from backend.database import init_db, get_db_session

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db()
        yield

    # In a route / dependency:
    async with get_db_session() as session:
        user = await session.get(User, user_id)
        ...

Design decisions
────────────────
- WAL mode: enabled at engine connect time for better concurrent read performance
  and reduced write contention with the real-time coaching pipeline.
- Single file, single engine: SQLite is embedded — there is no connection pool to
  worry about. AsyncEngine is a module-level singleton; tests override it via
  override_engine().
- echo=False in production: set DATABASE_ECHO=1 env var to enable SQL logging.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.models import Base

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = "persuasion_dojo.db"
_DB_URL = os.environ.get("DATABASE_URL", f"sqlite+aiosqlite:///{_DEFAULT_DB_PATH}")
_ECHO = os.environ.get("DATABASE_ECHO", "").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Engine (module-level singleton — override in tests via override_engine())
# ---------------------------------------------------------------------------

_engine: AsyncEngine = create_async_engine(
    _DB_URL,
    echo=_ECHO,
    connect_args={"check_same_thread": False},
)


def override_engine(engine: AsyncEngine) -> None:
    """
    Replace the module-level engine. Call in tests before init_db().

    Example:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        override_engine(engine)
        await init_db()
    """
    global _engine, _session_factory
    _engine = engine
    _session_factory = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )


# ---------------------------------------------------------------------------
# WAL mode pragma
# ---------------------------------------------------------------------------

def _set_wal_mode(dbapi_conn, _connection_record) -> None:  # type: ignore[type-arg]
    """Enable WAL journal mode on each new DBAPI connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")   # safe with WAL
    cursor.close()


# Register the event on the sync driver (aiosqlite wraps it)
event.listen(_engine.sync_engine, "connect", _set_wal_mode)


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    _engine, class_=AsyncSession, expire_on_commit=False
)


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession with automatic commit / rollback.

    Usage:
        async with get_db_session() as session:
            result = await session.execute(select(User))

    On exception, the session is rolled back and the exception re-raised.
    """
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Table initialisation
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """
    Create all tables if they don't exist, and add any missing columns.

    Safe to call on every app start — SQLAlchemy uses CREATE TABLE IF NOT EXISTS.
    The column migration loop handles the case where new columns are added to ORM
    models after the table was originally created (SQLite has no IF NOT EXISTS for
    ALTER TABLE ADD COLUMN, so we catch the "duplicate column" error).
    """
    async with _engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.run_sync(Base.metadata.create_all)

        # Auto-migrate: add any columns that exist in models but not yet in SQLite.
        for table in Base.metadata.sorted_tables:
            for col in table.columns:
                col_type = col.type.compile(dialect=conn.dialect)
                stmt = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}"
                try:
                    await conn.execute(text(stmt))
                except Exception:
                    # Column already exists — expected for most columns.
                    pass


async def drop_all_tables() -> None:
    """
    Drop all tables. For use in tests only — never call in production.
    """
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
