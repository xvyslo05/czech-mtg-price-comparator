"""Async engine + session factory wiring.

The engine is a process-wide singleton owned by the FastAPI app lifespan
(see ``web/app.py``). Tests construct their own engine via
``create_engine_from_settings`` against an in-memory SQLite URL.

Sessions are AsyncSession instances yielded by ``get_session`` — a
FastAPI dependency that opens a session per request and commits/rolls
back on the way out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import DatabaseSettings


def create_engine_from_settings(settings: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(settings.url, echo=settings.echo, future=True)


def session_factory_from_engine(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success or rolling back on error.

    Wired into FastAPI via ``Depends`` once endpoints need DB access; for
    now no endpoint uses it — PR1 of B1 is foundation only.
    """
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
