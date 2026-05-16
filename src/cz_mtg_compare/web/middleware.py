"""Session-loading + CSRF middlewares.

Wired into ``create_app`` via ``app.add_middleware``. Both reach into
``request.app.state.session_factory`` (populated by the lifespan in
``app.py``) so they don't need their own copy of the DB engine.
"""

from __future__ import annotations

import hmac
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .auth_config import AuthCookieSettings
from .sessions import load_session

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Routes that intentionally bypass CSRF (they have no side effects, or
# they're the very endpoints clients call to *get* the CSRF token, or
# they carry their own bearer-style proof — e.g. an email-verification
# token — that an attacker would have to steal anyway).
CSRF_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/v1/auth/csrf",
        "/v1/auth/verify/confirm",
    }
)


class SessionLoaderMiddleware(BaseHTTPMiddleware):
    """Reads the session cookie, looks up the row, and attaches it (or
    ``None``) to ``request.state``. Subsequent handlers and the CSRF
    middleware both read from this attribute."""

    def __init__(self, app, settings: AuthCookieSettings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.session = None
        request.state.user_id = None

        session_id = request.cookies.get(self.settings.session_cookie)
        if session_id:
            factory = getattr(request.app.state, "session_factory", None)
            if factory is not None:
                async with factory() as db:
                    session = await load_session(db, self.settings, session_id)
                    if session is not None:
                        request.state.session = session
                        request.state.user_id = session.user_id
                        # Detach so subsequent DB writes in the request
                        # handler don't observe a row attached to a closed
                        # session. We only need the data, not the
                        # SQLAlchemy identity here.
                        db.expunge(session)
                        await db.commit()
        return await call_next(request)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit CSRF check on state-changing methods.

    Threat model: a CSRF attack only matters when the victim has
    server-side state the attacker wants to abuse (a logged-in session,
    a pre-login anonymous session that tracks something). For requests
    with no session cookie at all, the attacker's forged request would
    be just as unauthenticated as anything they could make from their
    own browser — there's nothing to escalate. So CSRF only fires when
    the request carries a session cookie.

    For requests that DO carry a session:
    - Header must equal CSRF cookie (double-submit).
    - When a session row is loaded, the header must also equal
      session.csrf_token — revoking the row immediately kills the token.

    Skipped for safe methods (GET/HEAD/OPTIONS) and explicitly listed
    exempt paths.
    """

    def __init__(self, app, settings: AuthCookieSettings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in SAFE_METHODS or request.url.path in CSRF_EXEMPT_PATHS:
            return await call_next(request)

        # No session cookie → nothing to protect → let through.
        if self.settings.session_cookie not in request.cookies:
            return await call_next(request)

        header_token = request.headers.get("x-csrf-token") or ""
        cookie_token = request.cookies.get(self.settings.csrf_cookie) or ""

        if not header_token or not cookie_token:
            return JSONResponse(
                status_code=403,
                content={"detail": "csrf token missing"},
            )
        if not hmac.compare_digest(header_token, cookie_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "csrf token mismatch"},
            )

        session = getattr(request.state, "session", None)
        if session is not None and not hmac.compare_digest(session.csrf_token, header_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "csrf token does not match session"},
            )

        return await call_next(request)
