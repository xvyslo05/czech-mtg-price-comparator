from __future__ import annotations

import asyncio
import urllib.parse
from html import unescape

from selectolax.parser import HTMLParser, Node

from ..credentials import CredentialError, credentials_for
from ..http_client import get_client, host_slot
from ..models import Condition, Offer, SearchQuery
from ..normalize import (
    normalize_condition,
    parse_price_czk,
    parse_stock_qty,
    strip_card_suffixes,
)
from .base import ShopAdapter

BASE = "https://www.rishada.cz"
LOGIN_URL = f"{BASE}/"
PAGE_SIZE = 100


class RishadaAdapter(ShopAdapter):
    """Adapter for rishada.cz — Czech custom-PHP MTG shop with a tabular search results page.

    Search results live at `/kusovky/vysledky-hledani?searchtype=basic&xxcardname=<name>`.
    Each offer is one `<tr>` with cells (in order):
        name | edition | mana | type | P/T | condition | rarity | price | stock | buy
    """

    shop_id = "rishada"
    base_url = BASE
    supports_login = True
    # Cart submit buttons on anonymous search results have an onclick handler
    # that alerts "Pro manipulaci s košíkem musíte být přihlášeni!" — the
    # actual cart endpoint only renders once logged in. Shipped login-only;
    # cart is a follow-up once the post-login form shape is captured.
    supports_cart = False
    supports_watchlist = False

    def __init__(self) -> None:
        self._authenticated = False
        self._auth_lock = asyncio.Lock()

    def _search_url(self, query: SearchQuery) -> str:
        params = {
            "searchtype": "basic",
            "xxcardname": query.name,
            "xxpagesize": str(PAGE_SIZE),
        }
        return f"{BASE}/kusovky/vysledky-hledani?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        url = self._search_url(query)
        client = await get_client()
        async with host_slot("rishada.cz"):
            resp = await client.get(url)
        resp.raise_for_status()
        return self._parse(resp.text, url, query)

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        return self._parse(html, self._search_url(query), query)

    def _parse(self, html: str, url: str, query: SearchQuery) -> list[Offer]:
        tree = HTMLParser(html)
        offers: list[Offer] = []
        wanted = query.name.lower()
        edition_filter = (query.edition or "").lower().strip() or None

        # Result rows live inside <tr bgcolor="..."> elements within the result table.
        # The compact responsive layout drops the mana / type / P/T columns, so the
        # row can have anywhere from 6 to 9 td.tbody cells. Require at least 6
        # (name, edition, condition, rarity, price, stock) to qualify as a product row.
        for row in tree.css("tr"):
            cells = row.css("td.tbody")
            if len(cells) < 6:
                continue
            offer = self._parse_row(cells, url)
            if offer is None:
                continue
            if wanted not in offer.card_name.lower():
                continue
            if edition_filter and (
                not offer.edition or edition_filter not in offer.edition.lower()
            ):
                continue
            if query.in_stock_only and offer.stock_qty <= 0:
                continue
            offers.append(offer)
        return offers

    def _parse_row(self, cells: list[Node], url: str) -> Offer | None:
        # Cell 0: name. The text content is the visible card name (suffixes optional).
        name_node = cells[0]
        # Strip any tooltip/decoration content; take the visible text only.
        raw_name = unescape(name_node.text(separator=" ", strip=True))
        raw_name = " ".join(raw_name.split())
        if not raw_name:
            return None
        clean_name, foil_from_name, condition_from_name = strip_card_suffixes(raw_name)
        # Rishada uses descriptive parenthesised suffixes like "(judge foil)" or
        # "(etched foil)" that strip_card_suffixes preserves as-is. Whenever any
        # form of "foil" appears in the original name, treat the offer as foil.
        if "foil" in raw_name.lower():
            foil_from_name = True

        # Cell 1: edition.
        edition = " ".join(unescape(cells[1].text(strip=True)).split()) or None

        # Condition cell — typically index 5, but vary. Find the cell whose text matches
        # known condition wording.
        condition = condition_from_name
        for cell in cells:
            text = unescape(cell.text(strip=True))
            cond = normalize_condition(text)
            if cond is not Condition.UNKNOWN:
                condition = cond
                break
        if condition is Condition.UNKNOWN:
            condition = Condition.NM  # Rishada displays Near Mint by default.

        # Price cell — first cell containing 'Kč'.
        price_czk: int | None = None
        for cell in cells:
            text = unescape(cell.text(strip=True))
            if "Kč" in text or "CZK" in text.upper():
                price_czk = parse_price_czk(text)
                if price_czk is not None:
                    break
        if price_czk is None:
            return None

        # Stock cell — the cell after the price typically holds an integer stock count.
        stock_qty = 0
        for i, cell in enumerate(cells):
            text = unescape(cell.text(strip=True))
            if "Kč" in text and i + 1 < len(cells):
                stock_text = unescape(cells[i + 1].text(strip=True))
                stock_qty = parse_stock_qty(stock_text + " ks") if stock_text.isdigit() else parse_stock_qty(stock_text)
                if stock_text.isdigit():
                    stock_qty = int(stock_text)
                break

        return Offer(
            shop="rishada",
            card_name=clean_name,
            edition=edition,
            condition=condition,
            language=None,
            foil=foil_from_name,
            price_czk=price_czk,
            stock_qty=stock_qty,
            url=url,
        )

    # --- Account features ---------------------------------------------------

    async def login(self) -> None:
        async with self._auth_lock:
            await self._login_locked()

    async def _login_locked(self) -> None:
        creds = credentials_for("rishada")
        if creds is None:
            raise CredentialError(
                "rishada credentials not configured "
                "(set CZ_MTG_RISHADA_USER and CZ_MTG_RISHADA_PASS)"
            )
        client = await get_client()
        # rishada renders a tiny login form in the global sidebar — POST to /
        # with login-name + login-pass + dologin=<empty>. Session cookie name
        # varies across hosting setups but is set on success; the cleanest
        # confirmation is fetching the homepage afterwards and checking that
        # the menu no longer says "Uživatel: neznámý".
        async with host_slot("rishada.cz"):
            resp = await client.post(
                LOGIN_URL,
                data={
                    "login-name": creds.username,
                    "login-pass": creds.password,
                    "dologin": "",
                },
                headers={"Referer": LOGIN_URL},
                follow_redirects=False,
            )
        if resp.status_code not in (200, 302, 303):
            raise CredentialError(
                f"rishada login failed (status {resp.status_code})"
            )
        # The home page renders ``<form action="/" method="post" id="login-form">``
        # for anonymous visitors; once logged in that form is replaced by user
        # info. The original "Uživatel: neznámý" check was unreliable because
        # the rendered HTML is ``Uživatel: <span>...</span>`` so a substring
        # match against "neznámý" never actually fired against the right
        # element — checking the absence of the login form is the cleanest
        # post-auth signal that the site itself exposes.
        async with host_slot("rishada.cz"):
            check = await client.get(BASE + "/")
        if 'id="login-form"' in check.text:
            self._authenticated = False
            raise CredentialError(
                "rishada login: login form still rendered on the home page "
                "after submit — check CZ_MTG_RISHADA_* credentials"
            )
        self._authenticated = True
