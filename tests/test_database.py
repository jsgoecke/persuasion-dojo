"""
Tests for backend/database.py.

Covers:
  - init_db() creates all ORM tables
  - get_db_session() auto-commits on clean exit
  - get_db_session() rolls back on exception, re-raises
  - drop_all_tables() removes all tables
  - WAL journal mode is active after init_db()
  - override_engine() is picked up by get_db_session() and init_db()
"""

from __future__ import annotations

import asyncio
import tempfile
import os

import pytest
import pytest_asyncio
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.database import (
    drop_all_tables,
    get_db_session,
    init_db,
    override_engine,
)
from backend.models import Base, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_engine():
    """Return a brand-new in-memory async engine (no shared state)."""
    return create_async_engine("sqlite+aiosqlite:///:memory:")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_engine():
    """
    Provide an isolated in-memory engine for each test.

    Calls override_engine() so all database.py functions target this engine,
    then disposes it after the test.
    """
    engine = _fresh_engine()
    override_engine(engine)
    yield engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    @pytest.mark.asyncio
    async def test_creates_tables(self, db_engine):
        """init_db() creates all ORM-defined tables."""
        await init_db()

        async with db_engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )

        expected = {t.name for t in Base.metadata.sorted_tables}
        assert expected.issubset(set(table_names)), (
            f"Missing tables: {expected - set(table_names)}"
        )

    @pytest.mark.asyncio
    async def test_idempotent(self, db_engine):
        """Calling init_db() twice does not raise and leaves tables intact."""
        await init_db()
        await init_db()  # second call — must not raise

        async with db_engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )

        expected = {t.name for t in Base.metadata.sorted_tables}
        assert expected.issubset(set(table_names))

    @pytest.mark.asyncio
    async def test_sets_wal_mode(self):
        """init_db() enables WAL journal mode on a file-based database.

        In-memory SQLite always reports 'memory' regardless of the PRAGMA,
        so this test uses a temporary on-disk file.
        """
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        file_engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        override_engine(file_engine)

        try:
            await init_db()

            async with file_engine.connect() as conn:
                result = await conn.execute(text("PRAGMA journal_mode"))
                mode = result.scalar()

            assert mode == "wal", f"Expected WAL mode, got: {mode!r}"
        finally:
            await file_engine.dispose()
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# get_db_session — commit path
# ---------------------------------------------------------------------------

class TestGetDbSessionCommit:
    @pytest.mark.asyncio
    async def test_row_visible_after_clean_exit(self, db_engine):
        """get_db_session() commits the session when no exception is raised."""
        await init_db()

        async with get_db_session() as session:
            session.add(User(id="user-commit", display_name="Alice"))

        # Verify row is persisted in a separate session
        async with get_db_session() as session:
            user = await session.get(User, "user-commit")

        assert user is not None
        assert user.display_name == "Alice"

    @pytest.mark.asyncio
    async def test_multiple_rows_committed_atomically(self, db_engine):
        """Multiple rows added in one session are all committed."""
        await init_db()

        async with get_db_session() as session:
            for i in range(3):
                session.add(User(id=f"bulk-{i}", display_name=f"User {i}"))

        async with get_db_session() as session:
            for i in range(3):
                user = await session.get(User, f"bulk-{i}")
                assert user is not None, f"Row bulk-{i} not committed"


# ---------------------------------------------------------------------------
# get_db_session — rollback path
# ---------------------------------------------------------------------------

class TestGetDbSessionRollback:
    @pytest.mark.asyncio
    async def test_rollback_on_exception(self, db_engine):
        """get_db_session() rolls back if an exception is raised inside the block."""
        await init_db()

        with pytest.raises(ValueError, match="intentional"):
            async with get_db_session() as session:
                session.add(User(id="rollback-user", display_name="Bob"))
                raise ValueError("intentional error")

        # Row must NOT be in the database
        async with get_db_session() as session:
            user = await session.get(User, "rollback-user")

        assert user is None

    @pytest.mark.asyncio
    async def test_exception_is_reraised(self, db_engine):
        """The original exception propagates out of get_db_session()."""
        await init_db()

        class CustomError(Exception):
            pass

        with pytest.raises(CustomError):
            async with get_db_session() as session:
                session.add(User(id="reraise-user", display_name="Eve"))
                raise CustomError("should propagate")

    @pytest.mark.asyncio
    async def test_session_usable_after_failed_transaction(self, db_engine):
        """A fresh get_db_session() works normally after a rolled-back one."""
        await init_db()

        # First session — rolls back
        with pytest.raises(RuntimeError):
            async with get_db_session() as session:
                session.add(User(id="fail-then-ok", display_name="Fail"))
                raise RuntimeError("oops")

        # Second session — commits cleanly
        async with get_db_session() as session:
            session.add(User(id="clean-user", display_name="Clean"))

        async with get_db_session() as session:
            clean = await session.get(User, "clean-user")
            fail = await session.get(User, "fail-then-ok")

        assert clean is not None
        assert fail is None


# ---------------------------------------------------------------------------
# drop_all_tables
# ---------------------------------------------------------------------------

class TestDropAllTables:
    @pytest.mark.asyncio
    async def test_removes_all_tables(self, db_engine):
        """drop_all_tables() removes every ORM-managed table."""
        await init_db()
        await drop_all_tables()

        async with db_engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )

        orm_tables = {t.name for t in Base.metadata.sorted_tables}
        remaining = orm_tables & set(table_names)
        assert not remaining, f"Tables not dropped: {remaining}"

    @pytest.mark.asyncio
    async def test_reinit_after_drop(self, db_engine):
        """Tables can be recreated after drop_all_tables()."""
        await init_db()
        await drop_all_tables()
        await init_db()  # should succeed without errors

        async with get_db_session() as session:
            session.add(User(id="after-drop", display_name="Reborn"))

        async with get_db_session() as session:
            user = await session.get(User, "after-drop")

        assert user is not None


# ---------------------------------------------------------------------------
# override_engine
# ---------------------------------------------------------------------------

class TestOverrideEngine:
    @pytest.mark.asyncio
    async def test_get_db_session_uses_overridden_engine(self):
        """override_engine() is picked up by get_db_session()."""
        engine_a = _fresh_engine()
        engine_b = _fresh_engine()

        try:
            # Init and write to engine A
            override_engine(engine_a)
            await init_db()
            async with get_db_session() as session:
                session.add(User(id="in-a", display_name="Engine A user"))

            # Switch to engine B (fresh, empty)
            override_engine(engine_b)
            await init_db()

            # Row written to A must not be visible via B
            async with get_db_session() as session:
                user = await session.get(User, "in-a")

            assert user is None, "Row from engine A leaked into engine B"

        finally:
            await engine_a.dispose()
            await engine_b.dispose()

    @pytest.mark.asyncio
    async def test_init_db_uses_overridden_engine(self):
        """override_engine() is picked up by init_db()."""
        engine = _fresh_engine()
        override_engine(engine)

        try:
            await init_db()

            async with engine.connect() as conn:
                table_names = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )

            expected = {t.name for t in Base.metadata.sorted_tables}
            assert expected.issubset(set(table_names))

        finally:
            await engine.dispose()
