from __future__ import annotations

import urllib.parse
from html import unescape

from selectolax.parser import HTMLParser, Node

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
        )
