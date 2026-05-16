"""Google OAuth runtime configuration.

Same env-var pattern as ``auth_config`` / ``db.config``. The redirect URI
defaults to ``CZ_MTG_PUBLIC_BASE_URL`` + the callback path so most
deployments only have to set the client id + secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

GOOGLE_CLIENT_ID_ENV = "CZ_MTG_OAUTH_GOOGLE_CLIENT_ID"
GOOGLE_CLIENT_SECRET_ENV = "CZ_MTG_OAUTH_GOOGLE_CLIENT_SECRET"
GOOGLE_REDIRECT_URI_ENV = "CZ_MTG_OAUTH_GOOGLE_REDIRECT_URI"
PUBLIC_BASE_URL_ENV = "CZ_MTG_PUBLIC_BASE_URL"

CALLBACK_PATH = "/v1/auth/oauth/google/callback"


@dataclass(frozen=True)
class GoogleOAuthSettings:
    client_id: str | None
    client_secret: str | None
    redirect_uri: str

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id) and bool(self.client_secret)

    @classmethod
    def from_env(cls) -> GoogleOAuthSettings:
        base_url = os.environ.get(PUBLIC_BASE_URL_ENV, "http://localhost:8080").rstrip("/")
        return cls(
            client_id=os.environ.get(GOOGLE_CLIENT_ID_ENV) or None,
            client_secret=os.environ.get(GOOGLE_CLIENT_SECRET_ENV) or None,
            redirect_uri=os.environ.get(GOOGLE_REDIRECT_URI_ENV) or f"{base_url}{CALLBACK_PATH}",
        )
