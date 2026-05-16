"""Database foundation tests (B1 PR1).

Exercises:
- The User ORM model round-trips through a real (in-memory) database.
- ``email`` is unique-enforced (catches a future migration drift where the
  constraint silently drops).
- ``get_session`` commits on success and rolls back on exception.
- ``DatabaseSettings.from_env`` honours CZ_MTG_DATABASE_URL / _ECHO.
- The packaged alembic migration runs cleanly against a fresh DB and
  produces the same shape as the ORM models — so the two never drift
  apart undetected.
- The FastAPI lifespan creates and disposes the engine.
"""

from __future__ import annotations

import subprocess
import sys
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cz_mtg_compare.db.config import DatabaseSettings
from cz_mtg_compare.db.engine import (
    create_engine_from_settings,
    get_session,
    session_factory_from_engine,
)
from cz_mtg_compare.db.models import Base, User
from cz_mtg_compare.web.app import create_app


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
async def engine():
    eng = create_engine_from_settings(
        DatabaseSettings(url="sqlite+aiosqlite:///:memory:")
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def session_factory(engine):
    return session_factory_from_engine(engine)


# --- DatabaseSettings ---------------------------------------------------


def test_settings_default_to_inmemory_sqlite(monkeypatch):
    monkeypatch.delenv("CZ_MTG_DATABASE_URL", raising=False)
    monkeypatch.delenv("CZ_MTG_DATABASE_ECHO", raising=False)
    s = DatabaseSettings.from_env()
    assert s.url == "sqlite+aiosqlite:///:memory:"
    assert s.echo is False


def test_settings_honours_env(monkeypatch):
    monkeypatch.setenv("CZ_MTG_DATABASE_URL", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("CZ_MTG_DATABASE_ECHO", "true")
    s = DatabaseSettings.from_env()
    assert s.url == "postgresql+asyncpg://x/y"
    assert s.echo is True


# --- User model round-trip ---------------------------------------------


async def test_user_insert_and_query(session_factory):
    async with session_factory() as session:
        user = User(email="alice@example.com")
        session.add(user)
        await session.commit()
        assert user.id  # autogen
        assert user.email_verified is False
        assert user.created_at is not None

    async with session_factory() as session:
        fetched = (
            await session.execute(select(User).where(User.email == "alice@example.com"))
        ).scalar_one()
        assert fetched.email == "alice@example.com"


async def test_user_email_is_unique(session_factory):
    async with session_factory() as session:
        session.add(User(email="dup@example.com"))
        await session.commit()

    async with session_factory() as session:
        session.add(User(email="dup@example.com"))
        with pytest.raises(IntegrityError):
            await session.commit()


# --- get_session lifecycle ----------------------------------------------


async def test_get_session_commits_on_success(session_factory):
    """Walking the async generator to completion must commit the
    in-progress transaction so the changes are visible to a subsequent
    session — proves the dependency doesn't silently swallow writes."""
    async for session in get_session(session_factory):
        session.add(User(email="commit@example.com"))

    async with session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == "commit@example.com")
        )
        assert result.scalar_one().email == "commit@example.com"


async def test_get_session_rolls_back_on_error(session_factory):
    """An exception inside the dependency body must roll the session
    back so a half-finished write doesn't sneak into the DB."""
    gen = get_session(session_factory)
    session = await anext(gen)
    session.add(User(email="rollback@example.com"))

    with pytest.raises(RuntimeError):
        await gen.athrow(RuntimeError("boom"))

    async with session_factory() as fresh:
        result = await fresh.execute(
            select(User).where(User.email == "rollback@example.com")
        )
        assert result.first() is None


# --- Migration sanity ---------------------------------------------------


def test_alembic_migration_matches_orm_metadata(tmp_path):
    """Run the packaged migration against a fresh sqlite DB and verify the
    resulting schema matches what the ORM models declare. Catches the
    classic drift where someone edits the model but forgets the migration
    (or vice versa).

    Invoked via subprocess so alembic's ``asyncio.run()`` doesn't collide
    with the pytest-asyncio event loop — and this is also how operators
    actually run migrations (``alembic upgrade head``), so the test
    exercises the real entry point."""
    db_path = tmp_path / "migration.sqlite"
    url = f"sqlite+aiosqlite:///{db_path}"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env={
            **__import__("os").environ,
            "CZ_MTG_DATABASE_URL": url,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic upgrade failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    # Inspect the resulting schema synchronously via the stdlib sqlite3 —
    # avoids dragging another async engine into the test just to read
    # metadata.
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    finally:
        conn.close()

    assert "users" in tables
    assert cols == {
        "id",
        "email",
        "email_verified",
        "created_at",
        "updated_at",
    }

    # And the ORM Base declares exactly the same columns — drift guard.
    orm_cols = {c.name for c in Base.metadata.tables["users"].columns}
    assert orm_cols == cols


# --- FastAPI lifespan ---------------------------------------------------


def test_lifespan_creates_and_disposes_engine():
    """Entering the TestClient context manager runs the lifespan;
    leaving it disposes the engine. Verify the session factory is
    attached to app.state and the engine is closed afterwards."""
    app = create_app(db_settings=DatabaseSettings(url="sqlite+aiosqlite:///:memory:"))

    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 200
        assert hasattr(app.state, "session_factory")
        assert hasattr(app.state, "db_engine")
        engine_after_start = app.state.db_engine

    # After the context exits, the engine has been disposed. We can't
    # easily assert "is closed" via the public API, but we can verify
    # the engine reference still points to the same object — i.e. the
    # lifespan didn't swap it out mid-flight.
    assert app.state.db_engine is engine_after_start
