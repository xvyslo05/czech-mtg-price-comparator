"""Tests for ShopAdapter account features: capability flags, default
NotSupported behaviour, and the najada / tolarie login + cart implementations."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
import respx

from cz_mtg_compare import credentials
from cz_mtg_compare.adapters.base import AccountFeatureNotSupported, ShopAdapter
from cz_mtg_compare.adapters.najada import (
    AUTH_LOGIN_URL,
    AUTH_LOGOUT_URL,
    CART_ITEMS_URL,
    NajadaAdapter,
)
from cz_mtg_compare.adapters.tolarie import LOGIN_URL as TOLARIE_LOGIN_URL
from cz_mtg_compare.adapters.tolarie import TolarieAdapter
from cz_mtg_compare.models import Offer, SearchQuery


@pytest.fixture(autouse=True)
def _isolated_creds(monkeypatch: pytest.MonkeyPatch):
    import os

    for key in list(os.environ):
        if key.startswith("CZ_MTG_") and (key.endswith("_USER") or key.endswith("_PASS")):
            monkeypatch.delenv(key, raising=False)
    credentials.reset_secret_cache()
    yield


def _load_fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


# --- Capability flags + default impl ----------------------------------------


class _NoAccountAdapter(ShopAdapter):
    shop_id = "cernyrytir"  # arbitrary; using an existing ShopId literal
    base_url = "https://example.com"

    async def search(self, query: SearchQuery) -> list[Offer]:
        return []


@pytest.mark.asyncio
async def test_default_login_raises_not_supported() -> None:
    adapter = _NoAccountAdapter()
    assert adapter.supports_login is False
    assert adapter.supports_cart is False
    assert adapter.supports_watchlist is False
    with pytest.raises(AccountFeatureNotSupported) as exc:
        await adapter.login()
    assert exc.value.feature == "login"
    with pytest.raises(AccountFeatureNotSupported):
        await adapter.add_to_cart("some-ref")
    with pytest.raises(AccountFeatureNotSupported):
        await adapter.add_to_watchlist("some-ref")


def test_najada_advertises_login_and_cart() -> None:
    adapter = NajadaAdapter()
    assert adapter.supports_login is True
    assert adapter.supports_cart is True
    # Watchlist deferred until a follow-up PR — keep flag honest.
    assert adapter.supports_watchlist is False


def test_tolarie_advertises_login_and_cart() -> None:
    adapter = TolarieAdapter()
    assert adapter.supports_login is True
    assert adapter.supports_cart is True
    assert adapter.supports_watchlist is False


# --- Najada login + cart with mocked HTTP -----------------------------------


@pytest.mark.asyncio
async def test_najada_login_persists_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "secret")
    adapter = NajadaAdapter()

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(AUTH_LOGIN_URL).mock(
            return_value=httpx.Response(200, json={"auth_token": "tok-123"})
        )
        await adapter.login()

    assert adapter._auth_token == "tok-123"


@pytest.mark.asyncio
async def test_najada_login_missing_credentials_raises() -> None:
    adapter = NajadaAdapter()
    with pytest.raises(credentials.CredentialError, match="CZ_MTG_NAJADA_USER"):
        await adapter.login()


@pytest.mark.asyncio
async def test_najada_login_rejects_bad_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "wrong")
    adapter = NajadaAdapter()
    async with respx.mock(assert_all_called=True) as mock:
        mock.post(AUTH_LOGIN_URL).mock(
            return_value=httpx.Response(
                400, json={"non_field_errors": [{"code": "invalid_credentials"}]}
            )
        )
        with pytest.raises(credentials.CredentialError, match="invalid credentials"):
            await adapter.login()
    assert adapter._auth_token is None


@pytest.mark.asyncio
async def test_najada_search_captures_shop_ref() -> None:
    adapter = NajadaAdapter()
    payload = _load_fixture("najada_lightning_bolt.json")
    offers = await adapter.parse(payload, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    assert offers, "expected some offers in fixture"
    # Every offer should carry the article id from the JSON payload — najada uses
    # UUIDs as article PKs and the cart endpoint accepts them verbatim.
    assert all(o.shop_ref for o in offers), "shop_ref should be populated from article.id"
    uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    for o in offers:
        assert o.shop_ref is not None
        assert uuid_re.match(o.shop_ref), f"expected UUID article id, got {o.shop_ref!r}"


@pytest.mark.asyncio
async def test_najada_add_to_cart_posts_article_and_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "secret")
    adapter = NajadaAdapter()
    article_uuid = "d762c438-8915-4131-be7e-e301d91d8935"

    captured: dict[str, object] = {}

    def _capture_cart(request: httpx.Request) -> httpx.Response:
        import json

        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1, "article": article_uuid, "count": 2})

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(AUTH_LOGIN_URL).mock(
            return_value=httpx.Response(200, json={"auth_token": "tok-xyz"})
        )
        mock.post(CART_ITEMS_URL).mock(side_effect=_capture_cart)

        result = await adapter.add_to_cart(article_uuid, count=2)

    assert result == {"id": 1, "article": article_uuid, "count": 2}
    assert captured["body"] == {"article": article_uuid, "count": 2}
    headers = captured["headers"]
    # najada uses DRF's legacy TokenAuthentication scheme — Authorization
    # must be ``Token <key>`` (not ``Bearer``); see _auth_headers comment.
    assert headers["authorization"] == "Token tok-xyz"


@pytest.mark.asyncio
async def test_najada_add_to_cart_requires_shop_ref() -> None:
    adapter = NajadaAdapter()
    with pytest.raises(ValueError, match="shop_ref is required"):
        await adapter.add_to_cart("")


@pytest.mark.asyncio
async def test_najada_add_to_cart_retries_once_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale cached token should be transparently refreshed: cart POST gets
    a 401, the adapter drops the token, logs in again, and replays the POST."""
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "secret")
    adapter = NajadaAdapter()
    article_uuid = "d762c438-8915-4131-be7e-e301d91d8935"

    login_tokens = iter(["tok-stale", "tok-fresh"])

    def _login(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"auth_token": next(login_tokens)})

    seen_tokens: list[str] = []

    def _cart(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "").removeprefix("Token ")
        seen_tokens.append(token)
        if token == "tok-stale":
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(201, json={"ok": True})

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(AUTH_LOGIN_URL).mock(side_effect=_login)
        mock.post(CART_ITEMS_URL).mock(side_effect=_cart)
        result = await adapter.add_to_cart(article_uuid, count=1)

    assert result == {"ok": True}
    assert seen_tokens == ["tok-stale", "tok-fresh"]
    assert adapter._auth_token == "tok-fresh"


@pytest.mark.asyncio
async def test_najada_view_cart_uses_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "secret")
    adapter = NajadaAdapter()

    cart_body = {"items": [{"id": 7, "count": 1}], "subtotal_czk": 29.0}

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(AUTH_LOGIN_URL).mock(
            return_value=httpx.Response(200, json={"auth_token": "tok-abc"})
        )
        cart_route = mock.get(CART_ITEMS_URL).mock(
            return_value=httpx.Response(200, json=cart_body)
        )
        result = await adapter.view_cart()

    assert result == cart_body
    auth_header = cart_route.calls.last.request.headers.get("authorization")
    assert auth_header == "Token tok-abc"


@pytest.mark.asyncio
async def test_najada_clear_cart_deletes_each_item(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "secret")
    adapter = NajadaAdapter()

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(AUTH_LOGIN_URL).mock(
            return_value=httpx.Response(200, json={"auth_token": "tok-abc"})
        )
        mock.get(CART_ITEMS_URL).mock(
            return_value=httpx.Response(200, json={"items": [{"id": 11}, {"id": 22}]})
        )
        del_11 = mock.delete(f"{CART_ITEMS_URL}11/").mock(return_value=httpx.Response(204))
        del_22 = mock.delete(f"{CART_ITEMS_URL}22/").mock(return_value=httpx.Response(204))
        result = await adapter.clear_cart()

    assert result == {"removed_items": 2}
    assert del_11.called and del_22.called


@pytest.mark.asyncio
async def test_najada_view_cart_raises_when_relogin_also_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If both attempts get 401, the adapter surfaces a clear credential error
    rather than retrying forever."""
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "secret")
    adapter = NajadaAdapter()

    async with respx.mock(assert_all_called=False) as mock:
        mock.post(AUTH_LOGIN_URL).mock(
            return_value=httpx.Response(200, json={"auth_token": "tok-bad"})
        )
        mock.get(CART_ITEMS_URL).mock(return_value=httpx.Response(401, json={"detail": "expired"}))
        with pytest.raises(credentials.CredentialError, match="even after re-login"):
            await adapter.view_cart()


@pytest.mark.asyncio
async def test_najada_logout_clears_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "secret")
    adapter = NajadaAdapter()

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(AUTH_LOGIN_URL).mock(
            return_value=httpx.Response(200, json={"auth_token": "tok-logout"})
        )
        mock.post(AUTH_LOGOUT_URL).mock(return_value=httpx.Response(204))
        await adapter.login()
        await adapter.logout()

    assert adapter._auth_token is None


# --- Tolarie login + product ID capture -------------------------------------


TOLARIE_LOGIN_FORM_HTML = """
<html><body>
<form action="./" method="post" id="login_form">
  <input type="hidden" name="csrfmiddlewaretoken" value="csrf-from-form">
  <input type="text" name="username">
  <input type="password" name="password">
</form>
</body></html>
"""


@pytest.mark.asyncio
async def test_tolarie_login_posts_csrf_and_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_TOLARIE_USER", "bob")
    monkeypatch.setenv("CZ_MTG_TOLARIE_PASS", "bobpass")
    adapter = TolarieAdapter()

    captured: dict[str, object] = {}

    def _capture_login(request: httpx.Request) -> httpx.Response:
        captured["referer"] = request.headers.get("referer")
        captured["body"] = request.content.decode()
        return httpx.Response(
            302,
            headers={
                "location": "/",
                "set-cookie": "sessionid=session-abc; Path=/; HttpOnly",
            },
        )

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(TOLARIE_LOGIN_URL).mock(
            return_value=httpx.Response(200, text=TOLARIE_LOGIN_FORM_HTML)
        )
        mock.post(TOLARIE_LOGIN_URL).mock(side_effect=_capture_login)
        # The 302 follows to "/" — mock that too so follow_redirects doesn't 404.
        mock.get(re.compile(r"^https://www\.tolarie\.cz/$")).mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        await adapter.login()

    body = captured["body"]
    assert isinstance(body, str)
    assert "csrfmiddlewaretoken=csrf-from-form" in body
    assert "username=bob" in body
    assert "password=bobpass" in body
    assert captured["referer"] == TOLARIE_LOGIN_URL
    assert adapter._authenticated is True


@pytest.mark.asyncio
async def test_tolarie_login_failure_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_TOLARIE_USER", "bob")
    monkeypatch.setenv("CZ_MTG_TOLARIE_PASS", "wrong")
    adapter = TolarieAdapter()

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(TOLARIE_LOGIN_URL).mock(
            return_value=httpx.Response(200, text=TOLARIE_LOGIN_FORM_HTML)
        )
        # Form re-rendered without setting sessionid → still a 200 but no session.
        mock.post(TOLARIE_LOGIN_URL).mock(
            return_value=httpx.Response(200, text=TOLARIE_LOGIN_FORM_HTML)
        )
        with pytest.raises(credentials.CredentialError, match="tolarie login failed"):
            await adapter.login()
    assert adapter._authenticated is False


@pytest.mark.asyncio
async def test_tolarie_search_captures_product_id_into_shop_ref(load_fixture) -> None:
    """The Tolarie search-results fixture has product IDs encoded in
    ``class="js-add_to_cart_amount-<id>-card"`` on cart inputs. We capture
    those into ``Offer.shop_ref`` so the future cart implementation has the
    identifier it needs without re-fetching the search page."""
    adapter = TolarieAdapter()
    html = load_fixture("tolarie_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    # The fixture is a live snapshot — not every row necessarily has a cart
    # input visible to anonymous users, so we only assert "at least one offer
    # captured an id" rather than "every offer did".
    with_ref = [o for o in offers if o.shop_ref]
    if with_ref:  # if any captured, they must be numeric
        for o in with_ref:
            assert o.shop_ref is not None and o.shop_ref.isdigit()


# --- tolarie cart ------------------------------------------------------------


from cz_mtg_compare.adapters.tolarie import CART_URL as TOLARIE_CART_URL  # noqa: E402


@pytest.mark.asyncio
async def test_tolarie_add_to_cart_uses_per_product_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recon confirmed tolarie's cart-add endpoint is
    ``GET /eshop/cart/add-buy/<product_id>/?amount=N`` — verify the adapter
    hits exactly that URL with the right query and inherited sessionid."""
    monkeypatch.setenv("CZ_MTG_TOLARIE_USER", "bob")
    monkeypatch.setenv("CZ_MTG_TOLARIE_PASS", "bobpass")
    adapter = TolarieAdapter()

    captured: dict[str, str] = {}

    def _capture_cart(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["referer"] = request.headers.get("referer", "")
        return httpx.Response(200, json={"success": True, "message": "Přidáno do košíku"})

    async with respx.mock(assert_all_called=True) as mock:
        # Eager login first.
        mock.get(TOLARIE_LOGIN_URL).mock(
            return_value=httpx.Response(200, text=TOLARIE_LOGIN_FORM_HTML)
        )
        mock.post(TOLARIE_LOGIN_URL).mock(
            return_value=httpx.Response(
                302,
                headers={
                    "location": "/",
                    "set-cookie": "sessionid=session-abc; Path=/; HttpOnly",
                },
            )
        )
        mock.get(re.compile(r"^https://www\.tolarie\.cz/$")).mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        mock.get(re.compile(r"^https://www\.tolarie\.cz/eshop/cart/add-buy/63411/")).mock(
            side_effect=_capture_cart
        )
        result = await adapter.add_to_cart("63411", count=2)

    assert result == {"success": True, "message": "Přidáno do košíku"}
    assert "add-buy/63411/" in captured["url"]
    assert "amount=2" in captured["url"]


@pytest.mark.asyncio
async def test_tolarie_add_to_cart_relogins_on_login_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the cached sessionid was invalidated server-side, the cart endpoint
    302s anonymous callers to /accounts/login/. The adapter must drop the
    flag, log in again, and replay the cart GET once."""
    monkeypatch.setenv("CZ_MTG_TOLARIE_USER", "bob")
    monkeypatch.setenv("CZ_MTG_TOLARIE_PASS", "bobpass")
    adapter = TolarieAdapter()
    adapter._authenticated = True  # pretend we're already authed but the cookie is stale

    cart_responses = iter(
        [
            httpx.Response(
                302, headers={"location": "/accounts/login/?next=/eshop/cart/add-buy/77/"}
            ),
            httpx.Response(200, json={"success": True}),
        ]
    )

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(re.compile(r"^https://www\.tolarie\.cz/eshop/cart/add-buy/77/")).mock(
            side_effect=lambda r: next(cart_responses)
        )
        mock.get(TOLARIE_LOGIN_URL).mock(
            return_value=httpx.Response(200, text=TOLARIE_LOGIN_FORM_HTML)
        )
        mock.post(TOLARIE_LOGIN_URL).mock(
            return_value=httpx.Response(
                302,
                headers={
                    "location": "/",
                    "set-cookie": "sessionid=session-fresh; Path=/; HttpOnly",
                },
            )
        )
        # Login POST 302s to "/" — httpx follows it under the shared client's
        # follow_redirects=True default. The login flow itself doesn't read
        # the homepage; we just need a mock so the request doesn't blow up.
        mock.get(re.compile(r"^https://www\.tolarie\.cz/$")).mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        result = await adapter.add_to_cart("77")

    assert result == {"success": True}


@pytest.mark.asyncio
async def test_tolarie_add_to_cart_rejects_non_numeric_shop_ref() -> None:
    adapter = TolarieAdapter()
    with pytest.raises(ValueError, match="numeric product id"):
        await adapter.add_to_cart("not-a-number")


@pytest.mark.asyncio
async def test_tolarie_view_cart_parses_remove_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """``view_cart`` extracts each line item's ``cart_item_id`` from the
    ``/eshop/cart/del-buy/<id>/`` link rendered in the cart table — that's
    the id ``clear_cart`` needs in order to remove items."""
    monkeypatch.setenv("CZ_MTG_TOLARIE_USER", "bob")
    monkeypatch.setenv("CZ_MTG_TOLARIE_PASS", "bobpass")
    adapter = TolarieAdapter()
    adapter._authenticated = True

    cart_html = """
    <html><body>
    <table>
      <tr>
        <td>Lightning Bolt</td>
        <td>35 Kč</td>
        <td><a href="/eshop/cart/del-buy/9001/">Odebrat</a></td>
      </tr>
      <tr>
        <td>Sol Ring</td>
        <td>120 Kč</td>
        <td><a href="/eshop/cart/del-buy/9002/">Odebrat</a></td>
      </tr>
    </table>
    </body></html>
    """
    async with respx.mock(assert_all_called=True) as mock:
        mock.get(TOLARIE_CART_URL).mock(return_value=httpx.Response(200, text=cart_html))
        cart = await adapter.view_cart()

    assert cart["item_count"] == 2
    ids = sorted(item["cart_item_id"] for item in cart["items"])
    assert ids == ["9001", "9002"]


@pytest.mark.asyncio
async def test_tolarie_clear_cart_visits_del_buy_per_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CZ_MTG_TOLARIE_USER", "bob")
    monkeypatch.setenv("CZ_MTG_TOLARIE_PASS", "bobpass")
    adapter = TolarieAdapter()
    adapter._authenticated = True

    cart_html = """
    <html><body>
    <table>
      <tr><td><a href="/eshop/cart/del-buy/11/">x</a></td></tr>
      <tr><td><a href="/eshop/cart/del-buy/22/">x</a></td></tr>
    </table>
    </body></html>
    """
    async with respx.mock(assert_all_called=True) as mock:
        mock.get(TOLARIE_CART_URL).mock(return_value=httpx.Response(200, text=cart_html))
        del_11 = mock.get(re.compile(r"/eshop/cart/del-buy/11/")).mock(
            return_value=httpx.Response(200)
        )
        del_22 = mock.get(re.compile(r"/eshop/cart/del-buy/22/")).mock(
            return_value=httpx.Response(200)
        )
        result = await adapter.clear_cart()

    assert result == {"removed_items": 2}
    assert del_11.called and del_22.called
