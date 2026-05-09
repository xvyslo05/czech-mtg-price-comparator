from __future__ import annotations

import json
import urllib.parse
from typing import Any

from ..http_client import get_client, host_slot
from ..models import Condition, Offer, SearchQuery
from ..normalize import normalize_condition
from .base import ShopAdapter

API_BASE = "https://wizardshop.cz/api/v1"
SHOP_BASE = "https://najada.games"
ENDPOINT = f"{API_BASE}/najada2/catalog/mtg-singles/"
PAGE_LIMIT = 100  # API supports up to 100; covers most queries.


class NajadaAdapter(ShopAdapter):
    shop_id = "najada"
    base_url = SHOP_BASE

    def _request_url(self, query: SearchQuery) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {"q": query.name, "limit": PAGE_LIMIT}
        return ENDPOINT, params

    def _result_url(self, query: SearchQuery) -> str:
        return f"{SHOP_BASE}/vyhledavani?{urllib.parse.urlencode({'q': query.name})}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        url, params = self._request_url(query)
        client = await get_client()
        async with host_slot("wizardshop.cz"):
            resp = await client.get(
                url,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Origin": SHOP_BASE,
                    "Referer": f"{SHOP_BASE}/",
                },
            )
        resp.raise_for_status()
        return self._parse_payload(resp.json(), query)

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        # For tests using saved JSON fixture (we re-use the parse() ABC slot for any payload).
        return self._parse_payload(json.loads(html), query)

    def _parse_payload(self, payload: dict[str, Any], query: SearchQuery) -> list[Offer]:
        offers: list[Offer] = []
        results = payload.get("results", []) if isinstance(payload, dict) else []
        wanted = query.name.lower()
        edition_filter = (query.edition or "").lower().strip() or None
        url = self._result_url(query)

        for card in results:
            card_name = (
                card.get("localized_name")
                or card.get("name_cz")
                or card.get("name")
                or ""
            ).strip()
            if wanted not in card_name.lower():
                continue

            expansion = card.get("expansion") or {}
            edition = (
                expansion.get("localized_name")
                or expansion.get("name")
                or None
            )
            set_code = expansion.get("short_code") or None
            if edition_filter and (
                (not edition or edition_filter not in edition.lower())
                and (not set_code or edition_filter not in set_code.lower())
            ):
                continue

            for article in card.get("articles", []) or []:
                offer = self._article_to_offer(card_name, edition, set_code, article, url)
                if offer is None:
                    continue
                if query.in_stock_only and offer.stock_qty <= 0:
                    continue
                offers.append(offer)
        return offers

    def _article_to_offer(
        self,
        card_name: str,
        edition: str | None,
        set_code: str | None,
        article: dict[str, Any],
        url: str,
    ) -> Offer | None:
        price = article.get("effective_price_czk") or article.get("regular_price_czk")
        if price is None:
            return None
        try:
            price_czk = int(round(float(price)))
        except (TypeError, ValueError):
            return None

        cond_raw = (article.get("condition") or "").strip()
        condition = normalize_condition(cond_raw) if cond_raw else Condition.UNKNOWN
        # Najada uses our scheme directly (NM/EX/GD/LP/PL/HP); fall back to enum lookup.
        if condition is Condition.UNKNOWN and cond_raw:
            try:
                condition = Condition[cond_raw.upper()]
            except KeyError:
                condition = Condition.UNKNOWN

        props = article.get("additional_properties") or {}
        foil = bool(props.get("is_foil"))

        stock = article.get("total_availability")
        try:
            stock_qty = int(stock) if stock is not None else 0
        except (TypeError, ValueError):
            stock_qty = 0

        language = (article.get("language_code") or "").strip() or None

        return Offer(
            shop="najada",
            card_name=card_name,
            edition=edition,
            set_code=set_code,
            condition=condition,
            language=language,
            foil=foil,
            price_czk=price_czk,
            stock_qty=stock_qty,
            url=url,
        )
