from __future__ import annotations

import asyncio
import re
import urllib.parse
from html import unescape
from typing import Any

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
CART_URL = f"{BASE}/eshop/cart/"
# Cart-add endpoint discovered by post-login recon — the JS in /static/js/eshop.js
# wraps it as ``jQuery.getJSON(self.attr("data-url") + "?amount=" + …)``.
# ``data-url`` resolves to ``/eshop/cart/add-buy/<product_id>/`` per row.
CART_ADD_PATH = "/eshop/cart/add-buy/{product_id}/"
CART_REMOVE_PATH = "/eshop/cart/del-buy/{cart_item_id}/"

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
    supports_cart = True
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

    async def _ensure_auth(self) -> None:
        if self._authenticated:
            return
        async with self._auth_lock:
            if self._authenticated:
                return
            await self._login_locked()

    async def add_to_cart(self, shop_ref: str, count: int = 1) -> dict[str, Any]:
        """Add ``count`` of tolarie product ``shop_ref`` (numeric product id
        captured from ``js-add_to_cart_amount-<id>-card`` during search) to
        the cart.

        Hits the same JSON endpoint the in-page jQuery handler uses
        (``/eshop/cart/add-buy/<id>/?amount=N``). Auth is via the Django
        sessionid cookie set by ``login()``; the endpoint 302s anonymous
        callers to /accounts/login/, so we auto-relogin on that redirect.
        """
        if not shop_ref:
            raise ValueError("shop_ref is required (tolarie product id)")
        if not shop_ref.isdigit():
            raise ValueError(
                f"tolarie shop_ref must be a numeric product id, got {shop_ref!r}"
            )
        if count < 1:
            raise ValueError("count must be >= 1")
        await self._ensure_auth()
        client = await get_client()
        url = f"{BASE}{CART_ADD_PATH.format(product_id=shop_ref)}"
        async with host_slot("tolarie.cz"):
            resp = await client.get(
                url,
                params={"amount": str(count)},
                headers={"Referer": f"{BASE}/", "Accept": "application/json"},
                follow_redirects=False,
            )
        # Anonymous requests get 302 → /accounts/login/?next=…; if the cached
        # session expired server-side, retry exactly once after fresh login.
        if resp.status_code in (302, 303) and "/accounts/login/" in resp.headers.get(
            "location", ""
        ):
            self._authenticated = False
            await self.login()
            async with host_slot("tolarie.cz"):
                resp = await client.get(
                    url,
                    params={"amount": str(count)},
                    headers={"Referer": f"{BASE}/", "Accept": "application/json"},
                    follow_redirects=False,
                )
        if resp.status_code in (302, 303) and "/accounts/login/" in resp.headers.get(
            "location", ""
        ):
            raise CredentialError(
                "tolarie cart rejected even after re-login; check CZ_MTG_TOLARIE_* credentials"
            )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"status_code": resp.status_code, "product_id": shop_ref, "amount": count}

    async def view_cart(self) -> dict[str, Any]:
        """Return the HTML body of the cart page after logging in. Tolarie's
        cart page is server-rendered HTML; we don't try to parse it into a
        structured object — that's left for whichever caller wants line-item
        breakdowns.
        """
        await self._ensure_auth()
        client = await get_client()
        async with host_slot("tolarie.cz"):
            resp = await client.get(CART_URL, follow_redirects=False)
        if resp.status_code in (302, 303) and "/accounts/login/" in resp.headers.get(
            "location", ""
        ):
            self._authenticated = False
            await self.login()
            async with host_slot("tolarie.cz"):
                resp = await client.get(CART_URL, follow_redirects=False)
        resp.raise_for_status()
        items = self._parse_cart_items(resp.text)
        return {"items": items, "item_count": len(items), "url": CART_URL}

    async def clear_cart(self) -> dict[str, Any]:
        await self._ensure_auth()
        cart = await self.view_cart()
        items = cart.get("items", []) if isinstance(cart, dict) else []
        client = await get_client()
        removed = 0
        for item in items:
            cart_item_id = item.get("cart_item_id")
            if not cart_item_id:
                continue
            url = f"{BASE}{CART_REMOVE_PATH.format(cart_item_id=cart_item_id)}"
            async with host_slot("tolarie.cz"):
                resp = await client.get(
                    url,
                    headers={"Referer": CART_URL},
                    follow_redirects=False,
                )
            if resp.status_code in (200, 302, 303):
                removed += 1
                continue
            resp.raise_for_status()
        return {"removed_items": removed}

    @staticmethod
    def _parse_cart_items(html: str) -> list[dict[str, Any]]:
        """Best-effort scrape of the tolarie cart table. Returns one dict per
        line item with ``cart_item_id`` (from any ``del-buy/<id>/`` link) and
        the visible row text. Empty list if the cart is empty or the layout
        changed."""
        tree = HTMLParser(html)
        items: list[dict[str, Any]] = []
        for a in tree.css('a[href*="/eshop/cart/del-buy/"]'):
            href = (a.attributes.get("href") or "").strip()
            m = re.search(r"/eshop/cart/del-buy/(\d+)/", href)
            if not m:
                continue
            row = a
            for _ in range(6):  # walk up to enclosing <tr>
                row = row.parent
                if row is None:
                    break
                if (row.tag or "").lower() == "tr":
                    break
            row_text = (
                " ".join((row.text(separator=" ", strip=True) or "").split())
                if row is not None
                else ""
            )
            items.append(
                {
                    "cart_item_id": m.group(1),
                    "remove_url": href if href.startswith("http") else BASE + href,
                    "row_text": row_text,
                }
            )
        return items
