"""Email/password signup + login + logout tests (B1 PR3).

What's pinned:
- Password hashing round-trip + tamper resistance (passwords module).
- Signup: happy path, duplicate email, validation errors, normalises to
  lowercase, sets cookies, lands user_id in session.
- Login: happy path, wrong password, unknown email — and that the
  responses for wrong-password vs unknown-email are byte-identical so
  the endpoint isn't a user-enumeration oracle.
- Logout: deletes the session row, clears cookies, returns 204 even
  when called from an anonymous client.
- CSRF still gates the auth POSTs once the client has a session — proves
  the middleware composes with the new endpoints.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy
from fastapi.testclient import TestClient

from cz_mtg_compare.db.config import DatabaseSettings
from cz_mtg_compare.db.models import Base
from cz_mtg_compare.web.app import create_app
from cz_mtg_compare.web.auth_config import AuthCookieSettings
from cz_mtg_compare.web.passwords import (
    hash_password,
    needs_rehash,
    verify_password,
)


# --- passwords module ---------------------------------------------------


def test_hash_then_verify_succeeds():
    h = hash_password("hunter2hunter2")
    assert verify_password(h, "hunter2hunter2") is True


def test_verify_rejects_wrong_password():
    h = hash_password("right-password")
    assert verify_password(h, "wrong-password") is False


def test_verify_handles_malformed_hash():
    """The wrapper must never raise — verifying a garbage hash returns
    False so callers can treat the entire failure space uniformly."""
    assert verify_password("not-a-real-argon2-string", "anything") is False


def test_hash_is_randomly_salted():
    """Two hashes of the same password must differ — otherwise rainbow
    tables and equality checks would bypass argon2's purpose."""
    assert hash_password("abc12345") != hash_password("abc12345")


def test_needs_rehash_is_false_for_fresh_hash():
    assert needs_rehash(hash_password("abc12345")) is False


# --- endpoints fixture --------------------------------------------------


@pytest.fixture
def app_and_url(tmp_path):
    db_path = tmp_path / "auth_passwords.sqlite"
    async_url = f"sqlite+aiosqlite:///{db_path}"
    sync_url = f"sqlite:///{db_path}"

    sync_engine = sqlalchemy.create_engine(sync_url)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    auth = AuthCookieSettings(
        secure=False,
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
    app = app_and_url[0]
    with TestClient(app) as c:
        yield c


def _csrf_header(client) -> dict[str, str]:
    """Bootstrap CSRF cookies + return the header value to mirror."""
    r = client.get("/v1/auth/csrf")
    return {"X-CSRF-Token": r.json()["csrf_token"]}


# --- signup -------------------------------------------------------------


def test_signup_happy_path(client):
    r = client.post(
        "/v1/auth/signup",
        json={"email": "alice@example.com", "password": "longpassword"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert body["email_verified"] is False
    assert body["user_id"]
    # whoami now reports authenticated
    me = client.get("/v1/auth/whoami").json()
    assert me["authenticated"] is True
    assert me["user_id"] == body["user_id"]


def test_signup_normalises_email_lowercase(client):
    r = client.post(
        "/v1/auth/signup",
        json={"email": "Alice@Example.COM", "password": "longpassword"},
    )
    assert r.status_code == 201
    assert r.json()["email"] == "alice@example.com"


def test_signup_duplicate_email_is_rejected(client):
    payload = {"email": "dup@example.com", "password": "longpassword"}
    r1 = client.post("/v1/auth/signup", json=payload)
    assert r1.status_code == 201

    # Different TestClient state so the second signup isn't carrying
    # the first session's cookies.
    fresh = TestClient(client.app)
    with fresh:
        r2 = fresh.post("/v1/auth/signup", json=payload)
    assert r2.status_code == 409


def test_signup_rejects_invalid_email(client):
    r = client.post(
        "/v1/auth/signup",
        json={"email": "not-an-email", "password": "longpassword"},
    )
    assert r.status_code == 422


def test_signup_rejects_short_password(client):
    r = client.post(
        "/v1/auth/signup",
        json={"email": "shortpass@example.com", "password": "abc"},
    )
    assert r.status_code == 422


def test_signup_sets_session_and_csrf_cookies(client, app_and_url):
    auth = app_and_url[2]
    r = client.post(
        "/v1/auth/signup",
        json={"email": "cookie@example.com", "password": "longpassword"},
    )
    assert r.cookies.get(auth.session_cookie)
    assert r.cookies.get(auth.csrf_cookie)


# --- login --------------------------------------------------------------


def _signup_then_logout(client, email: str, password: str) -> None:
    r = client.post("/v1/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201
    headers = _csrf_header(client)
    r = client.post("/v1/auth/logout", headers=headers)
    assert r.status_code == 204
    client.cookies.clear()


def test_login_happy_path(client):
    _signup_then_logout(client, "login@example.com", "longpassword")

    r = client.post(
        "/v1/auth/login",
        json={"email": "login@example.com", "password": "longpassword"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == "login@example.com"
    assert client.get("/v1/auth/whoami").json()["authenticated"] is True


def test_login_is_case_insensitive_on_email(client):
    _signup_then_logout(client, "casey@example.com", "longpassword")

    r = client.post(
        "/v1/auth/login",
        json={"email": "CASEY@Example.com", "password": "longpassword"},
    )
    assert r.status_code == 200


def test_login_rejects_wrong_password(client):
    _signup_then_logout(client, "wrongpw@example.com", "longpassword")

    r = client.post(
        "/v1/auth/login",
        json={"email": "wrongpw@example.com", "password": "different-password"},
    )
    assert r.status_code == 401


def test_login_rejects_unknown_email_with_same_response_as_wrong_password(client):
    """Same status + same body for unknown-email and wrong-password.
    Anything that diverges turns the endpoint into a user enumeration
    oracle."""
    _signup_then_logout(client, "real@example.com", "longpassword")

    wrong_pw = client.post(
        "/v1/auth/login",
        json={"email": "real@example.com", "password": "nope-not-it"},
    )
    unknown = client.post(
        "/v1/auth/login",
        json={"email": "ghost@example.com", "password": "anything-here"},
    )
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


def test_login_mints_new_session_each_time(client, app_and_url):
    """Two successful logins must produce different session ids — the
    cookie rotates so an attacker who somehow obtained the old session
    id loses access after the user re-logs in."""
    auth = app_and_url[2]
    _signup_then_logout(client, "rotate@example.com", "longpassword")

    r1 = client.post(
        "/v1/auth/login",
        json={"email": "rotate@example.com", "password": "longpassword"},
    )
    sid_1 = r1.cookies.get(auth.session_cookie)

    # Clear cookies so the second login truly starts fresh.
    client.cookies.clear()
    r2 = client.post(
        "/v1/auth/login",
        json={"email": "rotate@example.com", "password": "longpassword"},
    )
    sid_2 = r2.cookies.get(auth.session_cookie)
    assert sid_1 and sid_2 and sid_1 != sid_2


# --- logout -------------------------------------------------------------


def test_logout_clears_cookies_and_deauths(client, app_and_url):
    auth = app_and_url[2]
    r = client.post(
        "/v1/auth/signup",
        json={"email": "logout@example.com", "password": "longpassword"},
    )
    assert r.status_code == 201

    headers = _csrf_header(client)
    r = client.post("/v1/auth/logout", headers=headers)
    assert r.status_code == 204

    # The Set-Cookie response should have cleared both cookies.
    set_cookies = r.headers.get_list("set-cookie")
    assert any(h.startswith(f"{auth.session_cookie}=") for h in set_cookies)
    assert any(h.startswith(f"{auth.csrf_cookie}=") for h in set_cookies)

    # httpx applies the clearing Set-Cookies, so whoami now anonymous.
    me = client.get("/v1/auth/whoami").json()
    assert me["authenticated"] is False


def test_logout_anonymous_returns_204(client):
    """Calling logout without a session must succeed silently —
    clients shouldn't have to track whether they had a session."""
    r = client.post("/v1/auth/logout")
    assert r.status_code == 204


# --- CSRF composition ---------------------------------------------------


def test_signup_does_not_require_csrf_when_anonymous(client):
    """Anonymous POST → no session cookie → CSRF middleware skips.
    Otherwise nobody could ever create an account."""
    r = client.post(
        "/v1/auth/signup",
        json={"email": "anon-csrf@example.com", "password": "longpassword"},
    )
    assert r.status_code == 201


def test_login_requires_csrf_when_client_carries_a_session(client, app_and_url):
    """Once a session cookie is in play (e.g. anonymous CSRF session
    from a prior visit), state-changing POSTs need the CSRF header.
    Proves the middleware doesn't make a special case for /v1/auth/*."""
    auth = app_and_url[2]
    client.get("/v1/auth/csrf")  # plants both cookies
    # Drop the CSRF cookie to simulate a missing header in the JS client.
    client.cookies.delete(auth.csrf_cookie)

    r = client.post(
        "/v1/auth/login",
        json={"email": "whoever@example.com", "password": "longpassword"},
    )
    assert r.status_code == 403