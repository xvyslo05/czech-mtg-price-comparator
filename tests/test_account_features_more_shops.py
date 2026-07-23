"""Account-feature tests for the four shops added on top of the najada/tolarie
PR: blacklotus, cernyrytir, rishada, untap. Login is implemented for all four;
add_to_cart / view_cart / clear_cart are wired for blacklotus, rishada, and
cernyrytir. untap implements cart against the API but sessions don't persist
between logins, so the capability flag is deliberately False."""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from cz_mtg_compare import credentials
from cz_mtg_compare.adapters.axionnow import AxionNowAdapter
from cz_mtg_compare.adapters.base import AccountFeatureNotSupported
from cz_mtg_compare.adapters.bazaargames import BazaarGamesAdapter
from cz_mtg_compare.adapters.blacklotus import (
    CART_ADD_URL,
    CART_CONTENT_URL,
    CART_DELETE_URL,
    LOGIN_URL as BL_LOGIN_URL,
    BlackLotusAdapter,
)
from cz_mtg_compare.adapters.cernyrytir import (
    LOGIN_URL as CR_LOGIN_URL,
    CernyRytirAdapter,
)
from cz_mtg_compare.adapters.rishada import LOGIN_URL as RI_LOGIN_URL, RishadaAdapter
from cz_mtg_compare.adapters.jkentertainment import JkEntertainmentAdapter
from cz_mtg_compare.adapters.magiccorporation import MagicCorporationAdapter
from cz_mtg_compare.adapters.magicmadhouse import MagicMadhouseAdapter
from cz_mtg_compare.adapters.magicstore import MagicStoreAdapter
from cz_mtg_compare.adapters.mtgspot import MtgspotAdapter
from cz_mtg_compare.adapters.traderonline import TraderOnlineAdapter
from cz_mtg_compare.adapters.untap import (
    ACCOUNT_URL,
    CART_URL,
    LOGIN_URL as UT_LOGIN_URL,
    UntapAdapter,
)
from cz_mtg_compare.models import SearchQuery


@pytest.fixture(autouse=True)
def _isolated_creds(monkeypatch: pytest.MonkeyPatch):
    import os

    for key in list(os.environ):
        if key.startswith("CZ_MTG_") and (key.endswith("_USER") or key.endswith("_PASS")):
            monkeypatch.delenv(key, raising=False)
    credentials.reset_secret_cache()
    yield


# --- Capability matrix ------------------------------------------------------


def test_capability_flags_per_shop() -> None:
    """The four new adapters expose the right capability flags. blacklotus,
    rishada, and cernyrytir support cart; untap is login-only because its
    cart implementation works against the Prestashop API but doesn't persist
    across sessions (each adapter login starts a fresh checkout), so the
    feature is deliberately disabled at the capability layer to avoid
    silently confusing users."""
    bl = BlackLotusAdapter(enrich_detail=False)
    assert (bl.supports_login, bl.supports_cart, bl.supports_watchlist) == (True, True, False)

    ut = UntapAdapter()
    assert (ut.supports_login, ut.supports_cart, ut.supports_watchlist) == (True, False, False)

    cr = CernyRytirAdapter()
    assert (cr.supports_login, cr.supports_cart, cr.supports_watchlist) == (True, True, False)

    ri = RishadaAdapter()
    assert (ri.supports_login, ri.supports_cart, ri.supports_watchlist) == (True, True, False)

    read_only_adapters = [
        AxionNowAdapter(),
        MtgspotAdapter(),
        MagicCorporationAdapter(),
        JkEntertainmentAdapter(),
        TraderOnlineAdapter(),
        MagicMadhouseAdapter(),
        MagicStoreAdapter(),
        BazaarGamesAdapter(
            shop_id="bazaarofmagic",
            base_url="https://www.bazaarofmagic.eu",
        ),
        BazaarGamesAdapter(
            shop_id="spellenwinkel",
            base_url="https://www.spellenwinkel.nl",
        ),
    ]
    assert all(
        (
            adapter.supports_login,
            adapter.supports_cart,
            adapter.supports_watchlist,
        )
        == (False, False, False)
        for adapter in read_only_adapters
    )


# --- blacklotus -------------------------------------------------------------


@pytest.mark.asyncio
async def test_blacklotus_login_succeeds_when_customer_cookie_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CZ_MTG_BLACKLOTUS_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_BLACKLOTUS_PASS", "secret")
    adapter = BlackLotusAdapter(enrich_detail=False)

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(BL_LOGIN_URL).mock(
            return_value=httpx.Response(
                302,
                headers={
                    "location": "/klient/",
                    "set-cookie": "customer-id=42; Path=/; Secure",
                },
            )
        )
        await adapter.login()
    assert adapter._authenticated is True


@pytest.mark.asyncio
async def test_blacklotus_login_failure_redirects_back_to_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CZ_MTG_BLACKLOTUS_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_BLACKLOTUS_PASS", "wrong")
    adapter = BlackLotusAdapter(enrich_detail=False)

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(BL_LOGIN_URL).mock(
            return_value=httpx.Response(
                302,
                headers={"location": "/login/?error=1"},
            )
        )
        with pytest.raises(credentials.CredentialError, match="blacklotus login failed"):
            await adapter.login()
    assert adapter._authenticated is False


@pytest.mark.asyncio
async def test_blacklotus_add_to_cart_posts_priceId(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_BLACKLOTUS_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_BLACKLOTUS_PASS", "secret")
    adapter = BlackLotusAdapter(enrich_detail=False)

    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        body = dict(p.split("=", 1) for p in request.content.decode().split("&"))
        captured.update(body)
        return httpx.Response(302, headers={"location": "/"})

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(BL_LOGIN_URL).mock(
            return_value=httpx.Response(
                302,
                headers={
                    "location": "/klient/",
                    "set-cookie": "customer-id=42; Path=/; Secure",
                },
            )
        )
        mock.post(CART_ADD_URL).mock(side_effect=_capture)
        result = await adapter.add_to_cart("645082", count=3)

    assert captured["priceId"] == "645082"
    assert captured["amount"] == "3"
    assert captured["language"] == "cs"
    assert result["priceId"] == "645082"


@pytest.mark.asyncio
async def test_blacklotus_add_to_cart_requires_shop_ref() -> None:
    adapter = BlackLotusAdapter(enrich_detail=False)
    with pytest.raises(ValueError, match="shop_ref is required"):
        await adapter.add_to_cart("")


@pytest.mark.asyncio
async def test_blacklotus_add_to_cart_retries_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the cached session is rejected (403), the adapter logs back in
    and replays the cart POST once before surfacing an error."""
    monkeypatch.setenv("CZ_MTG_BLACKLOTUS_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_BLACKLOTUS_PASS", "secret")
    adapter = BlackLotusAdapter(enrich_detail=False)
    cart_responses = iter(
        [
            httpx.Response(403),  # first attempt rejected
            httpx.Response(302, headers={"location": "/"}),  # retry succeeds
        ]
    )

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(BL_LOGIN_URL).mock(
            return_value=httpx.Response(
                302,
                headers={
                    "location": "/klient/",
                    "set-cookie": "customer-id=42; Path=/; Secure",
                },
            )
        )
        mock.post(CART_ADD_URL).mock(side_effect=lambda r: next(cart_responses))
        result = await adapter.add_to_cart("645082")

    assert result["status_code"] == 302


@pytest.mark.asyncio
async def test_blacklotus_captures_priceId_into_shop_ref() -> None:
    """When the search-result HTML contains a per-row addCartItem form, the
    priceId hidden input must flow into ``Offer.shop_ref``."""
    adapter = BlackLotusAdapter(enrich_detail=False)
    # Craft minimal HTML that includes the priceId input inside the product
    # microdata block the parser already recognises.
    html = """
    <html><body>
      <div class="product">
        <div class="p" data-micro="product">
          <span data-micro="name">Lightning Bolt</span>
          <span data-micro="offer" data-micro-price="29" data-micro-availability="InStock"></span>
          <span class="availability-amount">4 ks</span>
          <a data-micro="url" href="/lightning-bolt/"></a>
          <img alt="Lightning Bolt (Foil NE, Stav Near Mint)">
          <form action="/action/Cart/addCartItem/" method="post" class="csrf-enabled">
            <input type="hidden" name="priceId" value="645082" />
            <input type="hidden" name="productId" value="443095" />
            <input type="hidden" name="amount" value="1" />
          </form>
        </div>
      </div>
    </body></html>
    """
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    assert offers, "fixture parser regressed"
    assert offers[0].shop_ref == "645082"


# --- untap ------------------------------------------------------------------


UNTAP_HOMEPAGE_HTML = (
    "<html><head>"
    "<script>var prestashop = { static_token: 'abc123def456' };</script>"
    "</head><body></body></html>"
)


@pytest.mark.asyncio
async def test_untap_login_succeeds_and_captures_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_UNTAP_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_UNTAP_PASS", "secret")
    adapter = UntapAdapter()

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(UT_LOGIN_URL).mock(
            return_value=httpx.Response(
                302,
                headers={
                    "location": "/muj-ucet",
                    "set-cookie": "PrestaShop-abc123=session-payload; Path=/; HttpOnly",
                },
            )
        )
        mock.get(ACCOUNT_URL).mock(
            return_value=httpx.Response(200, text="<html>Můj účet</html>")
        )
        mock.get(re.compile(r"^https://untap\.cz/$")).mock(
            return_value=httpx.Response(200, text=UNTAP_HOMEPAGE_HTML)
        )
        await adapter.login()

    assert adapter._authenticated is True
    assert adapter._static_token == "abc123def456"


@pytest.mark.asyncio
async def test_untap_login_failure_when_account_page_redirects_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CZ_MTG_UNTAP_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_UNTAP_PASS", "wrong")
    adapter = UntapAdapter()

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(UT_LOGIN_URL).mock(
            return_value=httpx.Response(
                302,
                headers={
                    "location": "/p%c5%99ihl%c3%a1sit?back=my-account",
                    "set-cookie": "PrestaShop-abc=anon; Path=/; HttpOnly",
                },
            )
        )
        mock.get(ACCOUNT_URL).mock(
            return_value=httpx.Response(
                302, headers={"location": "/p%c5%99ihl%c3%a1sit?back=my-account"}
            )
        )
        with pytest.raises(credentials.CredentialError, match="untap login failed"):
            await adapter.login()


@pytest.mark.asyncio
async def test_untap_add_to_cart_posts_id_product_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CZ_MTG_UNTAP_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_UNTAP_PASS", "secret")
    adapter = UntapAdapter()

    captured: dict[str, str] = {}

    def _capture_cart(request: httpx.Request) -> httpx.Response:
        body = dict(p.split("=", 1) for p in request.content.decode().split("&"))
        captured.update(body)
        return httpx.Response(200, json={"ok": True})

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(UT_LOGIN_URL).mock(
            return_value=httpx.Response(
                302,
                headers={
                    "location": "/muj-ucet",
                    "set-cookie": "PrestaShop-abc=session; Path=/; HttpOnly",
                },
            )
        )
        mock.get(ACCOUNT_URL).mock(
            return_value=httpx.Response(200, text="<html>Můj účet</html>")
        )
        mock.get(re.compile(r"^https://untap\.cz/$")).mock(
            return_value=httpx.Response(200, text=UNTAP_HOMEPAGE_HTML)
        )
        mock.post(CART_URL).mock(side_effect=_capture_cart)

        result = await adapter.add_to_cart("21061", count=2)

    assert captured["token"] == "abc123def456"
    assert captured["id_product"] == "21061"
    assert captured["qty"] == "2"
    assert captured["add"] == "1"
    # Prestashop only switches to a JSON cart response when ``ajax=1`` is in
    # the body. X-Requested-With on its own is silently ignored and the
    # server returns the full cart HTML — see the live recon that found
    # this. Regression-guard against accidentally dropping ajax=1.
    assert captured["ajax"] == "1"
    assert captured["id_product_attribute"] == "0"
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_untap_add_to_cart_surfaces_prestashop_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Prestashop refuses the add (out of stock, quantity rule …), the
    JSON response has ``hasError=true`` and a localised ``errors`` array.
    The adapter must raise with that message instead of silently returning
    success — that was the "cart stays empty" bug."""
    monkeypatch.setenv("CZ_MTG_UNTAP_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_UNTAP_PASS", "secret")
    adapter = UntapAdapter()
    adapter._authenticated = True
    adapter._static_token = "abc123def456"

    error_payload = {
        "hasError": True,
        "errors": [
            'Produktu "Lightning Bolt" můžete koupit pouze 0 ks. '
            "Pro pokračování prosím upravte množství v košíku.",
        ],
        "quantity": 0,
    }
    async with respx.mock(assert_all_called=True) as mock:
        mock.post(CART_URL).mock(return_value=httpx.Response(200, json=error_payload))
        with pytest.raises(RuntimeError, match="můžete koupit pouze 0 ks"):
            await adapter.add_to_cart("21061")


@pytest.mark.asyncio
async def test_untap_add_to_cart_raises_when_response_is_html_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Prestashop returns HTML instead of JSON, something is wrong with
    the AJAX dispatch (session reset, proxy stripping fields, template
    change). Surface that loudly rather than pretending the add worked."""
    monkeypatch.setenv("CZ_MTG_UNTAP_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_UNTAP_PASS", "secret")
    adapter = UntapAdapter()
    adapter._authenticated = True
    adapter._static_token = "abc123def456"

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(CART_URL).mock(
            return_value=httpx.Response(
                200,
                text="<html><body>full cart page</body></html>",
                headers={"content-type": "text/html; charset=utf-8"},
            )
        )
        with pytest.raises(credentials.CredentialError, match="HTML, not JSON"):
            await adapter.add_to_cart("21061")


@pytest.mark.asyncio
async def test_untap_search_captures_id_product_into_shop_ref(load_fixture) -> None:
    """The existing untap fixture's product links encode id_product as the
    leading number in ``/<id>-<slug>.html`` — the parser must surface it on
    Offer.shop_ref."""
    adapter = UntapAdapter()
    html = load_fixture("untap_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    assert offers, "fixture parser regressed"
    with_ref = [o for o in offers if o.shop_ref]
    assert with_ref, "no untap offer captured an id_product"
    for o in with_ref:
        assert o.shop_ref is not None
        assert o.shop_ref.isdigit()


# --- cernyrytir + rishada (login flow only) ---------------------------------


@pytest.mark.asyncio
async def test_cernyrytir_login_confirms_via_account_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_CERNYRYTIR_USER", "bob")
    monkeypatch.setenv("CZ_MTG_CERNYRYTIR_PASS", "bobpass")
    adapter = CernyRytirAdapter()

    captured: dict[str, str] = {}

    def _login(request: httpx.Request) -> httpx.Response:
        captured.update(dict(p.split("=", 1) for p in request.content.decode().split("&")))
        return httpx.Response(200, text="ok")

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(CR_LOGIN_URL).mock(side_effect=_login)
        # Account-page probe: must include an "Odhlásit" / "Odhlášení" hint
        # to count as logged in.
        mock.get(CR_LOGIN_URL).mock(
            return_value=httpx.Response(
                200,
                content="<html>vítejte, <a>Odhlášení</a></html>".encode("windows-1250"),
            )
        )
        await adapter.login()

    assert captured["uzivjmeno"] == "bob"
    assert captured["uzivheslo"] == "bobpass"
    assert "login" in captured  # hidden flag is included
    assert adapter._authenticated is True


@pytest.mark.asyncio
async def test_cernyrytir_login_failure_without_logout_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CZ_MTG_CERNYRYTIR_USER", "bob")
    monkeypatch.setenv("CZ_MTG_CERNYRYTIR_PASS", "wrong")
    adapter = CernyRytirAdapter()

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(CR_LOGIN_URL).mock(return_value=httpx.Response(200, text="ok"))
        mock.get(CR_LOGIN_URL).mock(
            return_value=httpx.Response(
                200,
                content="<html>Přihlášení: <form>...</form></html>".encode("windows-1250"),
            )
        )
        with pytest.raises(credentials.CredentialError, match="logged-in account page"):
            await adapter.login()


@pytest.mark.asyncio
async def test_rishada_login_confirms_via_user_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_RISHADA_USER", "carol")
    monkeypatch.setenv("CZ_MTG_RISHADA_PASS", "carolpass")
    adapter = RishadaAdapter()

    captured: dict[str, str] = {}

    def _login(request: httpx.Request) -> httpx.Response:
        captured.update(dict(p.split("=", 1) for p in request.content.decode().split("&")))
        return httpx.Response(200, text="ok")

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(RI_LOGIN_URL).mock(side_effect=_login)
        # Post-login home page no longer renders the login form.
        mock.get(re.compile(r"^https://www\.rishada\.cz/$")).mock(
            return_value=httpx.Response(
                200, text="<html><body>Vítejte zpět, carol</body></html>"
            )
        )
        await adapter.login()

    assert captured["login-name"] == "carol"
    assert captured["login-pass"] == "carolpass"
    assert adapter._authenticated is True


@pytest.mark.asyncio
async def test_rishada_login_failure_still_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_RISHADA_USER", "carol")
    monkeypatch.setenv("CZ_MTG_RISHADA_PASS", "wrong")
    adapter = RishadaAdapter()

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(RI_LOGIN_URL).mock(return_value=httpx.Response(200, text="ok"))
        # Login failure: the home page still renders the login form (id="login-form").
        mock.get(re.compile(r"^https://www\.rishada\.cz/$")).mock(
            return_value=httpx.Response(
                200,
                text='<html><form action="/" method="post" id="login-form">'
                '<input name="login-name"></form></html>',
            )
        )
        with pytest.raises(credentials.CredentialError, match="login form still rendered"):
            await adapter.login()
