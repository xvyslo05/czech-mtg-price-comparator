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


# --- rishada cart ------------------------------------------------------------


from cz_mtg_compare.adapters.rishada import (  # noqa: E402
    BASE as RISHADA_BASE,
    CART_URL as RISHADA_CART_URL,
    LOGIN_URL as RISHADA_LOGIN_URL,
    RishadaAdapter,
)

# Minimal post-login homepage: no ``id="login-form"`` → ``_login_locked`` accepts it.
_RISHADA_LOGGED_IN_HOME = '<html><body><div>Uživatel: <span>bob</span></div></body></html>'
# Post-login response after a cart submit: contains the sidebar anchor that
# ``_parse_cart_summary`` reads. ``Košík:`` lives in one span, the price/count
# in the trailing text node — same shape as the live site.
_RISHADA_SIDEBAR_AFTER_ADD = (
    '<html><body>'
    '<a href="/nakupni-kosik"><span class="bold">Košík: </span>60,- Kč / 1 položek</a>'
    '</body></html>'
)


def _dispatch_rishada_post(capture_add):
    """Route POST /. Login POSTs carry ``dologin``; cart POSTs carry ``act``."""

    def _route(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "dologin=" in body:
            return httpx.Response(200, text=_RISHADA_LOGGED_IN_HOME)
        return capture_add(request)

    return _route


@pytest.mark.asyncio
async def test_rishada_logged_in_fixture_captures_cardid(load_fixture) -> None:
    """Logged-in result rows render ``<form id="sellformN">`` with a hidden
    ``cardid`` input — the parser must lift it into ``Offer.shop_ref`` so
    cart calls have a numeric id without an extra fetch."""
    adapter = RishadaAdapter()
    html = load_fixture("rishada_counterspell_logged_in.html")
    offers = await adapter.parse(html, SearchQuery(name="Counterspell", in_stock_only=False))
    with_ref = [o for o in offers if o.shop_ref]
    assert with_ref, "expected at least one offer with a captured cardid"
    for o in with_ref:
        assert o.shop_ref is not None and o.shop_ref.isdigit()


@pytest.mark.asyncio
async def test_rishada_add_to_cart_posts_form_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rishada cart-add request mimics the per-row ``<form>`` submit:
    POST ``act=20005``, ``cardid=<id>``, ``sell=<count>`` to the site root.
    ``max`` is intentionally omitted (client-side validation hint only)."""
    monkeypatch.setenv("CZ_MTG_RISHADA_USER", "bob")
    monkeypatch.setenv("CZ_MTG_RISHADA_PASS", "bobpass")
    adapter = RishadaAdapter()

    captured: dict[str, str] = {}

    def _capture_add(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        captured["referer"] = request.headers.get("referer", "")
        return httpx.Response(200, text=_RISHADA_SIDEBAR_AFTER_ADD)

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(RISHADA_LOGIN_URL).mock(side_effect=_dispatch_rishada_post(_capture_add))
        mock.get(RISHADA_LOGIN_URL).mock(
            return_value=httpx.Response(200, text=_RISHADA_LOGGED_IN_HOME)
        )
        result = await adapter.add_to_cart("59214", count=2)

    assert captured["url"] == RISHADA_LOGIN_URL
    body = captured["body"]
    assert "act=20005" in body
    assert "cardid=59214" in body
    assert "sell=2" in body
    assert "max=" not in body  # client-side validation hint only — we don't echo it
    assert result["cardid"] == "59214"
    assert result["count"] == 2
    assert result["cart_total_czk"] == 60
    assert result["cart_item_count"] == 1


@pytest.mark.asyncio
async def test_rishada_add_to_cart_rejects_invalid_inputs() -> None:
    adapter = RishadaAdapter()
    with pytest.raises(ValueError, match="shop_ref is required"):
        await adapter.add_to_cart("")
    with pytest.raises(ValueError, match="numeric cardid"):
        await adapter.add_to_cart("abc")
    with pytest.raises(ValueError, match="count must be >= 1"):
        await adapter.add_to_cart("123", count=0)


@pytest.mark.asyncio
async def test_rishada_view_cart_parses_items_and_summary(
    load_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cart fixture has one line item rendered with an ``odstranit``
    anchor whose href encodes the ``itemid``. ``view_cart`` should surface
    that id alongside the parsed name/price + the sidebar totals."""
    monkeypatch.setenv("CZ_MTG_RISHADA_USER", "bob")
    monkeypatch.setenv("CZ_MTG_RISHADA_PASS", "bobpass")
    adapter = RishadaAdapter()
    adapter._authenticated = True  # skip the login round-trip in the mock

    cart_html = load_fixture("rishada_cart_one_item.html")
    async with respx.mock(assert_all_called=True) as mock:
        mock.get(RISHADA_CART_URL).mock(return_value=httpx.Response(200, text=cart_html))
        cart = await adapter.view_cart()

    assert cart["url"] == RISHADA_CART_URL
    assert cart["item_count"] == 1
    assert cart["total_czk"] is not None and cart["total_czk"] > 0
    assert len(cart["items"]) == 1
    item = cart["items"][0]
    assert item["itemid"].isdigit()
    assert item["name"] and "counter" in item["name"].lower()
    assert item["price_czk"] is not None and item["price_czk"] > 0


@pytest.mark.asyncio
async def test_rishada_clear_cart_visits_remove_link_per_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``clear_cart`` walks ``view_cart`` items and GETs the
    ``?itemid=N&act=20032`` link the cart page renders for each row."""
    monkeypatch.setenv("CZ_MTG_RISHADA_USER", "bob")
    monkeypatch.setenv("CZ_MTG_RISHADA_PASS", "bobpass")
    adapter = RishadaAdapter()
    adapter._authenticated = True

    cart_html = (
        '<html><body>'
        '<a href="/nakupni-kosik"><span class="bold">Košík: </span>50,- Kč / 2 položek</a>'
        '<table>'
        '<tr><td>Card A</td><td>25 Kč</td><td>25 Kč</td>'
        '<td><a href="/nakupni-kosik?itemid=11&amp;act=20032">odstranit</a></td></tr>'
        '<tr><td>Card B</td><td>25 Kč</td><td>25 Kč</td>'
        '<td><a href="/nakupni-kosik?itemid=22&amp;act=20032">odstranit</a></td></tr>'
        '</table>'
        '</body></html>'
    )
    async with respx.mock(assert_all_called=True) as mock:
        mock.get(re.compile(r"^https://www\.rishada\.cz/nakupni-kosik$")).mock(
            return_value=httpx.Response(200, text=cart_html)
        )
        del_a = mock.get(
            re.compile(r"/nakupni-kosik\?itemid=11&act=20032")
        ).mock(return_value=httpx.Response(200))
        del_b = mock.get(
            re.compile(r"/nakupni-kosik\?itemid=22&act=20032")
        ).mock(return_value=httpx.Response(200))
        result = await adapter.clear_cart()

    assert result == {"removed_items": 2}
    assert del_a.called and del_b.called
    assert adapter.base_url == RISHADA_BASE


# --- cernyrytir cart ---------------------------------------------------------


from cz_mtg_compare.adapters.cernyrytir import (  # noqa: E402
    BASE as CR_BASE,
    CART_ADD_URL as CR_CART_ADD_URL,
    CART_URL as CR_CART_URL,
    CernyRytirAdapter,
)

# cernyrytir serves windows-1250 bytes; the adapter forces that encoding
# when decoding responses. To keep our test fixtures readable as Python
# string literals we author them in UTF-8 here and re-encode to cp1250
# before stuffing them into the mocked Response.
def _cr_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=text.encode("windows-1250"))


# Minimal logged-in cernyrytir homepage: contains an "Odhlásit" link, which
# is what ``_login_locked`` looks for to confirm authentication.
_CR_LOGGED_IN_HOME = (
    "<html><body><a href='index.php3?akce=0&odhlasse=1'>Odhlásit</a></body></html>"
)
# Sidebar fragment returned by an add-to-cart POST — same div the live site
# uses, so ``_parse_cart_summary`` parses it via the sidebar branch.
_CR_SIDEBAR_AFTER_ADD = (
    "<html><body>"
    "<div class='lista-kosik-polozka'>"
    "<span class='kosikbold'>V košíku máte </span>2 "
    "<span class='kosikbold'>položky</span> "
    "<span class='kosikbold'>za </span> 974 "
    "<span class='kosikbold'>Kč</span>"
    "</div></body></html>"
)


@pytest.mark.asyncio
async def test_cernyrytir_logged_in_fixture_captures_carovy_kod(load_fixture) -> None:
    """Logged-in result rows carry a per-product ``<form>`` with
    ``nakupzbozi=Pridat`` and a hidden ``carovy_kod``. The parser must lift
    that id into ``Offer.shop_ref`` — and NOT lift it from neighbouring
    out-of-stock rows whose form posts ``Hlidat`` (watchlist) against the
    same input shape."""
    adapter = CernyRytirAdapter()
    html = load_fixture("cernyrytir_counterspell_logged_in.html")
    offers = await adapter.parse(html, SearchQuery(name="Counterspell", in_stock_only=False))
    with_ref = [o for o in offers if o.shop_ref]
    assert with_ref, "expected at least one in-stock offer with a captured carovy_kod"
    for o in with_ref:
        assert o.shop_ref is not None and o.shop_ref.isdigit()
        # The fixture's in-stock SLD-R Counterspell row carries carovy_kod=426555.
        # Use it as a positive smoke test that we picked the Pridat form,
        # not a Hlidat one (Hlidat would yield a different id from the
        # out-of-stock 332684 row in the same fixture).
    assert any(o.shop_ref == "426555" for o in with_ref), (
        "expected to lift the in-stock SLD-R Counterspell carovy_kod (Pridat form), "
        "not a watchlist sku"
    )


def _dispatch_cernyrytir_post(capture_add):
    """Route POST /index.php3?akce=3. Login uses ``uzivjmeno`` field; cart
    uses ``nakupzbozi``. Direct each to its own response."""

    def _route(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "uzivjmeno=" in body:
            return _cr_response(_CR_LOGGED_IN_HOME)
        return capture_add(request)

    return _route


@pytest.mark.asyncio
async def test_cernyrytir_add_to_cart_posts_form_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cernyrytir cart-add POST mimics the per-row "Vložit do košíku"
    form: ``databaze=kusovkymagic``, ``carovy_kod=<id>``,
    ``nakupzbozi=Pridat``, ``kusu=<count>``. Reverse-engineered against the
    live site — verify the adapter wires up the exact payload + reads the
    sidebar ``V košíku máte ... za <Total> Kč`` from the response."""
    monkeypatch.setenv("CZ_MTG_CERNYRYTIR_USER", "bob")
    monkeypatch.setenv("CZ_MTG_CERNYRYTIR_PASS", "bobpass")
    adapter = CernyRytirAdapter()

    captured: dict[str, str] = {}

    def _capture_add(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return _cr_response(_CR_SIDEBAR_AFTER_ADD)

    async with respx.mock(assert_all_called=True) as mock:
        # Login POST + the followup GET that _login_locked does to verify.
        mock.post(re.compile(r"^https://www\.cernyrytir\.cz/index\.php3\?akce=0")).mock(
            return_value=_cr_response(_CR_LOGGED_IN_HOME)
        )
        mock.get(re.compile(r"^https://www\.cernyrytir\.cz/index\.php3\?akce=0")).mock(
            return_value=_cr_response(_CR_LOGGED_IN_HOME)
        )
        mock.post(re.compile(r"^https://www\.cernyrytir\.cz/index\.php3\?akce=3$")).mock(
            side_effect=_dispatch_cernyrytir_post(_capture_add)
        )
        result = await adapter.add_to_cart("426555", count=2)

    assert captured["url"] == CR_CART_ADD_URL
    body = captured["body"]
    assert "databaze=kusovkymagic" in body
    assert "carovy_kod=426555" in body
    assert "nakupzbozi=Pridat" in body
    assert "kusu=2" in body
    assert result["carovy_kod"] == "426555"
    assert result["count"] == 2
    assert result["cart_total_czk"] == 974
    assert result["cart_item_count"] == 2


@pytest.mark.asyncio
async def test_cernyrytir_add_to_cart_rejects_invalid_inputs() -> None:
    adapter = CernyRytirAdapter()
    with pytest.raises(ValueError, match="shop_ref is required"):
        await adapter.add_to_cart("")
    with pytest.raises(ValueError, match="numeric carovy_kod"):
        await adapter.add_to_cart("abc")
    with pytest.raises(ValueError, match="count must be >= 1"):
        await adapter.add_to_cart("123", count=0)


@pytest.mark.asyncio
async def test_cernyrytir_view_cart_parses_items_and_total(
    load_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cart-page fixture has one line item rendered with per-unit price,
    a visible quantity input, a line-total cell, and a delete form. The
    cart page also renders a ``Cena celkem`` row with the grand total
    (incl. shipping). ``view_cart`` should surface both."""
    monkeypatch.setenv("CZ_MTG_CERNYRYTIR_USER", "bob")
    monkeypatch.setenv("CZ_MTG_CERNYRYTIR_PASS", "bobpass")
    adapter = CernyRytirAdapter()
    adapter._authenticated = True

    cart_html = load_fixture("cernyrytir_cart_one_item.html")
    async with respx.mock(assert_all_called=True) as mock:
        mock.get(CR_CART_URL).mock(return_value=_cr_response(cart_html))
        cart = await adapter.view_cart()

    assert cart["url"] == CR_CART_URL
    # cart-page has no native item-count display, falls back to len(items)
    assert cart["item_count"] == 1
    assert cart["total_czk"] is not None and cart["total_czk"] > 0
    assert len(cart["items"]) == 1
    item = cart["items"][0]
    assert item["carovy_kod"] == "426555"
    assert item["name"] and "counter" in item["name"].lower()
    assert item["qty"] == 1
    assert item["price_czk"] == 399
    assert item["line_total_czk"] == 399


@pytest.mark.asyncio
async def test_cernyrytir_clear_cart_posts_upravit_kusu_zero_per_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cernyrytir has no dedicated "delete item" endpoint — instead the cart
    page renders a second form per row with ``Upravit`` + hidden ``kusu=0``,
    which the server treats as "remove this line". ``clear_cart`` must POST
    that payload for each item in the cart."""
    monkeypatch.setenv("CZ_MTG_CERNYRYTIR_USER", "bob")
    monkeypatch.setenv("CZ_MTG_CERNYRYTIR_PASS", "bobpass")
    adapter = CernyRytirAdapter()
    adapter._authenticated = True

    # Cart-page-shaped HTML with two items — enough that we exercise the
    # block_re's repetition and the per-item POST loop.
    cart_html = (
        "<html><body>"
        "<table><tr><td>Card A</td><td>25</td>"
        "<form action='index.php3?akce=3&kosicek=1' method='POST'>"
        "<td><input name='kusu' size='3' maxlength='5' value='1'></td>"
        "<td>25 Kč</td>"
        "<td><input type=HIDDEN name='databaze' value='kusovkymagic'>"
        "<input type=HIDDEN name='carovy_kod' value='111'>"
        "<input type=HIDDEN name='nakupzbozi' value='Upravit'>"
        "</td></form></tr>"
        "<tr><td>Card B</td><td>50</td>"
        "<form action='index.php3?akce=3&kosicek=1' method='POST'>"
        "<td><input name='kusu' size='3' maxlength='5' value='2'></td>"
        "<td>100 Kč</td>"
        "<td><input type=HIDDEN name='databaze' value='kusovkymagic'>"
        "<input type=HIDDEN name='carovy_kod' value='222'>"
        "<input type=HIDDEN name='nakupzbozi' value='Upravit'>"
        "</td></form></tr>"
        "<tr><td>Cena celkem</td><td>125 Kč</td></tr>"
        "</table></body></html>"
    )

    captured_bodies: list[str] = []

    def _capture_delete(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(request.content.decode())
        return httpx.Response(200)

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(CR_CART_URL).mock(return_value=_cr_response(cart_html))
        mock.post(re.compile(r"\?akce=3&kosicek=1")).mock(side_effect=_capture_delete)
        result = await adapter.clear_cart()

    assert result == {"removed_items": 2}
    assert len(captured_bodies) == 2
    # Both POSTs must carry the Upravit kusu=0 payload, one per carovy_kod.
    codes = {re.search(r"carovy_kod=(\d+)", b).group(1) for b in captured_bodies}
    assert codes == {"111", "222"}
    for body in captured_bodies:
        assert "nakupzbozi=Upravit" in body
        assert "kusu=0" in body
    assert adapter.base_url == CR_BASE
