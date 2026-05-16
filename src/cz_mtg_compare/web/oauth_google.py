"""Google OAuth client.

A Protocol so tests can substitute a recording fake without hitting the
network; the production implementation uses authlib for token exchange
and ID-token validation against Google's JWKS.

This module deliberately doesn't reach into ``app.state`` or pull session
helpers — it's the boundary between our app and Google. The app glue
lives in ``web/app.py`` where it can compose this with sessions, users,
and oauth_identities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from authlib.integrations.httpx_client import AsyncOAuth2Client
import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from .oauth_config import GoogleOAuthSettings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_SCOPE = "openid email profile"


@dataclass(frozen=True)
class GoogleUserInfo:
    """Subset of the ID token / userinfo we actually use.

    ``provider_user_id`` is Google's ``sub`` claim — stable, opaque,
    survives email changes.
    """

    provider_user_id: str
    email: str
    email_verified: bool
    name: str | None = None


class OAuthExchangeError(Exception):
    """Raised when the code → token → user-info pipeline fails. The
    endpoint turns this into a generic 400 — Google's error text never
    reaches the response body."""


class GoogleOAuthClient(Protocol):
    """Interface every implementation must satisfy. Keep it minimal so
    tests can fake it with a one-liner."""

    def authorization_url(self, *, state: str, redirect_uri: str) -> str: ...

    async def exchange_code(
        self, *, code: str, redirect_uri: str
    ) -> GoogleUserInfo: ...


class AuthlibGoogleOAuthClient:
    """Production implementation. Validates the ID token signature
    against Google's published JWKS on every exchange — never trust the
    body of the token response without it.
    """

    def __init__(self, settings: GoogleOAuthSettings) -> None:
        if not settings.is_configured:
            raise ValueError("Google OAuth client requires both client_id and client_secret")
        self._settings = settings
        # ``aud`` must equal our client_id; ``iss`` must come from Google.
        # Expiry / not-before checks happen inside ``validate`` based on
        # standard registered claims.
        self._claims_registry = JWTClaimsRegistry(
            aud={"essential": True, "value": settings.client_id},
            iss={"essential": True, "values": list(GOOGLE_ISSUERS)},
        )

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        client = AsyncOAuth2Client(
            client_id=self._settings.client_id,
            redirect_uri=redirect_uri,
            scope=GOOGLE_SCOPE,
        )
        url, _ = client.create_authorization_url(GOOGLE_AUTH_URL, state=state)
        return url

    async def exchange_code(
        self, *, code: str, redirect_uri: str
    ) -> GoogleUserInfo:
        async with AsyncOAuth2Client(
            client_id=self._settings.client_id,
            client_secret=self._settings.client_secret,
            redirect_uri=redirect_uri,
        ) as client:
            try:
                token = await client.fetch_token(
                    GOOGLE_TOKEN_URL, code=code, grant_type="authorization_code"
                )
            except Exception as exc:  # noqa: BLE001
                raise OAuthExchangeError("token exchange failed") from exc

        id_token = token.get("id_token")
        if not id_token:
            raise OAuthExchangeError("response missing id_token")

        claims = await self._validate_id_token(id_token)
        return _claims_to_user(claims)

    async def _validate_id_token(self, id_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as http:
            try:
                resp = await http.get(GOOGLE_JWKS_URL)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise OAuthExchangeError("could not load Google JWKS") from exc
            jwks = KeySet.import_key_set(resp.json())

        try:
            token = jwt.decode(id_token, jwks, algorithms=["RS256"])
            self._claims_registry.validate(token.claims)
        except JoseError as exc:
            raise OAuthExchangeError("id_token validation failed") from exc

        return dict(token.claims)


def _claims_to_user(claims: dict[str, Any]) -> GoogleUserInfo:
    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        raise OAuthExchangeError("id_token missing sub or email")
    return GoogleUserInfo(
        provider_user_id=str(sub),
        email=str(email).lower(),
        email_verified=bool(claims.get("email_verified", False)),
        name=claims.get("name"),
    )
