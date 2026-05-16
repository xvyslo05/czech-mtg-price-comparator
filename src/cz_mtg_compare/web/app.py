"""FastAPI app exposing the read-only service surface over HTTP.

What's here:
- ``GET  /v1/health``                 — liveness probe
- ``GET  /v1/shops``                  — configured shops + last-call status
- ``GET  /v1/shops/capabilities``     — per-shop login/cart/watchlist flags
- ``GET  /v1/cards/search``           — single-card cross-shop search
- ``GET  /v1/cards/lookup``           — Scryfall card resolution
- ``POST /v1/decklists/optimize``     — full decklist optimization

What's deliberately not here yet:
- Cart and login endpoints. Those need the credential vault (issue #9 → C)
  so users can authenticate against shops without pasting passwords into
  request bodies. Until the vault lands, account features stay MCP-only
  (env-var credentials).
- Auth (B1). Endpoints are open right now; rate-limiting and per-user API
  keys come in G1/G2.
- Async job queue for ``/decklists/optimize`` (A4). Today it runs inline
  in the handler; a 100-card list fans out to ~600 upstream requests and
  takes seconds. Acceptable for the first MVP slice, must move to a queue
  before public launch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import secrets
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from sqlalchemy import select

from ..adapters.base import AccountFeatureNotSupported
from ..db.config import DatabaseSettings
from ..db.engine import create_engine_from_settings, session_factory_from_engine
from ..db.models import OAuthIdentity, User
from ..models import Offer, ShopId, ShopStatus
from ..optimizer import DecklistOptimization
from ..scryfall import CardInfo
from ..service import CardCompareService, default_service
from .auth_config import AuthCookieSettings
from .auth_schemas import (
    AuthenticatedUser,
    LoginRequest,
    SignupRequest,
    VerifyConfirmRequest,
)
from .email_verification import (
    consume_token,
    issue_token,
    send_verification_email,
)
from .mailer import LoggingMailer, Mailer
from .middleware import CSRFMiddleware, SessionLoaderMiddleware
from .oauth_config import GoogleOAuthSettings
from .oauth_google import (
    AuthlibGoogleOAuthClient,
    GoogleOAuthClient,
    GoogleUserInfo,
    OAuthExchangeError,
)
from .passwords import hash_password, needs_rehash, verify_password
from .schemas import OptimizeDecklistRequest
from .sessions import attach_cookies, clear_cookies, create_session, delete_session


def create_app(
    service: CardCompareService | None = None,
    db_settings: DatabaseSettings | None = None,
    auth_settings: AuthCookieSettings | None = None,
    mailer: Mailer | None = None,
    google_oauth_settings: GoogleOAuthSettings | None = None,
    google_oauth_client: GoogleOAuthClient | None = None,
) -> FastAPI:
    svc = service or default_service
    settings = db_settings or DatabaseSettings.from_env()
    auth = auth_settings or AuthCookieSettings.from_env()
    mailer_instance: Mailer = mailer or LoggingMailer()
    google_settings = google_oauth_settings or GoogleOAuthSettings.from_env()
    # Only construct the real client when fully configured; tests inject
    # their own. When the deployment hasn't set credentials, leave the
    # client at None and let the endpoints surface a 503.
    google_client: GoogleOAuthClient | None = google_oauth_client
    if google_client is None and google_settings.is_configured:
        google_client = AuthlibGoogleOAuthClient(google_settings)
    public_base_url = urlsplit(google_settings.redirect_uri)._replace(path="").geturl().rstrip("/")

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine_from_settings(settings)
        app.state.db_engine = engine
        app.state.session_factory = session_factory_from_engine(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="cz-mtg-compare",
        version="0.6.0",
        description=(
            "Czech MTG price aggregator. Read-only HTTP surface over the same "
            "engine that powers the MCP server (search_card, optimize_decklist, "
            "lookup_card, list_shops). See issue #9 for the roadmap."
        ),
        lifespan=_lifespan,
    )

    # Middleware is applied bottom-up at request time: CSRF is added
    # AFTER SessionLoader so the CSRF check can read request.state.session.
    app.add_middleware(CSRFMiddleware, settings=auth)
    app.add_middleware(SessionLoaderMiddleware, settings=auth)

    @app.exception_handler(ValueError)
    async def _value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(AccountFeatureNotSupported)
    async def _capability_handler(
        _request: Request, exc: AccountFeatureNotSupported
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "shop": exc.shop_id,
                "feature": exc.feature,
            },
        )

    @app.get("/v1/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/shops", response_model=list[ShopStatus], tags=["shops"])
    def list_shops() -> list[ShopStatus]:
        return svc.list_shops()

    @app.get("/v1/shops/capabilities", tags=["shops"])
    def shop_capabilities() -> list[dict[str, Any]]:
        return svc.shop_account_capabilities()

    @app.get("/v1/cards/search", response_model=list[Offer], tags=["cards"])
    async def search_card(
        name: str = Query(min_length=1),
        edition: str | None = None,
        in_stock_only: bool = True,
        include_non_playable: bool = False,
        shops: list[ShopId] | None = Query(default=None),
        exclude_shops: list[ShopId] | None = Query(default=None),
    ) -> list[Offer]:
        return await svc.search_card(
            name=name,
            edition=edition,
            in_stock_only=in_stock_only,
            include_non_playable=include_non_playable,
            shops=shops,
            exclude_shops=exclude_shops,
        )

    @app.get("/v1/cards/lookup", response_model=CardInfo | None, tags=["cards"])
    async def lookup_card(
        name: str = Query(min_length=1),
        exact: bool = False,
    ) -> CardInfo | None:
        return await svc.lookup_card(name, exact=exact)

    @app.post(
        "/v1/decklists/optimize",
        response_model=DecklistOptimization,
        tags=["decklists"],
    )
    async def optimize_decklist(payload: OptimizeDecklistRequest) -> DecklistOptimization:
        return await svc.optimize_decklist(
            decklist=payload.decklist,
            in_stock_only=payload.in_stock_only,
            include_non_playable=payload.include_non_playable,
            shops=payload.shops,
            exclude_shops=payload.exclude_shops,
            strategy=payload.strategy,
        )

    @app.get("/v1/auth/csrf", tags=["auth"])
    async def issue_csrf(request: Request) -> JSONResponse:
        """Mint or refresh a CSRF token. Creates an anonymous session if
        the request has none. Returns the token in the body AND sets
        both the session and CSRF cookies on the response.

        Exempt from CSRF verification (see CSRF_EXEMPT_PATHS) because
        unauthenticated clients have no token to send yet."""
        factory = request.app.state.session_factory
        session = request.state.session

        async with factory() as db:
            if session is None:
                session = await create_session(db, auth, user_id=None)
                await db.commit()

            payload = {"csrf_token": session.csrf_token}
            response = JSONResponse(content=payload)
            attach_cookies(
                response,
                auth,
                session_id=session.id,
                csrf_token=session.csrf_token,
            )
            return response

    @app.get("/v1/auth/whoami", tags=["auth"])
    async def whoami(request: Request) -> dict[str, Any]:
        """Report the caller's auth state. Returns
        ``{"authenticated": false}`` for anonymous and missing sessions —
        callers shouldn't have to distinguish those two cases."""
        session = request.state.session
        if session is None or session.user_id is None:
            return {"authenticated": False, "user_id": None}
        return {"authenticated": True, "user_id": session.user_id}

    @app.post(
        "/v1/auth/signup",
        response_model=AuthenticatedUser,
        status_code=201,
        tags=["auth"],
    )
    async def signup(payload: SignupRequest, request: Request) -> JSONResponse:
        """Create a new email/password account and log the user in.

        Email is stored lowercase to avoid duplicate accounts like
        Alice@Example.com vs alice@example.com. The password is hashed
        with argon2id; the plaintext is never logged or persisted.

        A session is minted and the cookies are set on the response, so
        the client is immediately authenticated — same as a fresh
        login. The session has full privileges before email verification
        (PR4); that's intentional for v1 so users can use the product
        right away. Verification gates land with PR4 if a route needs
        them.
        """
        email = payload.email.lower()
        factory = request.app.state.session_factory

        async with factory() as db:
            existing = await db.execute(select(User).where(User.email == email))
            if existing.scalar_one_or_none() is not None:
                return JSONResponse(
                    status_code=409,
                    content={"detail": "email already registered"},
                )

            user = User(email=email, password_hash=hash_password(payload.password))
            db.add(user)
            await db.flush()

            session = await create_session(db, auth, user_id=user.id)
            raw_token = await issue_token(db, user)
            await db.commit()

            # Send AFTER commit so a delivery error doesn't leave the
            # user with no DB row but a delivered email pointing at a
            # ghost. Mailer failures are caught below — signup still
            # succeeds; the user can request a resend from settings.
            try:
                await send_verification_email(
                    mailer_instance,
                    to=user.email,
                    raw_token=raw_token,
                )
            except Exception:  # noqa: BLE001 — best-effort delivery
                pass

            body = AuthenticatedUser(
                user_id=user.id, email=user.email, email_verified=user.email_verified
            ).model_dump()
            response = JSONResponse(status_code=201, content=body)
            attach_cookies(
                response,
                auth,
                session_id=session.id,
                csrf_token=session.csrf_token,
            )
            return response

    @app.post(
        "/v1/auth/login",
        response_model=AuthenticatedUser,
        tags=["auth"],
    )
    async def login(payload: LoginRequest, request: Request) -> JSONResponse:
        """Verify credentials and mint a fresh session.

        Always returns the same error on bad email AND bad password so
        the response can't be used as a user-enumeration oracle. On
        successful login a new session row is created (the old one, if
        any, is left in place — logout is the explicit revocation
        path).
        """
        email = payload.email.lower()
        factory = request.app.state.session_factory

        invalid_credentials = JSONResponse(
            status_code=401,
            content={"detail": "invalid email or password"},
        )

        async with factory() as db:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()
            if user is None or user.password_hash is None:
                return invalid_credentials
            if not verify_password(user.password_hash, payload.password):
                return invalid_credentials

            # Upgrade the hash if argon2's defaults have moved on since
            # the row was last written. Safe to do here because we
            # already have the plaintext in hand and have just
            # validated it.
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(payload.password)

            session = await create_session(db, auth, user_id=user.id)
            await db.commit()

            body = AuthenticatedUser(
                user_id=user.id, email=user.email, email_verified=user.email_verified
            ).model_dump()
            response = JSONResponse(content=body)
            attach_cookies(
                response,
                auth,
                session_id=session.id,
                csrf_token=session.csrf_token,
            )
            return response

    @app.post("/v1/auth/verify/request", status_code=202, tags=["auth"])
    async def verify_request(request: Request) -> JSONResponse:
        """Re-issue a verification token for the logged-in user and
        re-send the email. No-op (still 202) when the user is already
        verified — keeps the client side dumb."""
        session = request.state.session
        if session is None or session.user_id is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "authentication required"},
            )

        factory = request.app.state.session_factory
        async with factory() as db:
            user = await db.get(User, session.user_id)
            if user is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "authentication required"},
                )
            if user.email_verified:
                return JSONResponse(status_code=202, content={"sent": False})

            raw_token = await issue_token(db, user)
            await db.commit()

            try:
                await send_verification_email(
                    mailer_instance,
                    to=user.email,
                    raw_token=raw_token,
                )
            except Exception:  # noqa: BLE001
                pass

            return JSONResponse(status_code=202, content={"sent": True})

    @app.post("/v1/auth/verify/confirm", tags=["auth"])
    async def verify_confirm(
        payload: VerifyConfirmRequest, request: Request
    ) -> JSONResponse:
        """Spend a verification token. The token itself is the bearer
        credential — no session required (the typical UX is "user clicks
        a link on a device that isn't logged in"). Returns a generic
        error for any failure mode so the endpoint can't be probed for
        which tokens are valid vs already used vs expired."""
        factory = request.app.state.session_factory
        invalid = JSONResponse(
            status_code=400,
            content={"detail": "invalid or expired token"},
        )

        async with factory() as db:
            user = await consume_token(db, payload.token)
            if user is None:
                return invalid
            await db.commit()
            body = AuthenticatedUser(
                user_id=user.id, email=user.email, email_verified=user.email_verified
            ).model_dump()
            return JSONResponse(content=body)

    @app.get("/v1/auth/oauth/google/start", tags=["auth"])
    async def oauth_google_start(request: Request) -> Response:
        """Begin a Google OAuth flow. Creates an anonymous session (or
        reuses the caller's) to hold the random ``state`` that protects
        the callback, then 302s to Google. CSRF doesn't gate GET, so the
        only protection on this endpoint is the state we plant — and
        verify on /callback.
        """
        if google_client is None:
            return JSONResponse(
                status_code=503,
                content={"detail": "google oauth not configured"},
            )

        factory = request.app.state.session_factory
        existing = request.state.session
        state_token = secrets.token_urlsafe(32)

        async with factory() as db:
            if existing is None:
                session = await create_session(db, auth, user_id=None)
            else:
                session = await db.get(type(existing), existing.id)
                if session is None:  # raced with expiry
                    session = await create_session(db, auth, user_id=None)
            session.oauth_state = state_token
            await db.commit()
            session_id = session.id
            csrf_token = session.csrf_token

        auth_url = google_client.authorization_url(
            state=state_token, redirect_uri=google_settings.redirect_uri
        )
        response = RedirectResponse(url=auth_url, status_code=302)
        attach_cookies(response, auth, session_id=session_id, csrf_token=csrf_token)
        return response

    @app.get("/v1/auth/oauth/google/callback", tags=["auth"])
    async def oauth_google_callback(
        request: Request,
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ) -> Response:
        """Finish a Google OAuth flow. Verifies the state, exchanges the
        code for an ID token, then either creates a new local user,
        attaches the identity to an existing email/password account
        (only when Google reports ``email_verified``), or logs in an
        already-linked user."""
        if google_client is None:
            return JSONResponse(
                status_code=503,
                content={"detail": "google oauth not configured"},
            )

        generic_invalid = JSONResponse(
            status_code=400,
            content={"detail": "invalid oauth callback"},
        )

        if error is not None or not code or not state:
            return generic_invalid

        cur_session = request.state.session
        if cur_session is None or not cur_session.oauth_state:
            return generic_invalid

        factory = request.app.state.session_factory

        # Single-use: snapshot the expected state and clear the row
        # BEFORE comparing. Even a failed attempt burns the slot, so an
        # attacker who can replay the callback URL or brute-force the
        # state value can't keep trying.
        expected_state = cur_session.oauth_state
        async with factory() as db:
            row = await db.get(type(cur_session), cur_session.id)
            if row is not None:
                row.oauth_state = None
                await db.commit()

        # Constant-time compare mirrors the CSRF middleware's pattern.
        import hmac as _hmac

        if not _hmac.compare_digest(expected_state, state):
            return generic_invalid

        try:
            info: GoogleUserInfo = await google_client.exchange_code(
                code=code, redirect_uri=google_settings.redirect_uri
            )
        except OAuthExchangeError:
            return generic_invalid

        async with factory() as db:
            # 1) existing oauth_identities row?
            identity_q = await db.execute(
                select(OAuthIdentity).where(
                    OAuthIdentity.provider == "google",
                    OAuthIdentity.provider_user_id == info.provider_user_id,
                )
            )
            identity = identity_q.scalar_one_or_none()

            if identity is not None:
                user = await db.get(User, identity.user_id)
                if user is None:  # orphaned — shouldn't happen with FK cascade
                    return generic_invalid
            else:
                # 2) match by email?
                user_q = await db.execute(
                    select(User).where(User.email == info.email)
                )
                user = user_q.scalar_one_or_none()

                if user is not None and not info.email_verified:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "detail": (
                                "email already registered; sign in with your "
                                "password first to link Google"
                            )
                        },
                    )

                if user is None:
                    # 3) brand new user.
                    user = User(
                        email=info.email,
                        email_verified=info.email_verified,
                        password_hash=None,
                    )
                    db.add(user)
                    await db.flush()
                elif info.email_verified and not user.email_verified:
                    user.email_verified = True

                db.add(
                    OAuthIdentity(
                        provider="google",
                        provider_user_id=info.provider_user_id,
                        user_id=user.id,
                        email=info.email,
                    )
                )
                await db.flush()

            # Rotate session: drop the old anon row, mint a fresh one
            # bound to this user. Same pattern as the email/password
            # login endpoint.
            old = await db.get(type(cur_session), cur_session.id)
            if old is not None:
                await delete_session(db, old)
            new_session = await create_session(db, auth, user_id=user.id)
            await db.commit()

            session_id = new_session.id
            csrf_token = new_session.csrf_token

        response = RedirectResponse(url=f"{public_base_url}/", status_code=302)
        attach_cookies(response, auth, session_id=session_id, csrf_token=csrf_token)
        return response

    @app.post("/v1/auth/logout", status_code=204, tags=["auth"])
    async def logout(request: Request) -> Response:
        """Delete the current session (if any) and clear cookies.

        Returns 204 regardless of whether a session was present — the
        client should treat it as "you are now logged out" either way.
        """
        session = request.state.session
        factory = request.app.state.session_factory
        if session is not None:
            async with factory() as db:
                # Reattach the session row to this DB session so we can
                # delete it — SessionLoaderMiddleware expunged it after
                # loading.
                row = await db.get(type(session), session.id)
                if row is not None:
                    await delete_session(db, row)
                    await db.commit()

        response = Response(status_code=204)
        clear_cookies(response, auth)
        return response

    return app


app = create_app()
