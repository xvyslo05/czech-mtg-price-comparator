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

BASE = "https://www.rishada.cz"
PAGE_SIZE = 100


class RishadaAdapter(ShopAdapter):
    """Adapter for rishada.cz — Czech custom-PHP MTG shop with a tabular search results page.

    Search results live at `/kusovky/vysledky-hledani?searchtype=basic&xxcardname=<name>`.
    Each offer is one `<tr>` with cells (in order):
        name | edition | mana | type | P/T | condition | rarity | price | stock | buy
    """

    shop_id = "rishada"
    base_url = BASE

    def _search_url(self, query: SearchQuery) -> str:
        params = {
            "searchtype": "basic",
            "xxcardname": query.name,
            "xxpagesize": str(PAGE_SIZE),
        }
        return f"{BASE}/kusovky/vysledky-hledani?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
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
            offer = self._parse_row(cells, url)
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

    def _parse_row(self, cells: list[Node], url: str) -> Offer | None:
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
        )
