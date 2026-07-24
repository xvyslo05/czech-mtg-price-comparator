from __future__ import annotations

import json
import math
import urllib.parse
from typing import Any

from .. import fx
from ..filters import filter_playable
from ..http_client import get_client, host_slot
from ..models import Condition, Offer, SearchQuery
from ..normalize import normalize_condition
from .base import ShopAdapter

SHOP_BASE = "https://mtgspot.pl"
GATEWAY = "https://gateway.mtgspot.pl"
ENDPOINT = f"{GATEWAY}/api/shop/articles"
API_KEY = "b3d39321-5dc4-4298-98c6-0399432a948b"
GAME_ID = "1"
SINGLES_CATEGORY = 1
PAGE_LIMIT = 100
DEFAULT_PLN_TO_CZK = fx.STATIC_DEFAULTS["PLN"]

_LANGUAGES = {
    "english": "EN",
    "german": "DE",
    "french": "FR",
    "italian": "IT",
    "spanish": "ES",
    "japanese": "JP",
    "portuguese": "PT",
    "russian": "RU",
    "korean": "KO",
    "chinese simplified": "ZH",
    "chinese traditional": "ZH",
}


class MtgspotAdapter(ShopAdapter):
    shop_id = "mtgspot"
    base_url = SHOP_BASE
    supports_login = False
    supports_cart = False
    supports_watchlist = False

    def __init__(self, *, pln_to_czk: float | None = None) -> None:
        self._pln_to_czk_override = pln_to_czk
        self._pln_to_czk = fx.rate_to_czk_nolive(
            "PLN",
            override=pln_to_czk,
        )

    async def search(self, query: SearchQuery) -> list[Offer]:
        pln_to_czk = (
            self._pln_to_czk
            if self._pln_to_czk_override is not None
            else await fx.rate_to_czk("PLN")
        )
        params: dict[str, str | int] = {
            "filter[name]": query.name,
            "filter[id_category]": SINGLES_CATEGORY,
            "sort": "name",
            "page[limit]": PAGE_LIMIT,
            "page[offset]": 0,
        }
        if query.in_stock_only:
            params["filter[is_in_channel]"] = 1
        client = await get_client()
        async with host_slot("gateway.mtgspot.pl"):
            response = await client.get(
                ENDPOINT,
                params=params,
                headers={
                    "X-Api-Key": API_KEY,
                    "X-Game-Id": GAME_ID,
                    "Accept": "application/json",
                    "Origin": SHOP_BASE,
                    "Referer": f"{SHOP_BASE}/single",
                },
            )
        response.raise_for_status()
        return self._parse(response.text, query, pln_to_czk)

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        return self._parse(html, query, self._pln_to_czk)

    def _parse(
        self,
        html: str,
        query: SearchQuery,
        pln_to_czk: float,
    ) -> list[Offer]:
        if not html or not html.strip():
            return []
        try:
            payload = json.loads(html)
        except (json.JSONDecodeError, TypeError):
            return []
        return self._parse_payload(payload, query, pln_to_czk)

    def _parse_payload(
        self,
        payload: Any,
        query: SearchQuery,
        pln_to_czk: float,
    ) -> list[Offer]:
        if not isinstance(payload, dict):
            return []
        response = payload.get("response")
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list):
            return []

        wanted = query.name.casefold()
        edition_filter = (query.edition or "").strip().casefold() or None
        offers: list[Offer] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            attributes = item.get("attributes")
            if not isinstance(attributes, dict):
                continue
            if str(attributes.get("type") or "").strip().casefold() != "single":
                continue

            card_name = " ".join(str(attributes.get("title") or "").split())
            if not card_name or wanted not in card_name.casefold():
                continue
            edition = " ".join(
                str(attributes.get("expansion_name") or "").split()
            ) or None
            if edition_filter and (
                not edition or edition_filter not in edition.casefold()
            ):
                continue

            offer = self._article_to_offer(
                item,
                card_name,
                edition,
                pln_to_czk,
            )
            if offer is None:
                continue
            if query.in_stock_only and offer.stock_qty <= 0:
                continue
            offers.append(offer)

        return offers if query.include_non_playable else filter_playable(offers)

    def _article_to_offer(
        self,
        item: dict[str, Any],
        card_name: str,
        edition: str | None,
        pln_to_czk: float,
    ) -> Offer | None:
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            return None
        try:
            price_pln = float(attributes.get("price"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(price_pln) or price_pln < 0:
            return None

        try:
            stock_qty = max(0, int(attributes.get("stock") or 0))
        except (TypeError, ValueError):
            stock_qty = 0

        condition_raw = str(attributes.get("condition") or "").strip()
        condition = normalize_condition(condition_raw)
        if condition is Condition.UNKNOWN and condition_raw:
            try:
                condition = Condition[condition_raw.upper()]
            except KeyError:
                condition = Condition.UNKNOWN

        language_raw = " ".join(
            str(attributes.get("language") or "").split()
        )
        language = self._normalize_language(language_raw)
        article_id = item.get("id")
        result_url = (
            f"{SHOP_BASE}/single?"
            f"{urllib.parse.urlencode({'search': card_name})}"
        )
        return Offer(
            shop="mtgspot",
            card_name=card_name,
            edition=edition,
            set_code=None,
            condition=condition,
            language=language,
            foil=attributes.get("is_foil") is True,
            price_czk=int(round(price_pln * pln_to_czk)),
            price_native=price_pln,
            currency="PLN",
            stock_qty=stock_qty,
            url=result_url,
            shop_ref=str(article_id) if article_id is not None else None,
        )

    @staticmethod
    def _normalize_language(language: str) -> str | None:
        if not language:
            return None
        return _LANGUAGES.get(language.casefold(), language)
