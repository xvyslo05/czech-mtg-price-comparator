from __future__ import annotations

import asyncio
import re
import urllib.parse
from html import unescape
from typing import Any

from selectolax.parser import HTMLParser, Node

from ..credentials import CredentialError, credentials_for, has_credentials
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
CART_URL = f"{BASE}/nakupni-kosik"
PAGE_SIZE = 100

# Server-side action codes posted alongside cardid / itemid. Reverse-engineered
# from the logged-in result row form (``act=20005`` on the per-row sellform)
# and the cart page "odstranit" links (``act=20032``).
_ACT_ADD = "20005"
_ACT_REMOVE = "20032"

_CART_ITEM_HREF_RE = re.compile(r"itemid=(\d+).*?act=20032")


class RishadaAdapter(ShopAdapter):
    """Adapter for rishada.cz — Czech custom-PHP MTG shop with a tabular search results page.

    Search results live at `/kusovky/vysledky-hledani?searchtype=basic&xxcardname=<name>`.
    Each offer is one `<tr>` with cells (in order):
        name | edition | mana | type | P/T | condition | rarity | price | stock | buy
    """

    shop_id = "rishada"
    base_url = BASE
    supports_login = True
    supports_cart = True
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
        # Auto-login when credentials are configured so the per-row cart form
        # (and its hidden ``cardid`` input that backs ``Offer.shop_ref``) is
        # included in the rendered HTML. Anonymous search still works, it just
        # can't surface shop_refs because rishada hides the form behind a
        # ``notLogged()`` stub for unauthenticated visitors.
        if has_credentials("rishada"):
            await self._ensure_auth()
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
            offer = self._parse_row(row, cells, url)
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

    def _parse_row(self, row: Node, cells: list[Node], url: str) -> Offer | None:
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

        # Cart cardid lives in ``<input type="hidden" name="cardid" value="...">``
        # inside the per-row ``<form id="sellformXXXX">``. Only logged-in rows
        # render the form; anonymous rows expose a stub button that triggers
        # ``notLogged()``. shop_ref stays None when no cardid is present.
        shop_ref: str | None = None
        cardid_input = row.css_first('input[name="cardid"]')
        if cardid_input is not None:
            value = (cardid_input.attributes.get("value") or "").strip()
            if value.isdigit():
                shop_ref = value

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
            shop_ref=shop_ref,
        )

    # --- Account features ---------------------------------------------------

    async def login(self) -> None:
        async with self._auth_lock:
            await self._login_locked()

    async def _ensure_auth(self) -> None:
        if self._authenticated:
            return
        async with self._auth_lock:
            if self._authenticated:
                return
            await self._login_locked()

    async def _login_locked(self) -> None:
        creds = credentials_for("rishada")
        if creds is None:
            raise CredentialError(
                "rishada credentials not configured "
                "(set CZ_MTG_RISHADA_USER and CZ_MTG_RISHADA_PASS)"
            )
        client = await get_client()
        # rishada renders a tiny login form in the global sidebar with hidden
        # input ``dologin=1``. POST to / with login-name + login-pass + dologin=1.
        # An empty ``dologin`` is silently ignored (the server returns 200 but
        # never authenticates), so the value must match what the form renders.
        async with host_slot("rishada.cz"):
            resp = await client.post(
                LOGIN_URL,
                data={
                    "login-name": creds.username,
                    "login-pass": creds.password,
                    "dologin": "1",
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

    async def add_to_cart(self, shop_ref: str, count: int = 1) -> dict[str, Any]:
        """Add ``count`` copies of rishada card ``shop_ref`` (numeric cardid
        captured during ``search()``) to the logged-in user's cart.

        Mimics the per-row ``<form id="sellformN">`` submit: POST ``cardid``,
        ``sell``, ``act=20005`` to the site. The form's ``action`` is the
        current search-results URL but the server-side handler only inspects
        ``act`` and ``cardid``, so we post to ``/`` for stability. ``max`` is
        a client-side validation hint and is intentionally omitted.
        """
        if not shop_ref:
            raise ValueError("shop_ref is required (rishada cardid)")
        if not shop_ref.isdigit():
            raise ValueError(
                f"rishada shop_ref must be a numeric cardid, got {shop_ref!r}"
            )
        if count < 1:
            raise ValueError("count must be >= 1")
        await self._ensure_auth()
        client = await get_client()
        async with host_slot("rishada.cz"):
            resp = await client.post(
                LOGIN_URL,
                data={"act": _ACT_ADD, "cardid": shop_ref, "sell": str(count)},
                headers={"Referer": LOGIN_URL},
                follow_redirects=False,
            )
        # rishada's add-cart handler doesn't surface a structured success/error
        # response — it re-renders the surrounding page. The cart-summary anchor
        # in the sidebar is the only reliable signal that an item was accepted.
        # If the user got logged out server-side between login() and now, the
        # sidebar will instead render the login form; retry exactly once.
        if 'id="login-form"' in resp.text:
            self._authenticated = False
            await self.login()
            async with host_slot("rishada.cz"):
                resp = await client.post(
                    LOGIN_URL,
                    data={"act": _ACT_ADD, "cardid": shop_ref, "sell": str(count)},
                    headers={"Referer": LOGIN_URL},
                    follow_redirects=False,
                )
        resp.raise_for_status()
        summary = self._parse_cart_summary(resp.text)
        return {
            "cardid": shop_ref,
            "count": count,
            "cart_total_czk": summary.get("total_czk"),
            "cart_item_count": summary.get("item_count"),
        }

    async def view_cart(self) -> dict[str, Any]:
        """Fetch ``/nakupni-kosik`` and return parsed cart contents."""
        await self._ensure_auth()
        client = await get_client()
        async with host_slot("rishada.cz"):
            resp = await client.get(CART_URL, follow_redirects=False)
        if 'id="login-form"' in resp.text:
            self._authenticated = False
            await self.login()
            async with host_slot("rishada.cz"):
                resp = await client.get(CART_URL, follow_redirects=False)
        resp.raise_for_status()
        items = self._parse_cart_items(resp.text)
        summary = self._parse_cart_summary(resp.text)
        return {
            "items": items,
            "item_count": summary.get("item_count", len(items)),
            "total_czk": summary.get("total_czk"),
            "url": CART_URL,
        }

    async def clear_cart(self) -> dict[str, Any]:
        """Remove every line item by GETting each ``odstranit`` link."""
        await self._ensure_auth()
        cart = await self.view_cart()
        client = await get_client()
        removed = 0
        for item in cart.get("items", []):
            itemid = item.get("itemid")
            if not itemid:
                continue
            async with host_slot("rishada.cz"):
                resp = await client.get(
                    f"{CART_URL}?itemid={itemid}&act={_ACT_REMOVE}",
                    headers={"Referer": CART_URL},
                    follow_redirects=False,
                )
            if resp.status_code in (200, 302, 303):
                removed += 1
        return {"removed_items": removed}

    @staticmethod
    def _parse_cart_summary(html: str) -> dict[str, Any]:
        """Extract the sidebar's ``Košík: <total>,- Kč / <N> položek`` link.

        The label and value live in different DOM nodes
        (``<span class="bold">Košík: </span>105,- Kč ...``), so a regex against the
        raw HTML misses on the intervening tag. Parse the anchor and read its
        flattened text instead.
        """
        tree = HTMLParser(html)
        anchor = tree.css_first('a[href="/nakupni-kosik"]')
        if anchor is None:
            return {}
        text = unescape(anchor.text(separator=" ", strip=True))
        m = re.search(r"([\d ]+),-?\s*Kč\s*/\s*(\d+)\s*položek", text)
        if not m:
            return {}
        total = int(re.sub(r"\D", "", m.group(1)) or 0)
        return {"total_czk": total, "item_count": int(m.group(2))}

    @staticmethod
    def _parse_cart_items(html: str) -> list[dict[str, Any]]:
        """Parse cart line items from ``/nakupni-kosik``.

        Each row in the cart table holds a name link, a price cell, a line-item
        total cell, and an ``odstranit`` anchor whose href encodes the cart
        ``itemid``. The HTML is fairly minimal — we walk the items table and
        match by the presence of the remove anchor."""
        tree = HTMLParser(html)
        items: list[dict[str, Any]] = []
        for a in tree.css("a[href]"):
            href = a.attributes.get("href") or ""
            m = _CART_ITEM_HREF_RE.search(href)
            if not m:
                continue
            itemid = m.group(1)
            # Walk up to the containing <tr> so we can pull the row text.
            row = a.parent
            while row is not None and row.tag != "tr":
                row = row.parent
            name = None
            price_czk: int | None = None
            line_total_czk: int | None = None
            if row is not None:
                cells = row.css("td")
                if cells:
                    name = " ".join(
                        unescape(cells[0].text(separator=" ", strip=True)).split()
                    ) or None
                # Pull the two Kč cells, in document order.
                kc_values = []
                for cell in cells:
                    text = unescape(cell.text(strip=True))
                    if "Kč" in text:
                        parsed = parse_price_czk(text)
                        if parsed is not None:
                            kc_values.append(parsed)
                if len(kc_values) >= 1:
                    price_czk = kc_values[0]
                if len(kc_values) >= 2:
                    line_total_czk = kc_values[1]
            items.append(
                {
                    "itemid": itemid,
                    "name": name,
                    "price_czk": price_czk,
                    "line_total_czk": line_total_czk,
                }
            )
        return items
