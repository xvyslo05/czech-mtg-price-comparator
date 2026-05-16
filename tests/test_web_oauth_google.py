"""Google OAuth endpoint + flow tests (B1 PR5).

What's pinned:
- /start 302s to Google with our client_id and a random state, plants the
  state on the caller's session, and sets the session + CSRF cookies.
- /start 503s when client credentials aren't configured (graceful no-config).
- /callback rejects on: missing/forged state, provider error, exchange
  failure. Each failure path returns the same generic 400 so the endpoint
  can't be probed for which precondition failed.
- /callback happy paths:
    * brand-new Google user — creates User + OAuthIdentity, marks
      email_verified from Google's claim, rotates to an authenticated
      session.
    * existing OAuthIdentity — logs the same user back in, no new row.
    * existing email match + Google says verified — auto-links, marks
      user verified.
    * existing email match + Google says NOT verified — 400 with the
      "use password first" detail.
- The state is single-use: cleared after one /callback attempt regardless
  of outcome, so a replay can't slip through.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy
from fastapi.testclient import TestClient

from cz_mtg_compare.db.config import DatabaseSettings
from cz_mtg_compare.db.models import Base, OAuthIdentity, Session as SessionRow, User
from cz_mtg_compare.web.app import create_app
from cz_mtg_compare.web.auth_config import AuthCookieSettings
from cz_mtg_compare.web.oauth_config import GoogleOAuthSettings
from cz_mtg_compare.web.oauth_google import GoogleUserInfo, OAuthExchangeError


class _RecordingGoogleOAuthClient:
    """Test stand-in. Records calls and returns a pre-canned GoogleUserInfo
    (or raises) on exchange_code — zero network access."""

    def __init__(
        self,
        info: GoogleUserInfo | None = None,
        *,
        raise_on_exchange: bool = False,
    ) -> None:
        self.info = info
        self.raise_on_exchange = raise_on_exchange
        self.auth_url_calls: list[dict[str, str]] = []
        self.exchange_calls: list[dict[str, str]] = []

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        self.auth_url_calls.append({"state": state, "redirect_uri": redirect_uri})
        # Use a deterministic URL so tests can assert on it.
        return (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id=test-client-id&state={state}"
            f"&redirect_uri={redirect_uri}"
            "&response_type=code&scope=openid+email+profile"
        )

    async def exchange_code(self, *, code: str, redirect_uri: str) -> GoogleUserInfo:
        self.exchange_calls.append({"code": code, "redirect_uri": redirect_uri})
        if self.raise_on_exchange:
            raise OAuthExchangeError("simulated failure")
        assert self.info is not None, "test must set .info before exchange"
        return self.info


def _build_app(
    tmp_path,
    *,
    oauth_client: _RecordingGoogleOAuthClient | None,
    google_settings: GoogleOAuthSettings | None = None,
):
    db_path = tmp_path / "oauth.sqlite"
    async_url = f"sqlite+aiosqlite:///{db_path}"
    sync_url = f"sqlite:///{db_path}"

    sync_engine = sqlalchemy.create_engine(sync_url)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    auth = AuthCookieSettings(
        secure=False, samesite="lax", session_ttl=timedelta(hours=1)
    )
    settings = google_settings or GoogleOAuthSettings(
        client_id="test-client-id",
        client_secret="test-secret",
        redirect_uri="http://localhost:8080/v1/auth/oauth/google/callback",
    )
    app = create_app(
        db_settings=DatabaseSettings(url=async_url),
        auth_settings=auth,
        google_oauth_settings=settings,
        google_oauth_client=oauth_client,
    )
    return app, sync_url, auth, settings


@pytest.fixture
def app_factory(tmp_path):
    def _factory(
        *,
        oauth_client: _RecordingGoogleOAuthClient | None = None,
        google_settings: GoogleOAuthSettings | None = None,
    ):
        return _build_app(
            tmp_path, oauth_client=oauth_client, google_settings=google_settings
        )

    return _factory


def _insert_user(sync_url: str, *, email: str, email_verified: bool = False) -> str:
    """Insert an existing email/password user. Returns user_id."""
    import secrets
    from datetime import datetime, timezone

    user_id = secrets.token_urlsafe(16)
    now = datetime.now(timezone.utc)
    eng = sqlalchemy.create_engine(sync_url)
    with eng.begin() as conn:
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO users (id, email, email_verified, password_hash, created_at, updated_at) "
                "VALUES (:id, :email, :verified, 'fake-hash', :now, :now)"
            ),
            {"id": user_id, "email": email, "verified": int(email_verified), "now": now},
        )
    eng.dispose()
    return user_id


def _insert_oauth_identity(
    sync_url: str, *, user_id: str, provider_user_id: str, email: str
) -> None:
    import secrets
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    eng = sqlalchemy.create_engine(sync_url)
    with eng.begin() as conn:
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO oauth_identities "
                "(id, provider, provider_user_id, user_id, email, created_at, updated_at) "
                "VALUES (:id, 'google', :pid, :uid, :email, :now, :now)"
            ),
            {
                "id": secrets.token_urlsafe(16),
                "pid": provider_user_id,
                "uid": user_id,
                "email": email,
                "now": now,
            },
        )
    eng.dispose()


def _row_count(sync_url: str, table: str) -> int:
    eng = sqlalchemy.create_engine(sync_url)
    try:
        with eng.connect() as conn:
            return conn.execute(
                sqlalchemy.text(f"SELECT COUNT(*) FROM {table}")
            ).scalar_one()
    finally:
        eng.dispose()


# --- /start -------------------------------------------------------------


def test_start_redirects_to_google_with_state(app_factory):
    client_stub = _RecordingGoogleOAuthClient()
    app, sync_url, auth, settings = app_factory(oauth_client=client_stub)

    with TestClient(app, follow_redirects=False) as client:
        r = client.get("/v1/auth/oauth/google/start")

    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("https://accounts.google.com/")
    assert "client_id=test-client-id" in location
    assert "state=" in location

    # Recorded state matches what was put on the session row.
    state = client_stub.auth_url_calls[0]["state"]
    eng = sqlalchemy.create_engine(sync_url)
    try:
        with eng.connect() as conn:
            row = conn.execute(
                sqlalchemy.text("SELECT oauth_state FROM sessions")
            ).scalar_one()
    finally:
        eng.dispose()
    assert row == state


def test_start_503s_when_not_configured(app_factory):
    """Production deployments that haven't set CLIENT_ID/SECRET should
    serve a clean 503 rather than crash. Test by passing settings with
    missing credentials AND no client."""
    settings = GoogleOAuthSettings(
        client_id=None,
        client_secret=None,
        redirect_uri="http://localhost:8080/v1/auth/oauth/google/callback",
    )
    app, _, _, _ = app_factory(oauth_client=None, google_settings=settings)
    with TestClient(app) as client:
        r = client.get("/v1/auth/oauth/google/start")
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


def test_start_sets_session_and_csrf_cookies(app_factory):
    app, _, auth, _ = app_factory(oauth_client=_RecordingGoogleOAuthClient())
    with TestClient(app, follow_redirects=False) as client:
        r = client.get("/v1/auth/oauth/google/start")
    assert r.cookies.get(auth.session_cookie)
    assert r.cookies.get(auth.csrf_cookie)


# --- /callback failure modes -------------------------------------------


def test_callback_400s_on_provider_error(app_factory):
    app, _, _, _ = app_factory(oauth_client=_RecordingGoogleOAuthClient())
    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/oauth/google/start")
        r = client.get(
            "/v1/auth/oauth/google/callback",
            params={"error": "access_denied"},
        )
    assert r.status_code == 400


def test_callback_400s_on_missing_state(app_factory):
    app, _, _, _ = app_factory(oauth_client=_RecordingGoogleOAuthClient())
    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/oauth/google/start")
        r = client.get(
            "/v1/auth/oauth/google/callback",
            params={"code": "x"},
        )
    assert r.status_code == 400


def test_callback_400s_on_state_mismatch_and_clears_stored_state(app_factory):
    """State is single-use: even when the mismatch is rejected, the row's
    stored state is cleared, so a follow-up replay with the correct
    state value also fails."""
    stub = _RecordingGoogleOAuthClient(
        info=GoogleUserInfo(
            provider_user_id="goog-1",
            email="new@example.com",
            email_verified=True,
        )
    )
    app, sync_url, _, _ = app_factory(oauth_client=stub)
    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/oauth/google/start")
        # First attempt with a bogus state must 400.
        bad = client.get(
            "/v1/auth/oauth/google/callback",
            params={"code": "x", "state": "totally-wrong"},
        )
        assert bad.status_code == 400

        # The real state was captured by the stub.
        real_state = stub.auth_url_calls[0]["state"]
        # Second attempt with the *correct* state must also 400 because
        # the stored value was cleared on the first failure.
        replay = client.get(
            "/v1/auth/oauth/google/callback",
            params={"code": "x", "state": real_state},
        )
        assert replay.status_code == 400

    # And no user / identity got created during either attempt.
    assert _row_count(sync_url, "users") == 0
    assert _row_count(sync_url, "oauth_identities") == 0


def test_callback_400s_on_exchange_failure(app_factory):
    """Authlib errors must not bleed through — generic 400."""
    stub = _RecordingGoogleOAuthClient(raise_on_exchange=True)
    app, _, _, _ = app_factory(oauth_client=stub)
    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/oauth/google/start")
        state = stub.auth_url_calls[0]["state"]
        r = client.get(
            "/v1/auth/oauth/google/callback",
            params={"code": "x", "state": state},
        )
    assert r.status_code == 400


# --- /callback happy paths ---------------------------------------------


def test_callback_creates_new_user_and_logs_in(app_factory):
    stub = _RecordingGoogleOAuthClient(
        info=GoogleUserInfo(
            provider_user_id="goog-new",
            email="new-google@example.com",
            email_verified=True,
            name="New User",
        )
    )
    app, sync_url, _, _ = app_factory(oauth_client=stub)

    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/oauth/google/start")
        state = stub.auth_url_calls[0]["state"]
        r = client.get(
            "/v1/auth/oauth/google/callback",
            params={"code": "auth-code", "state": state},
        )
        assert r.status_code == 302
        # Frontend gets a redirect (back to PUBLIC_BASE_URL).
        assert r.headers["location"].startswith("http")

        me = client.get("/v1/auth/whoami").json()
        assert me["authenticated"] is True

    # User + identity rows landed.
    eng = sqlalchemy.create_engine(sync_url)
    try:
        with eng.connect() as conn:
            user = conn.execute(
                sqlalchemy.text(
                    "SELECT email, email_verified, password_hash FROM users"
                )
            ).one()
            assert user.email == "new-google@example.com"
            assert bool(user.email_verified) is True
            assert user.password_hash is None

            identity = conn.execute(
                sqlalchemy.text(
                    "SELECT provider, provider_user_id, email FROM oauth_identities"
                )
            ).one()
            assert identity.provider == "google"
            assert identity.provider_user_id == "goog-new"
    finally:
        eng.dispose()


def test_callback_logs_in_existing_oauth_identity_without_creating_new_row(
    app_factory,
):
    stub = _RecordingGoogleOAuthClient(
        info=GoogleUserInfo(
            provider_user_id="goog-existing",
            email="existing@example.com",
            email_verified=True,
        )
    )
    app, sync_url, _, _ = app_factory(oauth_client=stub)
    user_id = _insert_user(sync_url, email="existing@example.com", email_verified=True)
    _insert_oauth_identity(
        sync_url,
        user_id=user_id,
        provider_user_id="goog-existing",
        email="existing@example.com",
    )

    assert _row_count(sync_url, "oauth_identities") == 1

    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/oauth/google/start")
        state = stub.auth_url_calls[0]["state"]
        r = client.get(
            "/v1/auth/oauth/google/callback",
            params={"code": "auth-code", "state": state},
        )
        assert r.status_code == 302
        me = client.get("/v1/auth/whoami").json()
        assert me["user_id"] == user_id

    # No additional identity row inserted.
    assert _row_count(sync_url, "oauth_identities") == 1


def test_callback_auto_links_when_google_verified(app_factory):
    """Existing password user; Google reports email_verified=True →
    attach the identity and flip user.email_verified if it wasn't."""
    stub = _RecordingGoogleOAuthClient(
        info=GoogleUserInfo(
            provider_user_id="goog-link",
            email="link-me@example.com",
            email_verified=True,
        )
    )
    app, sync_url, _, _ = app_factory(oauth_client=stub)
    user_id = _insert_user(sync_url, email="link-me@example.com", email_verified=False)

    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/oauth/google/start")
        state = stub.auth_url_calls[0]["state"]
        r = client.get(
            "/v1/auth/oauth/google/callback",
            params={"code": "auth-code", "state": state},
        )
    assert r.status_code == 302

    eng = sqlalchemy.create_engine(sync_url)
    try:
        with eng.connect() as conn:
            verified = conn.execute(
                sqlalchemy.text(
                    "SELECT email_verified FROM users WHERE id = :id"
                ),
                {"id": user_id},
            ).scalar_one()
            assert bool(verified) is True

            link = conn.execute(
                sqlalchemy.text(
                    "SELECT user_id FROM oauth_identities WHERE provider_user_id = 'goog-link'"
                )
            ).scalar_one()
            assert link == user_id
    finally:
        eng.dispose()


def test_callback_rejects_link_when_google_not_verified(app_factory):
    """Existing password user; Google's email_verified=False → reject
    with the specific 'use password first' detail (the only non-generic
    auth error in the codebase, deliberately)."""
    stub = _RecordingGoogleOAuthClient(
        info=GoogleUserInfo(
            provider_user_id="goog-unverified",
            email="risky@example.com",
            email_verified=False,
        )
    )
    app, sync_url, _, _ = app_factory(oauth_client=stub)
    _insert_user(sync_url, email="risky@example.com", email_verified=False)

    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/oauth/google/start")
        state = stub.auth_url_calls[0]["state"]
        r = client.get(
            "/v1/auth/oauth/google/callback",
            params={"code": "auth-code", "state": state},
        )

    assert r.status_code == 400
    assert "password first" in r.json()["detail"]
    # No identity row created.
    assert _row_count(sync_url, "oauth_identities") == 0


# --- Auth flow plumbing -------------------------------------------------


def test_callback_is_csrf_exempt(app_factory):
    """Callback runs as a top-level browser navigation from Google — it
    can't carry our X-CSRF-Token header. Even with an existing session
    cookie and no CSRF cookie/header, the endpoint must reach the
    handler (and fail on validation, not on CSRF)."""
    stub = _RecordingGoogleOAuthClient()
    app, _, auth, _ = app_factory(oauth_client=stub)

    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/csrf")  # plants both cookies
        client.cookies.delete(auth.csrf_cookie)

        r = client.get(
            "/v1/auth/oauth/google/callback",
            params={"code": "x", "state": "y"},
        )
        # Anything but 403 means CSRF didn't fire. We expect a 400 from
        # the handler's own validation.
        assert r.status_code != 403


def test_start_reuses_existing_session_row(app_factory):
    """If the caller already has a session (anonymous or otherwise),
    /start writes the new state onto THAT row instead of creating a
    second session — proves the session-reuse path."""
    stub = _RecordingGoogleOAuthClient()
    app, sync_url, _, _ = app_factory(oauth_client=stub)

    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/csrf")  # anon session
        before = _row_count(sync_url, "sessions")
        client.get("/v1/auth/oauth/google/start")
        after = _row_count(sync_url, "sessions")
    assert after == before  # same row reused
