from __future__ import annotations

import re
import urllib.parse
from html import unescape

from selectolax.parser import HTMLParser, Node

from ..http_client import get_client, host_slot
from ..models import Condition, Offer, SearchQuery
from ..normalize import normalize_condition, parse_stock_qty
from .base import ShopAdapter

BASE = "https://www.blacklotus.cz"

_ALT_RE = re.compile(
    r"\(\s*Foil\s+(ANO|NE)\s*,\s*Stav\s+([^)]+?)\s*\)", re.IGNORECASE
)
_EDITION_RE = re.compile(r"z\s+edice\s+(.+?)\s*[.\n]", re.IGNORECASE)


class BlackLotusAdapter(ShopAdapter):
    shop_id = "blacklotus"
    base_url = BASE

    def _search_url(self, query: SearchQuery) -> str:
        params = {"string": query.name}
        return f"{BASE}/vyhledavani/?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        url = self._search_url(query)
        client = await get_client()
        async with host_slot("blacklotus.cz"):
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
        )
