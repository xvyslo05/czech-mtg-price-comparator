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
from ..normalize import normalize_condition, parse_stock_qty
from .base import ShopAdapter

BASE = "https://www.blacklotus.cz"
LOGIN_URL = f"{BASE}/action/Customer/Login/"
CART_ADD_URL = f"{BASE}/action/Cart/addCartItem/"
CART_CONTENT_URL = f"{BASE}/action/Cart/GetCartContent/"
CART_DELETE_URL = f"{BASE}/action/Cart/deleteCartItem/"

_ALT_RE = re.compile(
    r"\(\s*Foil\s+(ANO|NE)\s*,\s*Stav\s+([^)]+?)\s*\)", re.IGNORECASE
)
_EDITION_RE = re.compile(r"z\s+edice\s+(.+?)\s*[.\n]", re.IGNORECASE)
# On the product detail page, Shoptet emits a gtag view_item event whose
# `variant` field encodes the same data: "Foil: ANO|NE, Stav: <condition>".
_DETAIL_VARIANT_RE = re.compile(
    r'"variant"\s*:\s*"(?:[^"]*~)?Foil:\s*(ANO|NE)\s*,\s*Stav:\s*([^"]+?)"',
    re.IGNORECASE,
)
_DETAIL_META_DESC_RE = re.compile(
    r'<meta\s+name="description"\s+content="([^"]+)"', re.IGNORECASE
)


class BlackLotusAdapter(ShopAdapter):
    shop_id = "blacklotus"
    base_url = BASE
    supports_login = True
    supports_cart = True
    supports_watchlist = False

    def __init__(self, *, enrich_detail: bool = True) -> None:
        self._enrich_detail = enrich_detail
        self._authenticated = False
        self._auth_lock = asyncio.Lock()

    def _search_url(self, query: SearchQuery) -> str:
        params = {"string": query.name}
        return f"{BASE}/vyhledavani/?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        url = self._search_url(query)
        client = await get_client()
        async with host_slot("blacklotus.cz"):
            resp = await client.get(url)
        resp.raise_for_status()
        offers = self._parse(resp.text, query)
        if self._enrich_detail:
            await self._enrich_offers(offers)
        return offers

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        return self._parse(html, query)

    async def _enrich_offers(self, offers: list[Offer]) -> None:
        """Follow detail URLs for offers where the listing didn't expose condition.

        The detail page carries a gtag view_item event with the exact variant
        ("Foil: ANO/NE, Stav: <cond>") plus the edition in the meta description.
        """
        targets = [
            o for o in offers
            if o.condition is Condition.UNKNOWN or o.edition is None
        ]
        if not targets:
            return
        client = await get_client()

        async def fetch_one(offer: Offer) -> None:
            try:
                async with host_slot("blacklotus.cz"):
                    resp = await client.get(offer.url)
                resp.raise_for_status()
                self._apply_detail(offer, resp.text)
            except Exception:  # noqa: BLE001 — enrichment is best-effort
                return

        await asyncio.gather(*(fetch_one(o) for o in targets))

    @staticmethod
    def _apply_detail(offer: Offer, html: str) -> None:
        m = _DETAIL_VARIANT_RE.search(html)
        if m:
            if offer.condition is Condition.UNKNOWN:
                offer.condition = normalize_condition(m.group(2))
            # Listing's foil flag wins when set; otherwise take detail page's.
            if not offer.foil:
                offer.foil = m.group(1).strip().upper() == "ANO"
        if offer.edition is None:
            md = _DETAIL_META_DESC_RE.search(html)
            if md:
                em = _EDITION_RE.search(unescape(md.group(1)))
                if em:
                    offer.edition = " ".join(em.group(1).split())

    def _parse(self, html: str, query: SearchQuery) -> list[Offer]:
        tree = HTMLParser(html)
        offers: list[Offer] = []
        wanted = query.name.lower()
        edition_filter = (query.edition or "").lower().strip() or None

        for product in tree.css('div.product div.p[data-micro="product"]'):
            offer = self._parse_product(product)
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

    def _parse_product(self, product: Node) -> Offer | None:
        # Name
        name_node = product.css_first('[data-micro="name"]')
        if name_node is None:
            return None
        card_name = " ".join(unescape(name_node.text(strip=True)).split())
        if not card_name:
            return None

        # Price (schema.org microdata; dot decimal)
        offer_node = product.css_first('[data-micro="offer"]')
        price_attr = (offer_node.attributes.get("data-micro-price") if offer_node else None) or ""
        price_czk: int | None
        try:
            price_czk = int(round(float(price_attr))) if price_attr else None
        except ValueError:
            price_czk = None
        if price_czk is None:
            return None

        # Availability — Shoptet exposes both schema.org and a stock-amount text
        availability_attr = (
            offer_node.attributes.get("data-micro-availability") if offer_node else None
        ) or ""
        in_stock_schema = "InStock" in availability_attr

        amount_node = product.css_first("span.availability-amount")
        amount_text = unescape(amount_node.text(strip=True)) if amount_node else ""
        stock_qty = parse_stock_qty(amount_text)
        if stock_qty == 0 and in_stock_schema:
            # Stock amount missing but schema says it's in stock — assume 1.
            stock_qty = 1
        if stock_qty > 0 and not in_stock_schema:
            # Conflicting; trust the schema.org marker.
            stock_qty = 0

        # Foil + condition from image alt: "Lightning Bolt (Foil ANO|NE, Stav <cond>)"
        foil = False
        condition = Condition.UNKNOWN
        img_node = product.css_first("img[alt]")
        if img_node is not None:
            alt = img_node.attributes.get("alt") or ""
            m = _ALT_RE.search(alt)
            if m:
                foil = m.group(1).strip().upper() == "ANO"
                condition = normalize_condition(m.group(2))

        # Edition from short description "Kusová karta z edice <NAME>."
        edition: str | None = None
        desc_node = product.css_first('[data-micro="description"]')
        if desc_node is not None:
            desc = unescape(desc_node.text(strip=True))
            m = _EDITION_RE.search(desc)
            if m:
                edition = " ".join(m.group(1).split())

        # Product detail URL
        url = BASE
        link_node = product.css_first('a[data-micro="url"]') or product.css_first("a.image")
        if link_node is not None:
            href = (link_node.attributes.get("href") or "").strip()
            if href.startswith("/"):
                url = BASE + href
            elif href:
                url = href

        # Per-row addCartItem form carries the priceId we need for cart calls.
        # On older Shoptet templates the form lives on the detail page only and
        # the search result has just the product link — in that case shop_ref
        # stays None and add_to_cart will tell the user to re-search.
        shop_ref: str | None = None
        price_input = product.css_first('input[name="priceId"]')
        if price_input is not None:
            shop_ref = (price_input.attributes.get("value") or "").strip() or None

        return Offer(
            shop="blacklotus",
            card_name=card_name,
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
        creds = credentials_for("blacklotus")
        if creds is None:
            raise CredentialError(
                "blacklotus credentials not configured "
                "(set CZ_MTG_BLACKLOTUS_USER and CZ_MTG_BLACKLOTUS_PASS)"
            )
        client = await get_client()
        # blacklotus's Shoptet installation reports shoptet.csrf.enabled = false
        # on its home page, so no CSRF token is required on the Login POST.
        # The form fields visible on /login/ are: email, password, hidden
        # ``referer`` (back-target after redirect) and a honeypot ``surname``
        # which must be empty.
        async with host_slot("blacklotus.cz"):
            resp = await client.post(
                LOGIN_URL,
                data={
                    "email": creds.username,
                    "password": creds.password,
                    "referer": "",
                    "surname": "",
                },
                headers={"Referer": f"{BASE}/login/"},
                follow_redirects=False,
            )
        # Shoptet returns 302 to /klient/ on success, or 302 back to /login/
        # with a flashmessage cookie on failure. The cleanest signal that the
        # session is now logged in is the presence of a Shoptet customer cookie.
        cookie_names = {c.name for c in client.cookies.jar}
        if any(name.startswith("logged-") or name == "customer-id" for name in cookie_names):
            self._authenticated = True
            return
        # Fall back: a successful Shoptet login also redirects to a customer
        # area URL rather than back to /login/.
        loc = resp.headers.get("location", "")
        if resp.status_code in (302, 303) and "/login" not in loc:
            self._authenticated = True
            return
        self._authenticated = False
        raise CredentialError(
            f"blacklotus login failed (status {resp.status_code}, redirect to "
            f"{loc or '<none>'}); double-check CZ_MTG_BLACKLOTUS_* credentials"
        )

    async def _ensure_auth(self) -> None:
        if self._authenticated:
            return
        async with self._auth_lock:
            if self._authenticated:
                return
            await self._login_locked()

    async def add_to_cart(self, shop_ref: str, count: int = 1) -> dict[str, Any]:
        """Add ``count`` of blacklotus ``shop_ref`` (Shoptet priceId) to the cart.

        ``shop_ref`` is the priceId captured during ``search_card``. Logs in
        automatically on first call; re-logs in transparently on 401.
        """
        if not shop_ref:
            raise ValueError(
                "shop_ref is required (blacklotus priceId). If the offer is "
                "missing it, blacklotus's search-result HTML didn't expose a "
                "per-row cart form — re-run search_card so the priceId is "
                "captured, or use the detail-page URL."
            )
        if count < 1:
            raise ValueError("count must be >= 1")
        await self._ensure_auth()
        client = await get_client()
        async with host_slot("blacklotus.cz"):
            resp = await client.post(
                CART_ADD_URL,
                data={
                    "language": "cs",
                    "priceId": shop_ref,
                    "amount": str(count),
                },
                headers={"Referer": f"{BASE}/"},
                follow_redirects=False,
            )
        if resp.status_code in (401, 403):
            self._authenticated = False
            # Retry once after a fresh login.
            await self.login()
            async with host_slot("blacklotus.cz"):
                resp = await client.post(
                    CART_ADD_URL,
                    data={
                        "language": "cs",
                        "priceId": shop_ref,
                        "amount": str(count),
                    },
                    headers={"Referer": f"{BASE}/"},
                    follow_redirects=False,
                )
        if resp.status_code in (401, 403):
            raise CredentialError(
                f"blacklotus cart rejected even after re-login "
                f"(status {resp.status_code}); check credentials"
            )
        # Shoptet returns 302 → / on success. Treat 200/302 as OK.
        if resp.status_code not in (200, 302, 303):
            resp.raise_for_status()
        return {
            "status_code": resp.status_code,
            "priceId": shop_ref,
            "amount": count,
        }

    async def view_cart(self) -> dict[str, Any]:
        await self._ensure_auth()
        client = await get_client()
        async with host_slot("blacklotus.cz"):
            resp = await client.get(
                CART_CONTENT_URL,
                headers={"Accept": "application/json", "Referer": f"{BASE}/"},
            )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    async def clear_cart(self) -> dict[str, Any]:
        """Remove every item from the blacklotus cart.

        Shoptet's ``deleteCartItem`` takes a ``cartItemId`` per call, sourced
        from the cart content payload. We iterate over all items.
        """
        await self._ensure_auth()
        cart = await self.view_cart()
        # Shoptet's GetCartContent payload nests items differently across
        # template versions; try the common keys defensively.
        items: list[dict[str, Any]] = []
        if isinstance(cart, dict):
            payload = cart.get("payload") if isinstance(cart.get("payload"), dict) else cart
            for key in ("cartItems", "items"):
                value = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(value, list):
                    items = value
                    break
        client = await get_client()
        removed = 0
        for item in items:
            item_id = item.get("itemId") or item.get("id") or item.get("cartItemId")
            if item_id is None:
                continue
            async with host_slot("blacklotus.cz"):
                resp = await client.post(
                    CART_DELETE_URL,
                    data={"cartItemId": str(item_id)},
                    headers={"Referer": f"{BASE}/kosik/"},
                    follow_redirects=False,
                )
            if resp.status_code in (200, 302, 303):
                removed += 1
                continue
            resp.raise_for_status()
        return {"removed_items": removed}
