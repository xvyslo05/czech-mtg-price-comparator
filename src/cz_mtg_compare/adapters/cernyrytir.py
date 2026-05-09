from __future__ import annotations

import re
import urllib.parse
from html import unescape

from selectolax.parser import HTMLParser, Node

from ..http_client import get_client, host_slot
from ..models import Condition, Offer, SearchQuery
from ..normalize import normalize_condition, parse_price_czk, parse_stock_qty
from .base import ShopAdapter

BASE = "https://www.cernyrytir.cz"
SEARCH_PATH = "/index.php3?akce=3"
PAGE_SIZE = 100  # poczob — most queries fit in a single page at 100 results.

# Cards with no real price (out of stock placeholders) are quoted at 9999 Kč.
SENTINEL_PRICE = 9999

# Cernyrytir encodes condition + foil as a textual suffix on the card name:
#   "Lightning Bolt", "Lightning Bolt - foil", "Lightning Bolt - lightly played",
#   "Lightning Bolt - foil - lightly played"
_SUFFIX_SPLIT_RE = re.compile(r"\s+-\s+")
_SET_ICON_RE = re.compile(r"/images/kusovky/([a-z0-9]+)\.gif", re.IGNORECASE)


class CernyRytirAdapter(ShopAdapter):
    shop_id = "cernyrytir"
    base_url = BASE

    def _request_url(self) -> str:
        return f"{BASE}{SEARCH_PATH}"

    def _result_url(self, query: SearchQuery) -> str:
        params = {
            "akce": "3",
            "jmenokarty": query.name,
            "edice_magic": "libovolna",
            "poczob": str(PAGE_SIZE),
            "triditpodle": "ceny",
            "hledej_pouze_magic": "1",
            "submit": "Vyhledej",
        }
        return f"{BASE}/index.php3?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        client = await get_client()
        async with host_slot("cernyrytir.cz"):
            resp = await client.post(
                self._request_url(),
                data={
                    "jmenokarty": query.name,
                    "edice_magic": "libovolna",
                    "poczob": str(PAGE_SIZE),
                    "triditpodle": "ceny",
                    "hledej_pouze_magic": "1",
                    "submit": "Vyhledej",
                },
            )
        resp.raise_for_status()
        # Site is windows-1250 encoded; httpx may guess wrong otherwise.
        resp.encoding = "windows-1250"
        return self._parse(resp.text, query)

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        return self._parse(html, query)

    def _parse(self, html: str, query: SearchQuery) -> list[Offer]:
        tree = HTMLParser(html)
        rows = tree.css("tr")
        offers: list[Offer] = []
        wanted = query.name.lower()
        edition_filter = (query.edition or "").lower().strip() or None

        # Each product spans 3 consecutive rows. The first row of a product carries a
        # td with rowspan="3" wrapping the card image; subsequent rows continue the same
        # bgcolor. We walk rows and group them by leading rowspan markers.
        i = 0
        n = len(rows)
        while i < n:
            row = rows[i]
            first_td = row.css_first("td")
            if first_td is None or (first_td.attributes.get("rowspan") or "").strip() != "3":
                i += 1
                continue
            group = [row, rows[i + 1] if i + 1 < n else None, rows[i + 2] if i + 2 < n else None]
            i += 3
            if any(g is None for g in group):
                continue
            offer = self._parse_group(group, query)  # type: ignore[arg-type]
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

    def _parse_group(self, rows: list[Node], query: SearchQuery) -> Offer | None:
        row1, row2, row3 = rows

        # ----- Row 1: name + foil/condition suffix -----
        bold = row1.css_first("font[style]")
        if bold is None:
            return None
        raw_name = unescape(bold.text(strip=True))
        if not raw_name:
            return None
        card_name, foil, condition = self._split_name_suffix(raw_name)
        # Default condition NM unless the suffix says otherwise.
        if condition is Condition.UNKNOWN:
            condition = Condition.NM

        # ----- Row 2: edition (icon + name) -----
        edition: str | None = None
        set_code: str | None = None
        for td in row2.css("td"):
            img = td.css_first("img[src]")
            if img is None:
                continue
            m = _SET_ICON_RE.search(img.attributes.get("src") or "")
            if m:
                set_code = m.group(1).upper()
                # text after the image is the edition name
                edition_text = unescape(td.text(strip=True))
                edition = " ".join(edition_text.split()) or None
                break

        # ----- Row 3: rarity, stock, price -----
        cells = row3.css("td")
        stock_qty = 0
        price_czk: int | None = None
        # The relevant cells contain a bold <font>; parse all and pick by content shape.
        for cell in cells:
            text = unescape(cell.text(separator=" ", strip=True))
            if "ks" in text and stock_qty == 0:
                stock_qty = parse_stock_qty(text)
            elif "Kč" in text and price_czk is None:
                price_czk = parse_price_czk(text)

        if price_czk is None:
            return None
        # Filter the 9999 Kč "ask us" placeholders if the user wants stocked offers.
        if price_czk >= SENTINEL_PRICE and stock_qty == 0 and query.in_stock_only:
            return None

        return Offer(
            shop="cernyrytir",
            card_name=card_name,
            edition=edition,
            set_code=set_code,
            condition=condition,
            language=None,
            foil=foil,
            price_czk=price_czk,
            stock_qty=stock_qty,
            url=self._result_url(query),
        )

    @staticmethod
    def _split_name_suffix(raw: str) -> tuple[str, bool, Condition]:
        # Suffixes are joined by " - ": "Name", "Name - foil", "Name - foil - lightly played"
        parts = [p.strip() for p in _SUFFIX_SPLIT_RE.split(raw) if p.strip()]
        if not parts:
            return raw, False, Condition.UNKNOWN
        name = parts[0]
        foil = False
        condition = Condition.UNKNOWN
        for token in parts[1:]:
            low = token.lower()
            if low == "foil":
                foil = True
                continue
            cond = normalize_condition(token)
            if cond is not Condition.UNKNOWN:
                condition = cond
            else:
                # Unrecognized suffix — fold it back into the name.
                name = f"{name} - {token}"
        return name, foil, condition
