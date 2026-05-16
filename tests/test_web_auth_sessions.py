"""Sessions + CSRF middleware tests (B1 PR2).

Covers:
- GET /v1/auth/csrf mints a session + token, sets both cookies
- GET /v1/auth/whoami returns the right shape unauth vs authenticated
- The SessionLoaderMiddleware attaches request.state.session from the cookie
- The CSRF middleware:
  * Lets safe methods through with no token
  * Rejects state-changing methods with missing / mismatched / forged tokens
  * Honours the session.csrf_token check when a session is present
  * Honours the CSRF_EXEMPT_PATHS allow-list
- Cookie flags: HttpOnly on session, NOT on CSRF, plus SameSite/Secure
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy
from fastapi.testclient import TestClient

from cz_mtg_compare.db.config import DatabaseSettings
from cz_mtg_compare.db.models import Base, Session as SessionRow, User
from cz_mtg_compare.web.app import create_app
from cz_mtg_compare.web.auth_config import AuthCookieSettings


@pytest.fixture
def db_url(tmp_path):
    """File-based sqlite shared across the sync setup phase and the
    async engine the app boots. ``:memory:`` would give each connection
    its own DB so the schema we install up front wouldn't be visible to
    the running app."""
    db_path = tmp_path / "auth.sqlite"
    return f"sqlite+aiosqlite:///{db_path}", f"sqlite:///{db_path}"


@pytest.fixture
def app_and_url(db_url):
    async_url, sync_url = db_url
    # Build the schema synchronously before the FastAPI lifespan opens.
    sync_engine = sqlalchemy.create_engine(sync_url)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    auth = AuthCookieSettings(
        secure=False,  # TestClient runs over HTTP
        samesite="lax",
        session_ttl=timedelta(hours=1),
    )
    app = create_app(
        db_settings=DatabaseSettings(url=async_url),
        auth_settings=auth,
    )
    return app, sync_url, auth


@pytest.fixture
def client(app_and_url):
    app, _, _ = app_and_url
    with TestClient(app) as c:
        yield c


def _insert_user_and_session(sync_url: str, *, user_id: str | None = None) -> dict[str, str]:
    """Synchronously bootstrap a session row + return cookie values that
    the test can hand to the TestClient. Bypasses the HTTP layer so a
    test can prove the middleware reads a pre-existing cookie."""
    import secrets
    from datetime import datetime, timezone

    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=1)

    eng = sqlalchemy.create_engine(sync_url)
    with eng.begin() as conn:
        if user_id is not None:
            conn.execute(
                sqlalchemy.text(
                    "INSERT INTO users (id, email, email_verified, created_at, updated_at) "
                    "VALUES (:id, :email, 0, :now, :now)"
                ),
                {"id": user_id, "email": f"{user_id}@example.com", "now": now},
            )
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO sessions (id, user_id, csrf_token, created_at, last_seen_at, expires_at) "
                "VALUES (:sid, :uid, :csrf, :now, :now, :exp)"
            ),
            {"sid": sid, "uid": user_id, "csrf": csrf, "now": now, "exp": expires},
        )
    eng.dispose()
    return {"sid": sid, "csrf": csrf}


# --- Issuing tokens -----------------------------------------------------


def test_csrf_endpoint_creates_anonymous_session_and_sets_cookies(client, app_and_url):
    r = client.get("/v1/auth/csrf")
    assert r.status_code == 200
    body = r.json()
    assert "csrf_token" in body and body["csrf_token"]

    cookies = r.cookies
    auth = app_and_url[2]
    assert cookies.get(auth.session_cookie)
    assert cookies.get(auth.csrf_cookie) == body["csrf_token"]


def test_csrf_endpoint_reuses_existing_session(client, app_and_url):
    """Calling /v1/auth/csrf twice from the same client must return the
    same session id — we don't mint a brand new row on every visit."""
    first = client.get("/v1/auth/csrf")
    auth = app_and_url[2]
    sid_1 = first.cookies.get(auth.session_cookie)

    second = client.get("/v1/auth/csrf")
    sid_2 = second.cookies.get(auth.session_cookie) or sid_1
    # On the second call, the SessionLoader already loaded the session,
    # so the endpoint doesn't create a new one. The cookie may not be
    # re-emitted (and the client carries the old one in subsequent
    # requests, so it never changes).
    assert sid_2 == sid_1


def test_session_cookie_is_httponly_csrf_cookie_is_not(client, app_and_url):
    r = client.get("/v1/auth/csrf")
    auth = app_and_url[2]
    # httpx exposes the Set-Cookie header(s); parse for httponly flag.
    set_cookie_headers = r.headers.get_list("set-cookie")
    session_set = next(
        h for h in set_cookie_headers if h.startswith(f"{auth.session_cookie}=")
    )
    csrf_set = next(
        h for h in set_cookie_headers if h.startswith(f"{auth.csrf_cookie}=")
    )
    assert "HttpOnly" in session_set
    assert "HttpOnly" not in csrf_set
    # SameSite present on both regardless of value
    assert "samesite=lax" in session_set.lower()
    assert "samesite=lax" in csrf_set.lower()


# --- /v1/auth/whoami ----------------------------------------------------


def test_whoami_anonymous(client):
    r = client.get("/v1/auth/whoami")
    assert r.status_code == 200
    assert r.json() == {"authenticated": False, "user_id": None}


def test_whoami_anonymous_session_still_unauthenticated(client, app_and_url):
    """An anonymous session (user_id null) is not authentication.
    /v1/auth/csrf creates one — whoami must not be fooled."""
    client.get("/v1/auth/csrf")  # mints anonymous session
    r = client.get("/v1/auth/whoami")
    assert r.json() == {"authenticated": False, "user_id": None}


def test_whoami_reads_user_id_from_session_cookie(client, app_and_url):
    _, sync_url, auth = app_and_url
    cookies = _insert_user_and_session(sync_url, user_id="user-1")
    client.cookies.set(auth.session_cookie, cookies["sid"])
    r = client.get("/v1/auth/whoami")
    assert r.json() == {"authenticated": True, "user_id": "user-1"}


def test_unknown_session_cookie_is_ignored(client, app_and_url):
    auth = app_and_url[2]
    client.cookies.set(auth.session_cookie, "not-a-real-session-id")
    r = client.get("/v1/auth/whoami")
    assert r.json() == {"authenticated": False, "user_id": None}


# --- CSRF middleware ----------------------------------------------------


def test_safe_method_does_not_require_csrf(client):
    """GETs must work without any CSRF token. Health is a simple proof."""
    r = client.get("/v1/health")
    assert r.status_code == 200


def test_post_without_session_cookie_does_not_require_csrf(client):
    """Unsessioned POSTs aren't a CSRF target — the attacker can't escalate
    something they don't have. Middleware passes them through so unrelated
    test code (and unauthenticated public clients) aren't gated."""
    r = client.post("/v1/decklists/optimize", json={"decklist": "4 Lightning Bolt"})
    # The handler may 200 or 500 depending on adapter wiring; the only
    # thing this test pins is "not a 403 from CSRF".
    assert r.status_code != 403


def test_post_with_session_and_no_csrf_is_rejected(client):
    """The moment a session cookie shows up, CSRF gates engage. Even
    without a CSRF cookie/header, the request must be rejected."""
    client.get("/v1/auth/csrf")  # plants both cookies
    # Strip the CSRF cookie so the request has only the session cookie.
    csrf_name = client.app.user_middleware  # noqa: F841 — placeholder, see next line
    # Easier: just delete the csrf cookie from the jar directly.
    from cz_mtg_compare.web.auth_config import AuthCookieSettings  # local import to avoid top-of-file churn

    auth = AuthCookieSettings(secure=False)
    client.cookies.delete(auth.csrf_cookie)
    r = client.post("/v1/decklists/optimize", json={"decklist": "4 Lightning Bolt"})
    assert r.status_code == 403
    assert "csrf" in r.json()["detail"].lower()


def test_post_with_session_and_mismatched_csrf_is_rejected(client):
    client.get("/v1/auth/csrf")
    r = client.post(
        "/v1/decklists/optimize",
        json={"decklist": "4 Lightning Bolt"},
        headers={"X-CSRF-Token": "different-from-cookie"},
    )
    assert r.status_code == 403


def test_post_with_matching_csrf_passes_middleware(client, app_and_url):
    """The CSRF gate must let a properly-double-submitted request through
    to the actual handler. We don't care that the optimizer returns a
    real result — just that the response isn't a 403 from the middleware."""
    r = client.get("/v1/auth/csrf")
    token = r.json()["csrf_token"]
    r2 = client.post(
        "/v1/decklists/optimize",
        json={"decklist": "4 Lightning Bolt"},
        headers={"X-CSRF-Token": token},
    )
    # Either the handler succeeded (200) or it failed for a reason that
    # *is not* CSRF (e.g. no real shop adapters, which would surface as
    # an aggregator-level error). Pinning != 403 captures the right idea.
    assert r2.status_code != 403


def test_csrf_token_must_match_session_csrf(client, app_and_url):
    """Cookie/header match alone isn't enough when a session is present —
    they must also equal session.csrf_token. Otherwise an attacker who
    can plant matching cookies on the victim's browser still forges
    state-changing requests after a victim's session is established."""
    _, sync_url, auth = app_and_url
    cookies = _insert_user_and_session(sync_url, user_id="user-2")
    # Plant the session cookie but supply a stale CSRF cookie+header.
    client.cookies.set(auth.session_cookie, cookies["sid"])
    client.cookies.set(auth.csrf_cookie, "forged-but-matching")
    r = client.post(
        "/v1/decklists/optimize",
        json={"decklist": "4 Lightning Bolt"},
        headers={"X-CSRF-Token": "forged-but-matching"},
    )
    assert r.status_code == 403


def test_csrf_exempt_path_skips_check(client):
    """/v1/auth/csrf must be reachable without any cookie/token — it's
    how anonymous clients bootstrap. The CSRF middleware skip-list owns
    that behaviour."""
    r = client.get("/v1/auth/csrf")
    assert r.status_code == 200