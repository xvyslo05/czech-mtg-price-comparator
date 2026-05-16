"""Auth-related runtime configuration.

Cookie names, session TTL, and the Secure / SameSite / domain knobs all
land here. The defaults are production-safe (Secure=True, SameSite=lax,
HttpOnly on the session cookie); local-dev runs over HTTP toggle Secure
off via ``CZ_MTG_COOKIE_SECURE=false``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

SESSION_COOKIE_ENV = "CZ_MTG_SESSION_COOKIE_NAME"
CSRF_COOKIE_ENV = "CZ_MTG_CSRF_COOKIE_NAME"
SESSION_TTL_ENV = "CZ_MTG_SESSION_TTL_SECONDS"
COOKIE_SECURE_ENV = "CZ_MTG_COOKIE_SECURE"
COOKIE_DOMAIN_ENV = "CZ_MTG_COOKIE_DOMAIN"
COOKIE_SAMESITE_ENV = "CZ_MTG_COOKIE_SAMESITE"

_DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthCookieSettings:
    session_cookie: str = "cz_session"
    csrf_cookie: str = "cz_csrf"
    session_ttl: timedelta = timedelta(seconds=_DEFAULT_TTL_SECONDS)
    secure: bool = True
    samesite: str = "lax"  # one of "lax", "strict", "none"
    domain: str | None = None

    @classmethod
    def from_env(cls) -> AuthCookieSettings:
        ttl_raw = os.environ.get(SESSION_TTL_ENV)
        try:
            ttl_seconds = int(ttl_raw) if ttl_raw else _DEFAULT_TTL_SECONDS
            if ttl_seconds <= 0:
                ttl_seconds = _DEFAULT_TTL_SECONDS
        except ValueError:
            ttl_seconds = _DEFAULT_TTL_SECONDS

        samesite = os.environ.get(COOKIE_SAMESITE_ENV, "lax").lower()
        if samesite not in {"lax", "strict", "none"}:
            samesite = "lax"

        return cls(
            session_cookie=os.environ.get(SESSION_COOKIE_ENV, "cz_session"),
            csrf_cookie=os.environ.get(CSRF_COOKIE_ENV, "cz_csrf"),
            session_ttl=timedelta(seconds=ttl_seconds),
            secure=_bool_env(COOKIE_SECURE_ENV, True),
            samesite=samesite,
            domain=os.environ.get(COOKIE_DOMAIN_ENV) or None,
        )
