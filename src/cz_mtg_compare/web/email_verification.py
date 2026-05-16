"""Email verification token lifecycle.

Plaintext tokens never live in the DB — only SHA-256 digests. That keeps
a backup leak from yielding live tokens, while still letting confirm
look up a row in O(1) (the hash is indexed and unique). Tokens are
single-use (``used_at`` is set on confirm) and short-lived (default 24h,
overridable via env).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EmailVerificationToken, User
from .mailer import Mailer

TOKEN_BYTES = 32

TOKEN_TTL_ENV = "CZ_MTG_EMAIL_VERIFY_TTL_SECONDS"
PUBLIC_BASE_URL_ENV = "CZ_MTG_PUBLIC_BASE_URL"

_DEFAULT_TTL_SECONDS = 60 * 60 * 24  # 24h


def token_ttl() -> timedelta:
    raw = os.environ.get(TOKEN_TTL_ENV)
    try:
        seconds = int(raw) if raw else _DEFAULT_TTL_SECONDS
        if seconds <= 0:
            seconds = _DEFAULT_TTL_SECONDS
    except ValueError:
        seconds = _DEFAULT_TTL_SECONDS
    return timedelta(seconds=seconds)


def public_base_url() -> str:
    """Where verification links should point. Dev default is the local
    server; production sets ``CZ_MTG_PUBLIC_BASE_URL`` to the deployed
    host (e.g. https://card-compare.cz)."""
    return os.environ.get(PUBLIC_BASE_URL_ENV, "http://localhost:8080").rstrip("/")


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def issue_token(db: AsyncSession, user: User) -> str:
    """Insert a new verification token row for the user. Returns the
    *plaintext* token — the caller emails it to the user. Only the hash
    is persisted."""
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    row = EmailVerificationToken(
        user_id=user.id,
        token_hash=_hash_token(raw),
        expires_at=_utcnow() + token_ttl(),
    )
    db.add(row)
    await db.flush()
    return raw


async def consume_token(db: AsyncSession, raw_token: str) -> User | None:
    """Look up a token, mark it used, and return its user. Returns None
    when the token is missing, expired, already used, or its user has
    been deleted — callers should treat all four as the same generic
    failure so the endpoint can't be probed for token validity by
    timing or message differences."""
    digest = _hash_token(raw_token)
    result = await db.execute(
        select(EmailVerificationToken).where(EmailVerificationToken.token_hash == digest)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    if row.used_at is not None:
        return None
    if row.is_expired():
        return None

    user = await db.get(User, row.user_id)
    if user is None:
        return None

    row.used_at = _utcnow()
    user.email_verified = True
    await db.flush()
    return user


def build_verification_url(raw_token: str, base_url: str | None = None) -> str:
    return f"{(base_url or public_base_url()).rstrip('/')}/verify?token={raw_token}"


async def send_verification_email(
    mailer: Mailer, *, to: str, raw_token: str, base_url: str | None = None
) -> None:
    await mailer.send_verification_email(
        to=to, verification_url=build_verification_url(raw_token, base_url=base_url)
    )


def constant_time_compare(a: str, b: str) -> bool:
    """Re-export of hmac.compare_digest for callers that want one
    obvious import path for constant-time comparisons."""
    return hmac.compare_digest(a, b)
