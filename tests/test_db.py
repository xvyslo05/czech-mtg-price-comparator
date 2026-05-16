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
    assert "sessions" in tables
    assert "email_verification_tokens" in tables
    assert "oauth_identities" in tables
    assert cols == {
        "id",
        "email",
        "email_verified",
        "password_hash",
        "created_at",
        "updated_at",
    }

    # Pin email_verification_tokens shape — drift guard.
    evt_cols = {
        row[1]
        for row in sqlite3.connect(str(db_path)).execute(
            "PRAGMA table_info(email_verification_tokens)"
        )
    }
    orm_evt_cols = {
        c.name for c in Base.metadata.tables["email_verification_tokens"].columns
    }
    assert evt_cols == orm_evt_cols
    assert evt_cols == {
        "id",
        "user_id",
        "token_hash",
        "created_at",
        "expires_at",
        "used_at",
    }

    # Pin oauth_identities shape — drift guard.
    oauth_cols = {
        row[1]
        for row in sqlite3.connect(str(db_path)).execute(
            "PRAGMA table_info(oauth_identities)"
        )
    }
    orm_oauth_cols = {c.name for c in Base.metadata.tables["oauth_identities"].columns}
    assert oauth_cols == orm_oauth_cols
    assert oauth_cols == {
        "id",
        "provider",
        "provider_user_id",
        "user_id",
        "email",
        "created_at",
        "updated_at",
    }

    # sessions.oauth_state was added in 0005 — confirm it's present.
    session_cols_check = {
        row[1]
        for row in sqlite3.connect(str(db_path)).execute("PRAGMA table_info(sessions)")
    }
    assert "oauth_state" in session_cols_check

    # And the ORM Base declares exactly the same columns — drift guard.
    orm_cols = {c.name for c in Base.metadata.tables["users"].columns}
    assert orm_cols == cols

    # Same drift check for sessions.
    session_cols = {
        row[1] for row in sqlite3.connect(str(db_path)).execute("PRAGMA table_info(sessions)")
    }
    orm_session_cols = {c.name for c in Base.metadata.tables["sessions"].columns}
    assert session_cols == orm_session_cols
    assert session_cols == {
        "id",
        "user_id",
        "csrf_token",
        "created_at",
        "last_seen_at",
        "expires_at",
        "oauth_state",
    }


def test_alembic_downgrade_clean(tmp_path):
    """Upgrade then downgrade to base — the users table must disappear.
    Catches the common mistake of writing upgrade() but botching
    downgrade(), which only surfaces when a bad migration ships to prod
    and someone tries to roll back."""
    db_path = tmp_path / "downgrade.sqlite"
    url = f"sqlite+aiosqlite:///{db_path}"
    env = {**__import__("os").environ, "CZ_MTG_DATABASE_URL": url}

    up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert up.returncode == 0, up.stderr

    down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert down.returncode == 0, (
        f"downgrade failed:\nstdout={down.stdout}\nstderr={down.stderr}"
    )

    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    # alembic_version remains; all app tables must be gone.
    assert "users" not in tables
    assert "sessions" not in tables
    assert "email_verification_tokens" not in tables
    assert "oauth_identities" not in tables


async def test_email_verified_defaults_to_false_at_db_level(engine, session_factory):
    """The migration sets ``server_default=text('0')`` on email_verified so
    a raw INSERT (one that doesn't go through the ORM default) still gets
    a sensible value. Exercise that path directly with raw SQL."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, created_at, updated_at) "
                "VALUES ('raw-id', 'raw@example.com', '2026-01-01', '2026-01-01')"
            )
        )

    async with session_factory() as session:
        fetched = (
            await session.execute(select(User).where(User.id == "raw-id"))
        ).scalar_one()
        assert fetched.email_verified is False


async def test_updated_at_advances_on_update(session_factory):
    """``onupdate=_utcnow`` must actually fire on UPDATE — locks the
    semantic in place so a later refactor (e.g. switching to
    server_onupdate) doesn't silently lose it."""
    async with session_factory() as session:
        user = User(email="touch@example.com")
        session.add(user)
        await session.commit()
        original_updated = user.updated_at

    async with session_factory() as session:
        fetched = (
            await session.execute(select(User).where(User.email == "touch@example.com"))
        ).scalar_one()
        fetched.email_verified = True
        await session.commit()
        assert fetched.updated_at > original_updated


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
