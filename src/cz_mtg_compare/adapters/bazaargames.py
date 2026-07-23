from __future__ import annotations

import asyncio
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
from ..models import Condition, Offer, SearchQuery, ShopId
from .base import ShopAdapter

DEFAULT_EUR_TO_CZK = 24.5
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_TITLE_PREFIX = "More information about "
_FOIL_RE = re.compile(r"\s*\(foil\)\s*$", re.IGNORECASE)
_COLLECTOR_RE = re.compile(r"\s*\(#\d+\)\s*$", re.IGNORECASE)


def _env_rate(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


class BazaarGamesAdapter(ShopAdapter):
    supports_login = False
    supports_cart = False
    supports_watchlist = False

    def __init__(
        self,
        *,
        shop_id: ShopId,
        base_url: str,
        eur_to_czk: float | None = None,
        enrich_detail: bool = False,
    ) -> None:
        self.shop_id = shop_id
        self.base_url = base_url.rstrip("/")
        self._host = urllib.parse.urlsplit(self.base_url).hostname or ""
        self._eur_to_czk = (
            eur_to_czk
            if eur_to_czk is not None
            else _env_rate("CZ_MTG_EUR_TO_CZK", DEFAULT_EUR_TO_CZK)
        )
        self._enrich_detail = enrich_detail

    def _search_url(self, query: SearchQuery) -> str:
        return (
            f"{self.base_url}/en-WW/query?"
            f"{urllib.parse.urlencode({'name': query.name, 'tab': 'singles'})}"
        )

    async def search(self, query: SearchQuery) -> list[Offer]:
        client = await get_client()
        async with host_slot(self._host):
            response = await client.get(
                self._search_url(query),
                headers={"User-Agent": BROWSER_USER_AGENT},
            )
        response.raise_for_status()
        offers = self._parse(response.text, query)
        if self._enrich_detail:
            await self._enrich_offers(offers)
            if query.in_stock_only:
                offers = [offer for offer in offers if offer.stock_qty > 0]
        return offers

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        return self._parse(html, query)

    def _parse(self, html: str, query: SearchQuery) -> list[Offer]:
        tree = HTMLParser(html)
        wanted = query.name.casefold()
        edition_filter = (query.edition or "").strip().casefold() or None
        offers: list[Offer] = []

        for tile in tree.css("div.singles"):
            offer = self._parse_tile(tile)
            if offer is None:
                continue
            if wanted not in offer.card_name.casefold():
                continue
            if edition_filter and (
                not offer.edition
                or edition_filter not in offer.edition.casefold()
            ):
                continue
            if query.in_stock_only and offer.stock_qty <= 0:
                continue
            offers.append(offer)

        return offers if query.include_non_playable else filter_playable(offers)

    def _parse_tile(self, tile: Node) -> Offer | None:
        link = tile.css_first("div.thumb a[href]")
        if link is None:
            return None
        href = (link.attributes.get("href") or "").strip()
        if not href:
            return None
        url = urllib.parse.urljoin(self.base_url, href)

        header = tile.css_first("a.header")
        visible_name = (
            " ".join(unescape(header.text(strip=True)).split())
            if header is not None
            else ""
        )
        title = " ".join(
            unescape(link.attributes.get("title") or visible_name).split()
        )
        card_name, edition, foil = self._split_name_edition_foil(title)
        if not card_name:
            card_name = visible_name
        if not card_name:
            return None

        price_node = tile.css_first("div.price-display span.nowrap")
        price_eur = (
            self._parse_price_eur(price_node.text(strip=True))
            if price_node is not None
            else None
        )
        if price_eur is None:
            return None

        stock_qty = 1 if tile.css_first("a.button.cta.cart.buy") else 0
        shop_ref = self._product_id(url, tile)
        return Offer(
            shop=self.shop_id,
            card_name=card_name,
            edition=edition,
            set_code=None,
            condition=Condition.NM,
            language=None,
            foil=foil,
            price_czk=int(round(price_eur * self._eur_to_czk)),
            stock_qty=stock_qty,
            url=url,
            shop_ref=shop_ref,
        )

    async def _enrich_offers(self, offers: list[Offer]) -> None:
        if not offers:
            return
        client = await get_client()

        async def enrich_one(offer: Offer) -> None:
            try:
                async with host_slot(self._host):
                    response = await client.get(
                        offer.url,
                        headers={"User-Agent": BROWSER_USER_AGENT},
                    )
                response.raise_for_status()
                self._apply_detail(offer, response.text)
            except Exception:  # noqa: BLE001 - detail enrichment is best-effort
                return

        await asyncio.gather(*(enrich_one(offer) for offer in offers))

    def _parse_detail(
        self,
        html: str,
        url: str | None = None,
        listing_stock_qty: int = 0,
    ) -> Offer | None:
        tree = HTMLParser(html)
        product: dict[str, Any] | None = None
        for node in tree.css('script[type="application/ld+json"]'):
            try:
                payload = json.loads(node.text())
            except (json.JSONDecodeError, TypeError):
                continue
            product = self._find_product(payload)
            if product is not None:
                break
        if product is None:
            return None

        offers_data = product.get("offers")
        if isinstance(offers_data, list):
            offers_data = next(
                (value for value in offers_data if isinstance(value, dict)),
                None,
            )
        if not isinstance(offers_data, dict):
            return None
        try:
            price_eur = float(offers_data.get("price"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(price_eur) or price_eur < 0:
            return None

        raw_name = " ".join(str(product.get("name") or "").split())
        card_name, parsed_edition, foil = self._split_name_edition_foil(raw_name)
        edition = " ".join(str(product.get("category") or "").split()) or parsed_edition
        detail_url = str(offers_data.get("url") or url or "").strip()
        if not card_name or not detail_url:
            return None
        availability = str(offers_data.get("availability") or "").casefold()
        if availability.endswith("outofstock"):
            stock_qty = 0
        elif availability.endswith("instock"):
            stock_qty = 1
        else:
            stock_qty = listing_stock_qty
        sku = product.get("sku")
        return Offer(
            shop=self.shop_id,
            card_name=card_name,
            edition=edition,
            set_code=None,
            condition=Condition.NM,
            language=None,
            foil=foil,
            price_czk=int(round(price_eur * self._eur_to_czk)),
            stock_qty=stock_qty,
            url=detail_url,
            shop_ref=str(sku) if sku is not None else None,
        )

    def _apply_detail(self, offer: Offer, html: str) -> None:
        detail = self._parse_detail(html, offer.url, offer.stock_qty)
        if detail is None:
            return
        offer.card_name = detail.card_name
        offer.edition = detail.edition
        offer.condition = detail.condition
        offer.foil = detail.foil
        offer.price_czk = detail.price_czk
        offer.stock_qty = detail.stock_qty
        offer.shop_ref = detail.shop_ref

    @classmethod
    def _split_name_edition_foil(
        cls,
        raw: str,
    ) -> tuple[str, str | None, bool]:
        value = raw.strip()
        if value.casefold().startswith(_TITLE_PREFIX.casefold()):
            value = value[len(_TITLE_PREFIX):].strip()
        if " - " in value:
            name, edition = value.rsplit(" - ", 1)
            edition = edition.strip() or None
        else:
            name, edition = value, None
        foil = bool(_FOIL_RE.search(name))
        name = _FOIL_RE.sub("", name).strip()
        name = _COLLECTOR_RE.sub("", name).strip()
        return name, edition, foil

    @staticmethod
    def _parse_price_eur(text: str) -> float | None:
        raw = (
            unescape(text)
            .replace("\u00a0", "")
            .replace(" ", "")
            .replace("€", "")
            .strip()
        )
        if not raw:
            return None
        if "," in raw:
            raw = raw.replace(".", "").replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if math.isfinite(value) and value >= 0 else None

    @staticmethod
    def _product_id(url: str, tile: Node) -> str | None:
        tail = urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            return tail
        id_node = tile.css_first("div.name a.list.toggle[data-id]")
        if id_node is None:
            return None
        value = (id_node.attributes.get("data-id") or "").strip()
        return value if value.isdigit() else None

    @classmethod
    def _find_product(cls, payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            if payload.get("@type") == "Product":
                return payload
            graph = payload.get("@graph")
            if graph is not None:
                return cls._find_product(graph)
        if isinstance(payload, list):
            for item in payload:
                product = cls._find_product(item)
                if product is not None:
                    return product
        return None
