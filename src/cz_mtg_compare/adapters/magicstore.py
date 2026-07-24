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

BASE = "https://www.magicstore.it"
DEFAULT_EUR_TO_CZK = fx.STATIC_DEFAULTS["EUR"]
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_PRICE_RE = re.compile(r"(\d[\d.,]*)")
_STOCK_RE = re.compile(r"\((\d+)\)")


class MagicStoreAdapter(ShopAdapter):
    shop_id = "magicstore"
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
        params = {"q": query.name, "id_cat": 9}
        return f"{BASE}/ricerca.php?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        eur_to_czk = (
            self._eur_to_czk
            if self._eur_to_czk_override is not None
            else await fx.rate_to_czk("EUR")
        )
        client = await get_client()
        async with host_slot("www.magicstore.it"):
            response = await client.get(
                self._search_url(query),
                headers={"User-Agent": BROWSER_USER_AGENT},
            )
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
        edition_filter = (query.edition or "").strip().casefold() or None
        offers: list[Offer] = []

        for row in tree.css("div.s_item.clearfix"):
            offer = self._parse_row(row, eur_to_czk)
            if offer is None:
                continue
            # Deliberately no query-name substring filter: the server maps
            # English searches to Italian printed names (Lightning Bolt → FULMINE).
            if edition_filter and (
                not offer.edition
                or edition_filter not in offer.edition.casefold()
            ):
                continue
            if query.in_stock_only and offer.stock_qty <= 0:
                continue
            offers.append(offer)

        return offers if query.include_non_playable else filter_playable(offers)

    def _parse_row(
        self,
        row: Node,
        eur_to_czk: float,
    ) -> Offer | None:
        name_node = row.css_first("h3 a[href]")
        if name_node is None:
            return None
        raw_name = " ".join(unescape(name_node.text(strip=True)).split())
        if not raw_name:
            image = row.css_first("a.s_thumb img[title]")
            raw_name = (
                " ".join(
                    unescape(image.attributes.get("title") or "").split()
                )
                if image is not None
                else ""
            )
        href = (name_node.attributes.get("href") or "").strip()
        if not raw_name or not href:
            return None

        edition_links = row.css("p.subcat-rel a")
        edition = (
            " ".join(unescape(edition_links[1].text(strip=True)).split()) or None
            if len(edition_links) >= 2
            else None
        )

        stock_icon = row.css_first('img[src*="semaforo"]')
        stock_src = (
            stock_icon.attributes.get("src") or ""
            if stock_icon is not None
            else ""
        )
        if "semaforo_0" in stock_src:
            stock_qty = 0
        else:
            stock_text = (
                unescape(stock_icon.parent.text(separator=" ", strip=True))
                if stock_icon is not None and stock_icon.parent is not None
                else ""
            )
            stock_match = _STOCK_RE.search(stock_text)
            stock_qty = int(stock_match.group(1)) if stock_match else 0
            if stock_qty == 0 and any(
                marker in stock_src
                for marker in ("semaforo_1", "semaforo_2")
            ):
                stock_qty = 1

        price_node = row.css_first("p.s_price")
        price_classes = (
            (price_node.attributes.get("class") or "").split()
            if price_node is not None
            else []
        )
        # Captured pages use `s_no_price` for unavailable rows, alongside an
        # optional `Valutazione` buyback amount. Neither is a current sale offer.
        if price_node is None or "s_no_price" in price_classes or stock_qty <= 0:
            return None
        price_eur = self._parse_price_eur(price_node.text(strip=True))
        if price_eur is None:
            return None

        cart = row.css_first(
            'a.s_button_add_to_cart[href*="/carrello/update.php"]'
        )
        shop_ref: str | None = None
        if cart is not None:
            value = (cart.attributes.get("rel") or "").strip()
            if value.isdigit():
                shop_ref = value
            else:
                values = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(
                        cart.attributes.get("href") or ""
                    ).query
                ).get("id")
                if values and values[0].isdigit():
                    shop_ref = values[0]

        return Offer(
            shop="magicstore",
            card_name=raw_name,
            edition=edition,
            set_code=None,
            # `M`, `R`, `NC`, and `C` in the row are rarity codes, not grades.
            # Magic Store exposes no condition axis and sells a single NM grade.
            condition=Condition.NM,
            language=None,
            foil="foil" in raw_name.casefold(),
            price_czk=int(round(price_eur * eur_to_czk)),
            price_native=price_eur,
            currency="EUR",
            stock_qty=stock_qty,
            url=urllib.parse.urljoin(BASE, href),
            shop_ref=shop_ref,
        )

    @staticmethod
    def _parse_price_eur(text: str) -> float | None:
        match = _PRICE_RE.search(unescape(text).replace("\u00a0", " "))
        if match is None:
            return None
        raw = match.group(1)
        if "," in raw:
            raw = raw.replace(".", "").replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if math.isfinite(value) and value >= 0 else None
