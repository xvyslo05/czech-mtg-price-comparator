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
from ..normalize import normalize_condition, parse_price_czk
from .base import ShopAdapter

BASE = "https://untap.cz"
# Prestashop's Czech localized URLs. URL-encoded explicitly so the URL is
# stable regardless of how httpx normalizes the path.
LOGIN_URL = f"{BASE}/{urllib.parse.quote('přihlásit')}?back=my-account"
ACCOUNT_URL = f"{BASE}/muj-ucet"
CART_URL = f"{BASE}/kosik"

_PRODUCT_ID_FROM_URL_RE = re.compile(r"/(\d+)-[a-z0-9-]+\.html")
# Matches both the bare-property form ``static_token: 'X'`` (JS object) and the
# quoted-property form ``"static_token": "X"`` (JSON snippets).
_STATIC_TOKEN_RE = re.compile(r"""static_token['"]?\s*[:=]\s*['"]([0-9a-f]+)['"]""")

# untap.cz is a Prestashop instance. Each search result is an <article
# class="product-miniature"> with a `data-id-product` attribute. The product
# reference (visible as `<div class="product-reference">`) encodes
# set + collector number + condition/foil suffix, e.g. "M10#146#N" or
# "PTC#GB208#F". The trailing letter is the variant marker.
_REFERENCE_RE = re.compile(r"#([A-Z]{1,3})\s*$")
_RARITY_RE = re.compile(r"^\s*\[([^\]]+)\]")

_VARIANT_TO_CONDITION_FOIL: dict[str, tuple[Condition, bool]] = {
    "N":  (Condition.NM, False),
    "F":  (Condition.NM, True),
    "EX": (Condition.EX, False),
    "EXF": (Condition.EX, True),
    "LP": (Condition.LP, False),
    "LPF": (Condition.LP, True),
    "PL": (Condition.PL, False),
    "PLF": (Condition.PL, True),
    "MP": (Condition.PL, False),
    "MPF": (Condition.PL, True),
    "HP": (Condition.HP, False),
    "HPF": (Condition.HP, True),
}


class UntapAdapter(ShopAdapter):
    """Adapter for untap.cz — Prestashop-based MTG shop.

    Search URL: ``/vyhledavani?search_query=<name>``.

    Each `<article class="product-miniature">` is one offer. Condition + foil
    are encoded as a trailing letter on the product reference (e.g. ``M10#146#N``
    for NM non-foil, ``M10#146#F`` for NM foil).
    """

    shop_id = "untap"
    base_url = BASE
    supports_login = True
    supports_cart = True
    supports_watchlist = False

    def __init__(self) -> None:
        self._authenticated = False
        self._auth_lock = asyncio.Lock()
        self._static_token: str | None = None

    def _search_url(self, query: SearchQuery) -> str:
        params = {"search_query": query.name, "submit_search": "Hledat"}
        return f"{BASE}/vyhledavani?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        url = self._search_url(query)
        client = await get_client()
        async with host_slot("untap.cz"):
            resp = await client.get(url)
        resp.raise_for_status()
        return self._parse(resp.text, query)

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        return self._parse(html, query)

    def _parse(self, html: str, query: SearchQuery) -> list[Offer]:
        tree = HTMLParser(html)
        offers: list[Offer] = []
        wanted = query.name.lower()
        edition_filter = (query.edition or "").lower().strip() or None

        for article in tree.css("article.product-miniature"):
            offer = self._parse_article(article)
            if offer is None:
                continue
            if wanted not in offer.card_name.lower():
                continue
            if edition_filter and (
                (not offer.edition or edition_filter not in offer.edition.lower())
                and (not offer.set_code or edition_filter not in offer.set_code.lower())
            ):
                continue
            if query.in_stock_only and offer.stock_qty <= 0:
                continue
            offers.append(offer)
        return offers

    def _parse_article(self, article: Node) -> Offer | None:
        # Name + URL
        name_anchor = article.css_first("h2.product-name a")
        if name_anchor is None:
            return None
        card_name = " ".join(unescape(name_anchor.text(strip=True)).split())
        if not card_name:
            return None
        # untap appends " - Foil" to the visible name for foil variants. Strip it
        # — foil status is already encoded in the product reference (parsed below).
        lower = card_name.lower()
        for marker in (" - foil", " (foil)"):
            if lower.endswith(marker):
                card_name = card_name[: -len(marker)].rstrip()
                break
        href = (name_anchor.attributes.get("href") or "").strip()
        url = href if href.startswith("http") else (BASE + href if href.startswith("/") else BASE)

        # Price — the schema-like `content` attribute is the cleanest source
        # because it strips formatting/currency.
        price_czk: int | None = None
        price_node = article.css_first("span.product-price")
        if price_node is not None:
            content_attr = price_node.attributes.get("content")
            if content_attr:
                try:
                    price_czk = int(round(float(content_attr)))
                except (TypeError, ValueError):
                    price_czk = None
            if price_czk is None:
                price_czk = parse_price_czk(unescape(price_node.text(strip=True)))
        if price_czk is None:
            return None

        # Stock: Prestashop renders an "unavailable" badge for sold-out items.
        # If the article carries product-unavailable, treat as stock_qty=0.
        unavailable_node = article.css_first(".product-unavailable")
        # Vyprodáno = sold out (Czech)
        out_of_stock = unavailable_node is not None or "Vyprodáno" in article.text()
        stock_qty = 0 if out_of_stock else 1  # untap doesn't expose exact counts in listings

        # Reference / variant suffix → condition + foil
        condition = Condition.UNKNOWN
        foil = False
        set_code: str | None = None
        ref_node = article.css_first(".product-reference")
        if ref_node is not None:
            ref = unescape(ref_node.text(strip=True))
            parts = ref.split("#")
            if len(parts) >= 2:
                set_code = parts[0].strip().upper() or None
            m = _REFERENCE_RE.search(ref)
            if m:
                token = m.group(1).upper()
                cond_foil = _VARIANT_TO_CONDITION_FOIL.get(token)
                if cond_foil is not None:
                    condition, foil = cond_foil
        if condition is Condition.UNKNOWN:
            # Default — Prestashop listings don't always include condition; assume NM.
            condition = Condition.NM

        # Edition — the second feature paragraph (the first one is the rarity bracket).
        edition: str | None = None
        for feature in article.css(".product-features p.feature-value"):
            text = unescape(feature.text(strip=True))
            if not text:
                continue
            if _RARITY_RE.match(text):
                continue  # this is the [common]/[rare] tag
            edition = " ".join(text.split())
            break

        # id_product is exposed either as data-id-product on the article or
        # encoded in the detail URL as ``/<id>-<slug>.html``. Try attribute
        # first, fall back to URL parsing.
        shop_ref = (article.attributes.get("data-id-product") or "").strip() or None
        if not shop_ref:
            m = _PRODUCT_ID_FROM_URL_RE.search(url)
            if m:
                shop_ref = m.group(1)

        return Offer(
            shop="untap",
            card_name=card_name,
            edition=edition,
            set_code=set_code,
            condition=condition,
            language=None,
            foil=foil,
            price_czk=price_czk,
            stock_qty=stock_qty,
            url=url,
            shop_ref=shop_ref,
        )

    # --- Account features ---------------------------------------------------

    async def _fetch_static_token(self) -> str:
        """Prestashop wraps form POSTs in a per-session ``static_token`` CSRF
        which is exposed inline on every rendered page. Fetch the homepage
        and grep it out."""
        client = await get_client()
        async with host_slot("untap.cz"):
            resp = await client.get(f"{BASE}/")
        resp.raise_for_status()
        m = _STATIC_TOKEN_RE.search(resp.text)
        if not m:
            raise CredentialError(
                "untap: couldn't locate static_token on the homepage — "
                "Prestashop template may have changed"
            )
        token = m.group(1)
        self._static_token = token
        return token

    async def login(self) -> None:
        async with self._auth_lock:
            await self._login_locked()

    async def _login_locked(self) -> None:
        creds = credentials_for("untap")
        if creds is None:
            raise CredentialError(
                "untap credentials not configured "
                "(set CZ_MTG_UNTAP_USER and CZ_MTG_UNTAP_PASS)"
            )
        client = await get_client()
        async with host_slot("untap.cz"):
            resp = await client.post(
                LOGIN_URL,
                data={
                    "back": "my-account",
                    "email": creds.username,
                    "password": creds.password,
                    "submitLogin": "1",
                },
                headers={"Referer": LOGIN_URL},
                follow_redirects=False,
            )
        # Prestashop returns 302 to /muj-ucet on success and re-renders the
        # form with an alert on failure. The "PrestaShop-<hash>" cookie is set
        # after login and is the cleanest auth signal.
        cookie_names = {c.name for c in client.cookies.jar}
        if any(name.startswith("PrestaShop") for name in cookie_names) and resp.status_code in (
            200,
            302,
            303,
        ):
            # Verify by fetching the account page — if Prestashop sends us
            # back to /přihlásit it means the cookie isn't actually authed.
            async with host_slot("untap.cz"):
                check = await client.get(ACCOUNT_URL, follow_redirects=False)
            location = check.headers.get("location", "")
            if check.status_code == 200 and "p%c5%99ihl" not in location.lower():
                self._authenticated = True
                # Refresh static_token after login (Prestashop rotates it).
                await self._fetch_static_token()
                return
        self._authenticated = False
        raise CredentialError(
            f"untap login failed (status {resp.status_code}); check CZ_MTG_UNTAP_* credentials"
        )

    async def _ensure_auth(self) -> None:
        if self._authenticated:
            return
        async with self._auth_lock:
            if self._authenticated:
                return
            await self._login_locked()

    async def _ensure_token(self) -> str:
        if self._static_token:
            return self._static_token
        return await self._fetch_static_token()

    async def add_to_cart(self, shop_ref: str, count: int = 1) -> dict[str, Any]:
        """Add ``count`` of untap product ``shop_ref`` (Prestashop id_product)
        to the cart. Logs in automatically on first call.
        """
        if not shop_ref:
            raise ValueError("shop_ref is required (untap id_product)")
        if count < 1:
            raise ValueError("count must be >= 1")
        await self._ensure_auth()
        token = await self._ensure_token()
        client = await get_client()
        async with host_slot("untap.cz"):
            resp = await client.post(
                CART_URL,
                data={
                    "token": token,
                    "id_product": shop_ref,
                    "id_customization": "0",
                    "qty": str(count),
                    "add": "1",
                    "action": "update",
                },
                headers={"Referer": f"{BASE}/", "X-Requested-With": "XMLHttpRequest"},
            )
        # Prestashop rotates static_token if the session changes — retry once
        # with a freshly fetched token on 403 / token-rejection responses.
        if resp.status_code in (403, 401):
            self._static_token = None
            token = await self._ensure_token()
            async with host_slot("untap.cz"):
                resp = await client.post(
                    CART_URL,
                    data={
                        "token": token,
                        "id_product": shop_ref,
                        "id_customization": "0",
                        "qty": str(count),
                        "add": "1",
                        "action": "update",
                    },
                    headers={"Referer": f"{BASE}/", "X-Requested-With": "XMLHttpRequest"},
                )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"status_code": resp.status_code, "id_product": shop_ref, "qty": count}

    async def view_cart(self) -> dict[str, Any]:
        await self._ensure_auth()
        client = await get_client()
        async with host_slot("untap.cz"):
            resp = await client.get(
                CART_URL,
                params={"action": "show"},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    async def clear_cart(self) -> dict[str, Any]:
        """Remove every item from the untap cart. Prestashop removes a line by
        POSTing the same /kosik endpoint with ``op=down`` (or ``delete=1`` on
        older templates) and the cart line id. We pull the current contents
        and zero each line out via ``qty=0``.
        """
        await self._ensure_auth()
        cart = await self.view_cart()
        items: list[dict[str, Any]] = []
        if isinstance(cart, dict):
            payload = cart.get("cart", cart)
            for key in ("products", "items"):
                value = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(value, list):
                    items = value
                    break
        token = await self._ensure_token()
        client = await get_client()
        removed = 0
        for item in items:
            id_product = item.get("id_product") or item.get("idProduct")
            if id_product is None:
                continue
            async with host_slot("untap.cz"):
                resp = await client.post(
                    CART_URL,
                    data={
                        "token": token,
                        "id_product": str(id_product),
                        "id_customization": str(item.get("id_customization", 0) or 0),
                        "qty": "1",
                        "delete": "1",
                        "action": "update",
                    },
                    headers={"Referer": f"{BASE}/", "X-Requested-With": "XMLHttpRequest"},
                )
            if resp.status_code in (200, 302, 303):
                removed += 1
                continue
            resp.raise_for_status()
        return {"removed_items": removed}
