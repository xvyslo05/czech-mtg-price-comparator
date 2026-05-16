"""ORM models.

Kept deliberately portable: no postgres-only types in this PR. The
migration layer (alembic) decides what the canonical schema is per
backend; the model definitions stay backend-agnostic so the same code
runs on SQLite (tests) and Postgres (prod).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SESSION_ID_BYTES = 32
CSRF_TOKEN_BYTES = 32


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _new_session_id() -> str:
    return secrets.token_urlsafe(SESSION_ID_BYTES)


def _new_csrf_token() -> str:
    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, email={self.email!r}, verified={self.email_verified})"


class Session(Base):
    """Server-side session.

    The cookie sent to the browser carries only the opaque ``id``; everything
    else lives in this row. Anonymous sessions (``user_id`` null) exist so
    pre-login CSRF tokens have a stable home — they get upgraded in-place to
    authenticated sessions on login.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_session_id)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    csrf_token: Mapped[str] = mapped_column(
        String(64), nullable=False, default=_new_csrf_token
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def is_expired(self, *, now: datetime | None = None) -> bool:
        # SQLite strips tzinfo on round-trip. Treat naive values as UTC so
        # the comparison works regardless of backend; Postgres preserves
        # the offset and lands in the same branch.
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        current = now or _utcnow()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current >= expires

    @classmethod
    def new(
        cls,
        *,
        user_id: str | None = None,
        ttl: timedelta,
    ) -> Session:
        now = _utcnow()
        return cls(
            user_id=user_id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + ttl,
        )
