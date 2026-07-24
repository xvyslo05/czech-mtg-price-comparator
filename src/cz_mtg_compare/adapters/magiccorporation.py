from __future__ import annotations

import math
import re
import urllib.parse
from html import unescape

from selectolax.parser import HTMLParser, Node

from .. import fx
from ..filters import filter_playable
from ..http_client import get_client, host_slot
from ..models import Condition, Offer, SearchQuery
from .base import ShopAdapter

BASE = "https://boutique.magiccorporation.com"
DEFAULT_EUR_TO_CZK = fx.STATIC_DEFAULTS["EUR"]

_EUR_RE = re.compile(r"(\d[\d\s\u00a0.]*,\d{2})\s*€")
_SET_CODE_RE = re.compile(
    r"svgs\.scryfall\.io/sets/([a-z0-9]+)\.svg",
    re.IGNORECASE,
)
_VARIANTS = {"vo", "vf", "vo_foil", "vf_foil"}


class MagicCorporationAdapter(ShopAdapter):
    shop_id = "magiccorporation"
    base_url = BASE
    supports_login = False
    supports_cart = False
    supports_watchlist = False

    def __init__(self, *, eur_to_czk: float | None = None) -> None:
        self._eur_to_czk_override = eur_to_czk
        self._eur_to_czk = fx.rate_to_czk_nolive(
            "EUR",
            override=eur_to_czk,
        )

    def _search_url(self, query: SearchQuery) -> str:
        return f"{BASE}/recherche?{urllib.parse.urlencode({'q': query.name})}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        eur_to_czk = (
            self._eur_to_czk
            if self._eur_to_czk_override is not None
            else await fx.rate_to_czk("EUR")
        )
        client = await get_client()
        async with host_slot("boutique.magiccorporation.com"):
            response = await client.get(self._search_url(query))
        response.raise_for_status()
        return self._parse(response.text, query, eur_to_czk)

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        return self._parse(html, query, self._eur_to_czk)

    def _parse(
        self,
        html: str,
        query: SearchQuery,
        eur_to_czk: float,
    ) -> list[Offer]:
        tree = HTMLParser(html)
        wanted = query.name.casefold()
        edition_filter = (query.edition or "").strip().casefold() or None
        offers: list[Offer] = []

        for form in tree.css("form.js-cart-row"):
            row = self._ancestor_row(form)
            if row is None:
                continue
            type_input = form.css_first('input[name="type"]')
            if type_input is not None and (
                type_input.attributes.get("value") or ""
            ).strip().casefold() != "carte":
                continue

            name_node = row.css_first(
                'a[href*="/carte/"] span.block.text-xs.text-gray-500'
            )
            if name_node is None:
                name_node = row.css_first(
                    'a[href*="/carte/"] span.block.text-white'
                )
            card_name = (
                " ".join(unescape(name_node.text(strip=True)).split())
                if name_node is not None
                else ""
            )
            if not card_name or wanted not in card_name.casefold():
                continue

            edition_node = row.css_first('a[href*="/cartes/"]')
            edition: str | None = None
            if edition_node is not None:
                edition = " ".join(
                    unescape(
                        edition_node.attributes.get("title")
                        or edition_node.text(strip=True)
                    ).split()
                ) or None
            set_code_match = _SET_CODE_RE.search(row.html or "")
            set_code = set_code_match.group(1).upper() if set_code_match else None
            if edition_filter and (
                (
                    not edition
                    or edition_filter not in edition.casefold()
                )
                and (
                    not set_code
                    or edition_filter not in set_code.casefold()
                )
            ):
                continue

            detail_node = row.css_first('a[href*="/carte/"]')
            detail_url = (
                (detail_node.attributes.get("href") or "").strip()
                if detail_node is not None
                else ""
            )
            if not detail_url:
                continue
            product_input = form.css_first('input[name="id"]')
            product_id = (
                (product_input.attributes.get("value") or "").strip()
                if product_input is not None
                else ""
            )
            if not product_id:
                continue

            for option in form.css('select[name="variant"] option'):
                variant = (option.attributes.get("value") or "").strip().casefold()
                if variant not in _VARIANTS:
                    continue
                price_eur = self._parse_price_eur(option.text(strip=True))
                if price_eur is None:
                    continue
                try:
                    stock_qty = max(
                        0, int(option.attributes.get("data-max") or 0)
                    )
                except (TypeError, ValueError):
                    stock_qty = 0
                if query.in_stock_only and stock_qty <= 0:
                    continue
                offers.append(
                    Offer(
                        shop="magiccorporation",
                        card_name=card_name,
                        edition=edition,
                        set_code=set_code,
                        # Search results expose new copies only; detail pages label
                        # this stock Near Mint.
                        condition=Condition.NM,
                        language="EN" if variant.startswith("vo") else "FR",
                        foil=variant.endswith("_foil"),
                        price_czk=int(round(price_eur * eur_to_czk)),
                        price_native=price_eur,
                        currency="EUR",
                        stock_qty=stock_qty,
                        url=detail_url,
                        shop_ref=f"{product_id}:{variant}",
                    )
                )

        return offers if query.include_non_playable else filter_playable(offers)

    @staticmethod
    def _ancestor_row(node: Node) -> Node | None:
        current: Node | None = node
        while current is not None and current.tag != "tr":
            current = current.parent
        return current

    @staticmethod
    def _parse_price_eur(text: str) -> float | None:
        match = _EUR_RE.search(unescape(text))
        if match is None:
            return None
        raw = (
            match.group(1)
            .replace("\u00a0", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
        )
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if math.isfinite(value) and value >= 0 else None
