"""Session lifecycle helpers.

Keep the cookie surface area (read/write/clear) out of route handlers and
middlewares — both call into this module so the rules stay in one place.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Session
from .auth_config import AuthCookieSettings


async def create_session(
    db: AsyncSession,
    settings: AuthCookieSettings,
    *,
    user_id: str | None = None,
) -> Session:
    """Insert a fresh session row. Caller is responsible for committing if
    they hold an open transaction; this function flushes so ``id`` is set."""
    session = Session.new(user_id=user_id, ttl=settings.session_ttl)
    db.add(session)
    await db.flush()
    return session


async def delete_session(db: AsyncSession, session: Session) -> None:
    await db.delete(session)
    await db.flush()


async def load_session(
    db: AsyncSession,
    settings: AuthCookieSettings,
    session_id: str,
) -> Session | None:
    """Return a non-expired session by id, refreshing ``last_seen_at``.
    Returns None if the row is missing or expired; expired rows are deleted
    eagerly so they don't accumulate."""
    row = await db.get(Session, session_id)
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    if row.is_expired(now=now):
        await db.delete(row)
        await db.flush()
        return None
    row.last_seen_at = now
    return row


def attach_cookies(
    response: Response,
    settings: AuthCookieSettings,
    *,
    session_id: str,
    csrf_token: str,
) -> None:
    """Set both cookies on the response with the project's standard
    flags. The session cookie is HttpOnly (JS must never read it); the
    CSRF cookie is readable so the SPA can mirror it into the
    X-CSRF-Token header on state-changing requests."""
    max_age = int(settings.session_ttl.total_seconds())
    response.set_cookie(
        key=settings.session_cookie,
        value=session_id,
        max_age=max_age,
        httponly=True,
        secure=settings.secure,
        samesite=settings.samesite,
        domain=settings.domain,
        path="/",
    )
    response.set_cookie(
        key=settings.csrf_cookie,
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.secure,
        samesite=settings.samesite,
        domain=settings.domain,
        path="/",
    )


def clear_cookies(response: Response, settings: AuthCookieSettings) -> None:
    response.delete_cookie(settings.session_cookie, path="/", domain=settings.domain)
    response.delete_cookie(settings.csrf_cookie, path="/", domain=settings.domain)
