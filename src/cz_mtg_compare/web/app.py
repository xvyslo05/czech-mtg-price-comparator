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

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from ..adapters.base import AccountFeatureNotSupported
from ..db.config import DatabaseSettings
from ..db.engine import create_engine_from_settings, session_factory_from_engine
from ..models import Offer, ShopId, ShopStatus
from ..optimizer import DecklistOptimization
from ..scryfall import CardInfo
from ..service import CardCompareService, default_service
from .auth_config import AuthCookieSettings
from .middleware import CSRFMiddleware, SessionLoaderMiddleware
from .schemas import OptimizeDecklistRequest
from .sessions import attach_cookies, create_session


def create_app(
    service: CardCompareService | None = None,
    db_settings: DatabaseSettings | None = None,
    auth_settings: AuthCookieSettings | None = None,
) -> FastAPI:
    svc = service or default_service
    settings = db_settings or DatabaseSettings.from_env()
    auth = auth_settings or AuthCookieSettings.from_env()

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

    return app


app = create_app()
