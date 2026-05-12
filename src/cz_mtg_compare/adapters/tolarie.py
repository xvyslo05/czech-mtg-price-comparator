from __future__ import annotations

import asyncio
import re
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

BASE = "https://www.tolarie.cz"
LOGIN_URL = f"{BASE}/accounts/login/"

# Cart product id is encoded in classes like ``js-add_to_cart_amount-63411-card``
# on search-result rows. Captured during search so cart features can reference it.
_PRODUCT_ID_RE = re.compile(r"js-add_to_cart_amount-(\d+)-card")

# Tolarie encodes condition + foil as small icons in <td class="td_priznak">,
# each wrapped in <a data-tooltip="...">. Map the tooltip text to our model.
_TOOLTIP_TO_CONDITION: dict[str, Condition] = {
    "near mint": Condition.NM,
    "excellent": Condition.EX,
    "slightly played": Condition.LP,
    "lightly played": Condition.LP,
    "played": Condition.PL,
    "moderately played": Condition.PL,
    "heavily played": Condition.HP,
    "poor": Condition.HP,
}


class TolarieAdapter(ShopAdapter):
    shop_id = "tolarie"
    base_url = BASE
    supports_login = True
    # Cart and watchlist endpoints exist on tolarie.cz but their exact request
    # shapes can only be confirmed from inside a logged-in session. Login is
    # implemented so a future PR can add cart/watchlist once verified live;
    # the search adapter captures product IDs into Offer.shop_ref now so that
    # follow-up doesn't need to touch the parser.
    supports_cart = False
    supports_watchlist = False

    def __init__(self) -> None:
        self._authenticated = False
        self._auth_lock = asyncio.Lock()

    def _search_url(self, query: SearchQuery) -> str:
        params = {"q": query.name, "searchbtn": ""}
        return f"{BASE}/vyhledavani/?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        url = self._search_url(query)
        client = await get_client()
        async with host_slot("tolarie.cz"):
            resp = await client.get(url)
        resp.raise_for_status()
        return self._parse(resp.text, url, query)

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        return self._parse(html, self._search_url(query), query)

    def _parse(self, html: str, url: str, query: SearchQuery) -> list[Offer]:
        tree = HTMLParser(html)
        rows = tree.css("table.kusovky_search tbody tr")
        offers: list[Offer] = []
        wanted = query.name.lower()
        edition_filter = (query.edition or "").lower().strip() or None

        for row in rows:
            offer = self._parse_row(row, url)
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

    def _parse_row(self, row: Node, url: str) -> Offer | None:
        name_cell = row.css_first("td.td_name")
        price_cell = row.css_first("td.td_price")
        if name_cell is None or price_cell is None:
            return None

        name_node = name_cell.css_first("span.product_name")
        if name_node is None:
            return None
        # Strip nested image markup; keep text only.
        for img in name_node.css("img"):
            img.decompose()
        raw_name = unescape(name_node.text(separator=" ", strip=True))
        raw_name = " ".join(raw_name.split())
        if not raw_name:
            return None

        clean_name, foil_from_name, condition_from_name = strip_card_suffixes(raw_name)

        avail_node = name_cell.css_first("div.availability")
        stock_qty = parse_stock_qty(avail_node.text(strip=True) if avail_node else "")

        price_text = price_cell.text(strip=True)
        price_czk = parse_price_czk(price_text)
        if price_czk is None:
            return None

        # Edition
        edition_cell = row.css_first("td.td_edice")
        edition = (
            unescape(edition_cell.text(separator=" ", strip=True)) if edition_cell else None
        )
        edition = " ".join(edition.split()) if edition else None
        if edition == "Edice":  # header leftover (defensive)
            edition = None

        # Priznak: foil + condition icons (data-tooltip)
        foil = foil_from_name
        condition = condition_from_name
        priznak_cell = row.css_first("td.td_priznak")
        if priznak_cell is not None:
            for tooltip_node in priznak_cell.css("[data-tooltip]"):
                tip = (tooltip_node.attributes.get("data-tooltip") or "").strip().lower()
                if tip == "foil":
                    foil = True
                elif tip in _TOOLTIP_TO_CONDITION:
                    condition = _TOOLTIP_TO_CONDITION[tip]
                else:
                    cond = normalize_condition(tip)
                    if cond is not Condition.UNKNOWN:
                        condition = cond

        # Tolarie default condition is NM unless an icon says otherwise.
        if condition is Condition.UNKNOWN:
            condition = Condition.NM

        shop_ref: str | None = None
        cart_input = row.css_first("input.js-add_to_cart_amount-card") or row.css_first(
            "input[class*='js-add_to_cart_amount']"
        )
        if cart_input is not None:
            cls = cart_input.attributes.get("class") or ""
            match = _PRODUCT_ID_RE.search(cls)
            if match:
                shop_ref = match.group(1)

        return Offer(
            shop="tolarie",
            card_name=clean_name,
            edition=edition,
            condition=condition,
            language=None,
            foil=foil,
            price_czk=price_czk,
            stock_qty=stock_qty,
            url=url,
            shop_ref=shop_ref,
        )

    # --- Account features ---------------------------------------------------

    async def login(self) -> None:
        async with self._auth_lock:
            await self._login_locked()

    async def _login_locked(self) -> None:
        creds = credentials_for("tolarie")
        if creds is None:
            raise CredentialError(
                "tolarie credentials not configured "
                "(set CZ_MTG_TOLARIE_USER and CZ_MTG_TOLARIE_PASS)"
            )
        client = await get_client()
        async with host_slot("tolarie.cz"):
            form_resp = await client.get(LOGIN_URL)
        form_resp.raise_for_status()
        tree = HTMLParser(form_resp.text)
        csrf_node = tree.css_first('input[name="csrfmiddlewaretoken"]')
        if csrf_node is None:
            raise CredentialError(
                "tolarie login: csrfmiddlewaretoken input not found on login page"
            )
        csrf = (csrf_node.attributes.get("value") or "").strip()
        if not csrf:
            raise CredentialError("tolarie login: empty csrfmiddlewaretoken")
        async with host_slot("tolarie.cz"):
            resp = await client.post(
                LOGIN_URL,
                data={
                    "csrfmiddlewaretoken": csrf,
                    "username": creds.username,
                    "password": creds.password,
                    "next": "/",
                },
                headers={"Referer": LOGIN_URL},
            )
        # Django auth redirects to ``next`` on success; on failure it re-renders
        # the form with an error message inside the same 200 response.
        has_session = any(c.name == "sessionid" for c in client.cookies.jar)
        if resp.status_code in (200, 302) and has_session:
            self._authenticated = True
            return
        self._authenticated = False
        raise CredentialError(
            f"tolarie login failed (status {resp.status_code}, sessionid set: {has_session})"
        )
