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
from ..normalize import strip_card_suffixes
from .base import ShopAdapter

BASE = "https://trader-online.de"
DEFAULT_EUR_TO_CZK = fx.STATIC_DEFAULTS["EUR"]
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_PRODUCT_CODE_RE = re.compile(r"([A-Z0-9]{2,4})-([A-Z]{2})\d+", re.IGNORECASE)
_CENTS_RE = re.compile(r"(\d{1,2})")


class TraderOnlineAdapter(ShopAdapter):
    shop_id = "traderonline"
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
        params = {
            "lang": 1,
            "cl": "search",
            "searchparam": query.name,
            "_artperpage": 100,
        }
        return f"{BASE}/index.php?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        eur_to_czk = (
            self._eur_to_czk
            if self._eur_to_czk_override is not None
            else await fx.rate_to_czk("EUR")
        )
        client = await get_client()
        async with host_slot("trader-online.de"):
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
        wanted = query.name.casefold()
        edition_filter = (query.edition or "").strip().casefold() or None
        offers: list[Offer] = []

        for tile in tree.css("div.card.product-card"):
            offer = self._parse_card(tile, eur_to_czk)
            if offer is None:
                continue
            if wanted not in offer.card_name.casefold():
                continue
            if edition_filter and not any(
                edition_filter in value.casefold()
                for value in (offer.edition, offer.set_code)
                if value
            ):
                continue
            if query.in_stock_only and offer.stock_qty <= 0:
                continue
            offers.append(offer)

        return offers if query.include_non_playable else filter_playable(offers)

    def _parse_card(
        self,
        tile: Node,
        eur_to_czk: float,
    ) -> Offer | None:
        link = tile.css_first("a.stretched-link[href]")
        href = (
            (link.attributes.get("href") or "").strip()
            if link is not None
            else ""
        )
        # Buylist amounts are what the shop pays, not a sale price.
        if (
            not href
            or "/card-purchase/" in href
            or "/card-purchase-" in href
        ):
            return None
        url = urllib.parse.urljoin(BASE, href)

        image = tile.css_first("img.product-img")
        title = tile.css_first(".card-title")
        raw_name = (
            " ".join(unescape(title.text(strip=True)).split())
            if title is not None
            else ""
        )
        if not raw_name or raw_name.endswith(("…", "...")):
            raw_name = (
                " ".join(
                    unescape(image.attributes.get("alt") or "").split()
                )
                if image is not None
                else ""
            )
        if not raw_name:
            return None
        card_name, foil_from_suffix, _ = strip_card_suffixes(raw_name)
        if not card_name:
            return None

        edition: str | None = None
        has_edition_attribute = False
        for item in tile.css("ul.attributes li"):
            label = item.css_first("strong")
            if label is None:
                continue
            if unescape(label.text(strip=True)).rstrip(":").casefold() != "edition":
                continue
            has_edition_attribute = True
            value = item.css_first("span")
            if value is not None:
                edition = " ".join(unescape(value.text(strip=True)).split()) or None
            break

        image_src = (
            (image.attributes.get("src") or "").strip()
            if image is not None
            else ""
        )
        code_match = _PRODUCT_CODE_RE.search(image_src)
        set_code = code_match.group(1).upper() if code_match else None
        language = code_match.group(2).upper() if code_match else None
        # Sell cards can use root-level URLs. Product-code or edition metadata
        # distinguishes them from accessories and sealed products.
        if code_match is None and not has_edition_attribute:
            return None

        price_eur = self._parse_price_eur(tile)
        if price_eur is None:
            return None

        stock_flag = tile.css_first("span.stockFlag")
        stock_qty = self._stock_qty(stock_flag)
        shop_ref = self._shop_ref(tile)
        foil = foil_from_suffix or "foil" in f"{raw_name} {edition or ''}".casefold()

        return Offer(
            shop="traderonline",
            card_name=card_name,
            edition=edition,
            set_code=set_code,
            # Search tiles have no grade axis. Trader-Online sells its singles
            # as Near Mint / Mint, so the results-only adapter defaults to NM.
            condition=Condition.NM,
            language=language,
            foil=foil,
            price_czk=int(round(price_eur * eur_to_czk)),
            price_native=price_eur,
            currency="EUR",
            stock_qty=stock_qty,
            url=url,
            shop_ref=shop_ref,
        )

    @staticmethod
    def _parse_price_eur(tile: Node) -> float | None:
        # Select only the split current-price spans. Sale tiles also contain a
        # nested struck `.oldPrice`, which must never affect the parsed amount.
        pre_node = tile.css_first(".grid-price .price-pre")
        decimal_node = tile.css_first(".grid-price .price-decimal")
        if pre_node is None or decimal_node is None:
            return None
        euros = re.sub(r"\D", "", unescape(pre_node.text(strip=True)))
        cents_match = _CENTS_RE.search(unescape(decimal_node.text(strip=True)))
        if not euros or cents_match is None:
            return None
        try:
            value = float(f"{euros}.{cents_match.group(1).zfill(2)}")
        except ValueError:
            return None
        return value if math.isfinite(value) and value >= 0 else None

    @staticmethod
    def _stock_qty(stock_flag: Node | None) -> int:
        if stock_flag is None:
            return 0
        text = " ".join(unescape(stock_flag.text(strip=True)).split()).casefold()
        if any(
            marker in text
            for marker in ("not available", "unavailable", "out of stock")
        ):
            return 0
        icon = stock_flag.css_first(".stock-flag-icon")
        classes = (
            (icon.attributes.get("class") or "").split()
            if icon is not None
            else []
        )
        if "text-success" in classes or "ready for shipping" in text:
            return 1
        return 0

    @staticmethod
    def _shop_ref(tile: Node) -> str | None:
        button = tile.css_first("button.divi-add-to-cart[data-id]")
        if button is not None:
            value = (button.attributes.get("data-id") or "").strip()
            if value:
                return value
        wishlist = tile.css_first('a[href*="cl=account"][href*="anid="]')
        if wishlist is None:
            return None
        href = wishlist.attributes.get("href") or ""
        values = urllib.parse.parse_qs(
            urllib.parse.urlsplit(href).query
        ).get("anid")
        return values[0] if values and values[0] else None
