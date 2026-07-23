from __future__ import annotations

import asyncio
import json
import math
import os
import re
from typing import Any

from selectolax.parser import HTMLParser

from ..filters import filter_playable
from ..http_client import get_client, host_slot
from ..models import Offer, SearchQuery
from ..normalize import normalize_condition
from .base import ShopAdapter

SHOP_BASE = "https://axionnow.com"
SUGGEST_ENDPOINT = f"{SHOP_BASE}/search/suggest.json"
DEFAULT_GBP_TO_CZK = 28.5
MAX_HANDLES = 10

_TITLE_RE = re.compile(r"^(.*?)\s*\(\d+\)\s*-\s*([A-Z0-9]+)\s*$")


def _env_rate(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


class AxionNowAdapter(ShopAdapter):
    shop_id = "axionnow"
    base_url = SHOP_BASE
    supports_login = False
    supports_cart = False
    supports_watchlist = False

    def __init__(self, *, gbp_to_czk: float | None = None) -> None:
        self._gbp_to_czk = (
            gbp_to_czk
            if gbp_to_czk is not None
            else _env_rate("CZ_MTG_GBP_TO_CZK", DEFAULT_GBP_TO_CZK)
        )

    async def search(self, query: SearchQuery) -> list[Offer]:
        client = await get_client()
        async with host_slot("axionnow.com"):
            response = await client.get(
                SUGGEST_ENDPOINT,
                params={
                    "q": query.name,
                    "resources[type]": "product",
                    "resources[limit]": str(MAX_HANDLES),
                },
                headers={"Accept": "application/json"},
            )
        response.raise_for_status()
        products = self._parse_suggest(response.text, query)[:MAX_HANDLES]

        async def fetch_product(product: dict[str, str | None]) -> list[Offer]:
            handle = product.get("handle")
            if not handle:
                return []
            async with host_slot("axionnow.com"):
                product_response = await client.get(
                    f"{SHOP_BASE}/products/{handle}.js",
                    headers={"Accept": "application/json"},
                )
            product_response.raise_for_status()
            return self._parse_product(
                product_response.text,
                product.get("card_name"),
                product.get("edition"),
                product.get("set_code"),
                query,
            )

        results = await asyncio.gather(
            *(fetch_product(product) for product in products),
            return_exceptions=True,
        )
        return [
            offer
            for group in results
            if isinstance(group, list)
            for offer in group
        ]

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        return self._parse_product(html, None, None, None, query)

    def _parse_suggest(
        self,
        payload_str: str,
        query: SearchQuery,
    ) -> list[dict[str, str | None]]:
        if not payload_str or not payload_str.strip():
            return []
        try:
            payload = json.loads(payload_str)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(payload, dict):
            return []

        resources = payload.get("resources")
        results = resources.get("results") if isinstance(resources, dict) else None
        products = results.get("products") if isinstance(results, dict) else None
        if not isinstance(products, list):
            return []

        wanted = query.name.casefold()
        edition_filter = (query.edition or "").strip().casefold() or None
        matches: list[dict[str, str | None]] = []
        for product in products:
            if not isinstance(product, dict):
                continue
            if str(product.get("type") or "").strip().casefold() != "singles":
                continue
            tags = product.get("tags")
            if not isinstance(tags, list) or not any(
                isinstance(tag, str) and tag.casefold() == "magic" for tag in tags
            ):
                continue

            title = " ".join(str(product.get("title") or "").split())
            handle = str(product.get("handle") or "").strip()
            if not title or not handle:
                continue

            card_name, title_set_code = self._name_and_set_code(title)
            edition: str | None = None
            body = product.get("body")
            if isinstance(body, str) and body:
                tree = HTMLParser(body)
                name_node = tree.css_first(".product-description-name")
                edition_node = tree.css_first(".product-description-set-name")
                if name_node is not None:
                    parsed_name = " ".join(name_node.text(strip=True).split())
                    if parsed_name:
                        card_name = parsed_name
                if edition_node is not None:
                    parsed_edition = " ".join(edition_node.text(strip=True).split())
                    edition = parsed_edition or None

            if wanted not in card_name.casefold():
                continue
            if edition_filter and not any(
                edition_filter in value.casefold()
                for value in (edition, title_set_code, title)
                if value
            ):
                continue
            matches.append(
                {
                    "handle": handle,
                    "card_name": card_name,
                    "edition": edition,
                    "set_code": title_set_code,
                }
            )
        return matches

    def _parse_product(
        self,
        js_payload_str: str,
        card_name: str | None,
        edition: str | None,
        set_code: str | None,
        query: SearchQuery,
    ) -> list[Offer]:
        if not js_payload_str or not js_payload_str.strip():
            return []
        try:
            product = json.loads(js_payload_str)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(product, dict):
            return []

        title = " ".join(str(product.get("title") or "").split())
        parsed_name, parsed_set_code = self._name_and_set_code(title)
        card_name = " ".join((card_name or parsed_name).split())
        set_code = (set_code or parsed_set_code or "").upper() or None
        handle = str(product.get("handle") or "").strip()
        if not card_name or not handle:
            return []

        wanted = query.name.casefold()
        if wanted not in card_name.casefold():
            return []
        edition_filter = (query.edition or "").strip().casefold() or None
        if edition_filter and not any(
            edition_filter in value.casefold()
            for value in (edition, set_code)
            if value
        ):
            return []

        variants = product.get("variants")
        if not isinstance(variants, list):
            return []

        offers: list[Offer] = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            try:
                price_pence = float(variant.get("price"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(price_pence) or price_pence < 0:
                continue

            finish = str(variant.get("option1") or "").strip()
            condition_raw = str(variant.get("option2") or "").strip()
            if not finish or not condition_raw:
                title_parts = [
                    part.strip()
                    for part in str(variant.get("title") or "").split("/")
                ]
                if not finish and title_parts:
                    finish = title_parts[0]
                if not condition_raw and len(title_parts) > 1:
                    condition_raw = title_parts[1]

            available = variant.get("available") is True
            stock_qty = 1 if available else 0
            if query.in_stock_only and stock_qty <= 0:
                continue
            variant_id = variant.get("id")
            offers.append(
                Offer(
                    shop="axionnow",
                    card_name=card_name,
                    edition=edition,
                    set_code=set_code,
                    condition=normalize_condition(condition_raw),
                    language=None,
                    foil=finish.casefold() == "foil",
                    price_czk=int(round((price_pence / 100) * self._gbp_to_czk)),
                    stock_qty=stock_qty,
                    url=f"{SHOP_BASE}/products/{handle}",
                    shop_ref=str(variant_id) if variant_id is not None else None,
                )
            )

        return offers if query.include_non_playable else filter_playable(offers)

    @staticmethod
    def _name_and_set_code(title: str) -> tuple[str, str | None]:
        match = _TITLE_RE.match(title)
        if match is None:
            return title.strip(), None
        return match.group(1).strip(), match.group(2).upper()
