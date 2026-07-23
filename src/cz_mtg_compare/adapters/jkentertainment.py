from __future__ import annotations

import json
import math
import os
import re
import urllib.parse
from html import unescape
from typing import Any

from selectolax.parser import HTMLParser, Node

from ..filters import filter_playable
from ..http_client import get_client, host_slot
from ..models import Offer, SearchQuery
from ..normalize import normalize_condition
from .base import ShopAdapter

BASE = "https://www.jk-entertainment.de"
DEFAULT_EUR_TO_CZK = 24.5

_DATA_LAYER_RE = re.compile(
    r"var\s+onEventDataLayer\s*=\s*JSON\.parse\('(.*?)'\);",
    re.DOTALL,
)
_NON_MTG_RE = re.compile(
    r"\b(?:YGO|Yu-?Gi-?Oh|Pok[eé]mon|One Piece|Digimon|Lorcana|"
    r"Flesh\s*and\s*Blood|Speed Duel|Weiss|Vanguard)\b",
    re.IGNORECASE,
)
_SHOP_REF_RE = re.compile(r"/detail/([0-9a-f]{32})(?:[/?#]|$)", re.IGNORECASE)
_LANGUAGES = {
    "EN": "English",
    "GE": "German",
    "FR": "French",
    "IT": "Italian",
    "SP": "Spanish",
    "PT": "Portuguese",
    "JP": "Japanese",
    "RU": "Russian",
    "KR": "Korean",
    "CN": "Chinese",
}


def _env_rate(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


class JkEntertainmentAdapter(ShopAdapter):
    shop_id = "jkentertainment"
    base_url = BASE
    supports_login = False
    supports_cart = False
    supports_watchlist = False

    def __init__(self, *, eur_to_czk: float | None = None) -> None:
        self._eur_to_czk = (
            eur_to_czk
            if eur_to_czk is not None
            else _env_rate("CZ_MTG_EUR_TO_CZK", DEFAULT_EUR_TO_CZK)
        )

    def _search_url(self, query: SearchQuery) -> str:
        return f"{BASE}/search?{urllib.parse.urlencode({'search': query.name})}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        client = await get_client()
        async with host_slot("jk-entertainment.de"):
            response = await client.get(self._search_url(query))
        response.raise_for_status()
        return await self.parse(response.text, query)

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        if not html:
            return []
        match = _DATA_LAYER_RE.search(html)
        if match is None:
            return []
        data = self._decode_data_layer(match.group(1))
        if not isinstance(data, dict) or data.get("event") != "view_item_list":
            return []
        ecommerce = data.get("ecommerce")
        items = ecommerce.get("items") if isinstance(ecommerce, dict) else None
        if not isinstance(items, list):
            return []

        tree = HTMLParser(html)
        anchors = tree.css("a.product-name")
        aligned = len(anchors) == len(items)
        search_url = self._search_url(query)
        wanted = query.name.casefold()
        edition_filter = (query.edition or "").strip().casefold() or None
        offers: list[Offer] = []

        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            product_card = (
                self._product_card(anchors[index]) if aligned else None
            )
            category = " ".join(
                unescape(str(item.get("item_category") or "")).split()
            )
            if _NON_MTG_RE.search(category):
                continue
            raw_name = " ".join(
                unescape(str(item.get("item_name") or "")).split()
            )
            if not raw_name:
                continue
            card_name, edition = self._split_name_and_edition(raw_name, category)
            dom_edition = self._labeled_characteristic(
                product_card,
                "Edition",
            )
            if dom_edition:
                edition = dom_edition
            if wanted not in card_name.casefold():
                continue
            if edition_filter and (
                not edition or edition_filter not in edition.casefold()
            ):
                continue

            id_parts = str(item.get("item_id") or "").split("#")
            if len(id_parts) < 4:
                continue
            try:
                price_eur = float(item.get("price"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(price_eur) or price_eur < 0:
                continue

            url = search_url
            shop_ref: str | None = None
            if aligned:
                href = (anchors[index].attributes.get("href") or "").strip()
                if href:
                    url = urllib.parse.urljoin(BASE, href)
                    ref_match = _SHOP_REF_RE.search(url)
                    if ref_match is not None:
                        shop_ref = ref_match.group(1)

            language_code = id_parts[2].upper()
            stock_qty = self._stock_qty(product_card)
            if query.in_stock_only and stock_qty <= 0:
                continue
            offers.append(
                Offer(
                    shop="jkentertainment",
                    card_name=card_name,
                    edition=edition,
                    set_code=None,
                    condition=normalize_condition(id_parts[1]),
                    language=_LANGUAGES.get(language_code, language_code or None),
                    foil=id_parts[3].upper() == "FO",
                    price_czk=int(round(price_eur * self._eur_to_czk)),
                    stock_qty=stock_qty,
                    url=url,
                    shop_ref=shop_ref,
                )
            )

        return offers if query.include_non_playable else filter_playable(offers)

    @staticmethod
    def _product_card(anchor: Node) -> Node | None:
        current: Node | None = anchor
        while current is not None:
            classes = (current.attributes.get("class") or "").split()
            if "product-box" in classes:
                return current
            current = current.parent
        return None

    @staticmethod
    def _labeled_characteristic(
        product_card: Node | None,
        label: str,
    ) -> str | None:
        if product_card is None:
            return None
        suffix = f"{label.casefold()}:"
        for option in product_card.css(
            ".product-variant-characteristics-option"
        ):
            previous = option.prev
            if previous is None:
                continue
            previous_text = " ".join(
                unescape(previous.text(strip=True)).split()
            )
            if not previous_text.casefold().endswith(suffix):
                continue
            value = " ".join(unescape(option.text(strip=True)).split())
            return value or None
        return None

    @staticmethod
    def _stock_qty(product_card: Node | None) -> int:
        if product_card is None:
            return 1
        quantity = product_card.css_first(
            "input.product-detail-quantity-input"
        )
        if quantity is None:
            return 1
        try:
            return max(0, int(quantity.attributes.get("max") or ""))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _decode_data_layer(raw: str) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                decoded = raw.encode("utf-8").decode("unicode_escape")
                return json.loads(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None

    @staticmethod
    def _split_name_and_edition(
        raw_name: str,
        category: str,
    ) -> tuple[str, str | None]:
        if " - " in raw_name:
            name, edition = raw_name.rsplit(" - ", 1)
            return name.strip(), edition.strip() or None
        category_root = category.split("#", 1)[0].strip()
        if category_root and not category_root.casefold().startswith("mtg"):
            return raw_name.strip(), category_root
        return raw_name.strip(), None
