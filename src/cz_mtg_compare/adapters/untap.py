from __future__ import annotations

import re
import urllib.parse
from html import unescape

from selectolax.parser import HTMLParser, Node

from ..http_client import get_client, host_slot
from ..models import Condition, Offer, SearchQuery
from ..normalize import normalize_condition, parse_price_czk
from .base import ShopAdapter

BASE = "https://untap.cz"

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
        )
