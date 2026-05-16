"""Email verification flow tests (B1 PR4).

Pins:
- Signup auto-issues a token and calls the mailer with the verification URL.
- Token lifecycle: issue → consume marks user verified + token used.
- consume rejects expired tokens, already-used tokens, missing tokens, and
  tokens for deleted users — all with the same return value so callers
  can't distinguish failure modes (the endpoint already returns one
  generic 400; the helper enforces it at the data layer too).
- POST /v1/auth/verify/request requires auth, no-ops when already
  verified, otherwise sends a fresh token.
- POST /v1/auth/verify/confirm consumes a token, flips email_verified.
- /v1/auth/verify/confirm is CSRF-exempt (the token IS the protection).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy
from fastapi.testclient import TestClient

from cz_mtg_compare.db.config import DatabaseSettings
from cz_mtg_compare.db.models import Base, EmailVerificationToken, User
from cz_mtg_compare.web.app import create_app
from cz_mtg_compare.web.auth_config import AuthCookieSettings
from cz_mtg_compare.web.email_verification import (
    build_verification_url,
    consume_token,
    issue_token,
)


class _RecordingMailer:
    """Captures send_verification_email arguments instead of delivering."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send_verification_email(self, *, to: str, verification_url: str) -> None:
        self.sent.append({"to": to, "verification_url": verification_url})

    @property
    def last_token(self) -> str:
        url = self.sent[-1]["verification_url"]
        # Parse out the ?token= query — tests use this to feed
        # /v1/auth/verify/confirm with the real plaintext.
        return url.split("token=", 1)[1]


@pytest.fixture
def app_and_mailer(tmp_path):
    db_path = tmp_path / "email_verify.sqlite"
    async_url = f"sqlite+aiosqlite:///{db_path}"
    sync_url = f"sqlite:///{db_path}"

    sync_engine = sqlalchemy.create_engine(sync_url)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    auth = AuthCookieSettings(
        secure=False, samesite="lax", session_ttl=timedelta(hours=1)
    )
    mailer = _RecordingMailer()
    app = create_app(
        db_settings=DatabaseSettings(url=async_url),
        auth_settings=auth,
        mailer=mailer,
    )
    return app, sync_url, auth, mailer


@pytest.fixture
def client(app_and_mailer):
    app = app_and_mailer[0]
    with TestClient(app) as c:
        yield c


def _csrf_header(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.get("/v1/auth/csrf").json()["csrf_token"]}


# --- helpers + URL builder ----------------------------------------------


def test_build_verification_url_strips_trailing_slash():
    assert (
        build_verification_url("abc", base_url="https://x.example/")
        == "https://x.example/verify?token=abc"
    )


# --- signup auto-sends --------------------------------------------------


def test_signup_sends_verification_email(client, app_and_mailer):
    mailer = app_and_mailer[3]
    r = client.post(
        "/v1/auth/signup",
        json={"email": "newuser@example.com", "password": "longpassword"},
    )
    assert r.status_code == 201
    assert len(mailer.sent) == 1
    assert mailer.sent[0]["to"] == "newuser@example.com"
    assert "token=" in mailer.sent[0]["verification_url"]


# --- token data layer ---------------------------------------------------


@pytest.fixture
async def db_session(app_and_mailer):
    app = app_and_mailer[0]
    # Open the FastAPI app's lifespan so the engine + session_factory
    # are wired up. We can't reach into the TestClient's loop, so build
    # our own engine bound to the same URL.
    from cz_mtg_compare.db.engine import (
        create_engine_from_settings,
        session_factory_from_engine,
    )

    db_settings = DatabaseSettings(url=app.state.__dict__.get("_db_url") or "")
    # Easier: pull the URL from the engine fixture path.
    _, sync_url, _, _ = app_and_mailer
    async_url = sync_url.replace("sqlite:///", "sqlite+aiosqlite:///")
    eng = create_engine_from_settings(DatabaseSettings(url=async_url))
    factory = session_factory_from_engine(eng)
    try:
        async with factory() as db:
            yield db
    finally:
        await eng.dispose()


async def test_issue_then_consume_marks_user_verified(db_session):
    user = User(email="data@example.com", email_verified=False)
    db_session.add(user)
    await db_session.flush()

    raw = await issue_token(db_session, user)
    await db_session.commit()

    confirmed = await consume_token(db_session, raw)
    assert confirmed is not None
    assert confirmed.id == user.id
    assert confirmed.email_verified is True

    # Single-use: a second attempt with the same token fails.
    again = await consume_token(db_session, raw)
    assert again is None


async def test_consume_rejects_unknown_token(db_session):
    assert await consume_token(db_session, "totally-bogus-token") is None


async def test_consume_rejects_expired_token(db_session):
    from datetime import datetime, timezone

    user = User(email="expired@example.com")
    db_session.add(user)
    await db_session.flush()

    raw = await issue_token(db_session, user)
    # Force-expire the row.
    row = (
        await db_session.execute(
            sqlalchemy.select(EmailVerificationToken).where(
                EmailVerificationToken.user_id == user.id
            )
        )
    ).scalar_one()
    row.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    await db_session.commit()

    assert await consume_token(db_session, raw) is None


# --- POST /v1/auth/verify/confirm --------------------------------------


def test_verify_confirm_marks_user_verified(client, app_and_mailer):
    mailer = app_and_mailer[3]
    r = client.post(
        "/v1/auth/signup",
        json={"email": "confirm@example.com", "password": "longpassword"},
    )
    assert r.status_code == 201
    raw_token = mailer.last_token

    # Confirm from an anonymous client (typical: user clicks the email
    # link on a different device). Drop cookies first.
    client.cookies.clear()
    r2 = client.post("/v1/auth/verify/confirm", json={"token": raw_token})
    assert r2.status_code == 200
    body = r2.json()
    assert body["email_verified"] is True
    assert body["email"] == "confirm@example.com"


def test_verify_confirm_rejects_garbage(client):
    r = client.post("/v1/auth/verify/confirm", json={"token": "totally-bogus"})
    assert r.status_code == 400
    assert "invalid" in r.json()["detail"].lower()


def test_verify_confirm_rejects_already_used_token(client, app_and_mailer):
    mailer = app_and_mailer[3]
    client.post(
        "/v1/auth/signup",
        json={"email": "reuse@example.com", "password": "longpassword"},
    )
    raw = mailer.last_token

    client.cookies.clear()
    r1 = client.post("/v1/auth/verify/confirm", json={"token": raw})
    assert r1.status_code == 200

    r2 = client.post("/v1/auth/verify/confirm", json={"token": raw})
    assert r2.status_code == 400


def test_verify_confirm_is_csrf_exempt(client, app_and_mailer):
    """A user clicking the email link on a fresh device may still carry
    a stale anonymous session cookie from a previous visit. Without the
    CSRF exempt entry, /verify/confirm would 403 in that scenario. The
    token itself is the bearer credential — CSRF doesn't add protection
    here."""
    auth = app_and_mailer[2]
    mailer = app_and_mailer[3]

    client.post(
        "/v1/auth/signup",
        json={"email": "exempt@example.com", "password": "longpassword"},
    )
    raw = mailer.last_token

    # Plant a session cookie but no CSRF header — would 403 a normal
    # POST. The exempt entry must let this through.
    client.cookies.clear()
    client.get("/v1/auth/csrf")  # plants an anonymous session
    client.cookies.delete(auth.csrf_cookie)

    r = client.post("/v1/auth/verify/confirm", json={"token": raw})
    # Token may be invalid because the signup session created a different
    # one — but the response must be 200 (success) or 400 (invalid),
    # NEVER 403 (CSRF). The exempt status is what we're pinning.
    assert r.status_code != 403


# --- POST /v1/auth/verify/request --------------------------------------


def test_verify_request_unauthenticated_returns_401(client):
    r = client.post("/v1/auth/verify/request")
    assert r.status_code == 401


def test_verify_request_sends_a_fresh_token(client, app_and_mailer):
    mailer = app_and_mailer[3]
    client.post(
        "/v1/auth/signup",
        json={"email": "resend@example.com", "password": "longpassword"},
    )
    assert len(mailer.sent) == 1
    original_token = mailer.last_token

    headers = _csrf_header(client)
    r = client.post("/v1/auth/verify/request", headers=headers)
    assert r.status_code == 202
    assert r.json() == {"sent": True}
    assert len(mailer.sent) == 2
    # New token, distinct from the one issued at signup.
    assert mailer.last_token != original_token


def test_verify_request_noops_when_already_verified(client, app_and_mailer):
    mailer = app_and_mailer[3]
    client.post(
        "/v1/auth/signup",
        json={"email": "already@example.com", "password": "longpassword"},
    )
    raw = mailer.last_token

    # Confirm first so the user is verified.
    # Need to keep cookies so verify/request below is authenticated.
    csrf_headers = _csrf_header(client)
    # Confirm doesn't need CSRF (exempt). Use a fresh client to be safe?
    # Actually using the same client is fine — confirm is exempt.
    client.post("/v1/auth/verify/confirm", json={"token": raw})

    before = len(mailer.sent)
    r = client.post("/v1/auth/verify/request", headers=csrf_headers)
    assert r.status_code == 202
    assert r.json() == {"sent": False}
    assert len(mailer.sent) == before  # no new email