from __future__ import annotations

import asyncio
import json
import math
import os
import re
import urllib.parse
from html import unescape
from typing import Any

from selectolax.parser import HTMLParser

from ..filters import filter_playable
from ..http_client import get_client, host_slot
from ..models import Condition, Offer, SearchQuery
from ..normalize import normalize_condition
from .base import ShopAdapter

BASE = "https://magicmadhouse.co.uk"
SEARCH_URL = f"{BASE}/search.php"
PRODUCT_ATTRIBUTES_URL = f"{BASE}/remote/v1/product-attributes/{{product_id}}"
DEFAULT_GBP_TO_CZK = 28.5
DEFAULT_MAX_PAGES = 1
MAX_SEARCH_PAGES = 4
MAX_ENRICH_PRODUCTS = 5
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_BODL_RE = re.compile(
    r'var\s+BODL\s*=\s*JSON\.parse\(\s*("(?:\\.|[^"\\])*")\s*\);',
    re.DOTALL,
)
_ATTRIBUTE_NAME_RE = re.compile(r"^attribute\[(\d+)\]$")
_FOIL_PAREN_RE = re.compile(r"\s*\(\s*foil\s*\)", re.IGNORECASE)
_REVERSE_HOLO_RE = re.compile(r"\s*\(\s*reverse holo\s*\)", re.IGNORECASE)
_TRAILING_BRACKET_RE = re.compile(r"\s*\[[^\]]+\]\s*$")
_NAME_TOKEN_RE = re.compile(r"[^a-z0-9]+")

ConditionOption = tuple[str, str, str, str]


def _env_rate(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


class MagicMadhouseAdapter(ShopAdapter):
    shop_id = "magicmadhouse"
    base_url = BASE
    supports_login = False
    supports_cart = False
    supports_watchlist = False

    def __init__(
        self,
        *,
        enrich_variants: bool = False,
        max_pages: int = DEFAULT_MAX_PAGES,
        gbp_to_czk: float | None = None,
    ) -> None:
        self._enrich_variants = enrich_variants
        self._max_pages = max(1, min(max_pages, MAX_SEARCH_PAGES))
        self._gbp_to_czk = (
            gbp_to_czk
            if gbp_to_czk is not None
            else _env_rate("CZ_MTG_GBP_TO_CZK", DEFAULT_GBP_TO_CZK)
        )

    def _search_url(self, query: SearchQuery, page: int = 1) -> str:
        params: dict[str, str | int] = {"search_query": query.name}
        if page > 1:
            params["page"] = page
        return f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        client = await get_client()
        pairs: list[tuple[Offer, dict[str, Any]]] = []

        for page in range(1, self._max_pages + 1):
            async with host_slot("magicmadhouse.co.uk"):
                response = await client.get(
                    self._search_url(query, page),
                    headers={"User-Agent": BROWSER_USER_AGENT},
                )
            response.raise_for_status()
            products = self._decode_bodl(response.text)
            pairs.extend(self._parse_products(products, query))
            if len(products) < 16:
                break

        offers = [offer for offer, _ in pairs]
        if self._enrich_variants:
            offers = await self._enrich_offers(pairs)
        offers = self._apply_filters(offers, query)
        return offers if query.include_non_playable else filter_playable(offers)

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        pairs = self._parse_products(self._decode_bodl(html), query)
        offers = self._apply_filters([offer for offer, _ in pairs], query)
        return offers if query.include_non_playable else filter_playable(offers)

    @staticmethod
    def _decode_bodl(html: str) -> list[dict[str, Any]]:
        if not html:
            return []
        match = _BODL_RE.search(html)
        if match is None:
            return []
        try:
            decoded = json.loads(json.loads(match.group(1)))
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(decoded, dict):
            return []
        search = decoded.get("search")
        products = search.get("products") if isinstance(search, dict) else None
        if not isinstance(products, list):
            return []
        return [product for product in products if isinstance(product, dict)]

    def _parse_products(
        self,
        products: list[dict[str, Any]],
        query: SearchQuery,
    ) -> list[tuple[Offer, dict[str, Any]]]:
        pairs: list[tuple[Offer, dict[str, Any]]] = []
        for product in products:
            brand = product.get("brand")
            if not isinstance(brand, dict) or (
                str(brand.get("name") or "").strip()
                != "Magic: The Gathering"
            ):
                continue
            offer = self._product_to_offer(product)
            if offer is None or not self._name_matches(
                query.name, offer.card_name
            ):
                continue
            pairs.append((offer, product))
        return pairs

    def _product_to_offer(self, product: dict[str, Any]) -> Offer | None:
        raw_name = self._clean_text(product.get("name"))
        card_name, suffix_edition = self._split_name(raw_name)
        if not card_name:
            return None

        custom_fields = self._custom_fields(product.get("custom_fields"))
        edition = custom_fields.get("magic set") or suffix_edition
        sku = self._clean_text(product.get("sku"))
        set_code = self._set_code_from_sku(sku)
        price_gbp = self._price_from_mapping(product)
        if price_gbp is None:
            return None

        url = self._canonical_url(self._clean_text(product.get("url")))
        product_id = product.get("id")
        if not url or product_id is None:
            return None

        language = self._language_code(custom_fields.get("language"))
        single_cards = custom_fields.get("single cards", "")
        return Offer(
            shop="magicmadhouse",
            card_name=card_name,
            edition=edition,
            set_code=set_code,
            condition=Condition.UNKNOWN,
            language=language,
            foil=self._foil_from(sku, raw_name, single_cards),
            price_czk=int(round(price_gbp * self._gbp_to_czk)),
            stock_qty=self._listing_stock(product),
            url=url,
            shop_ref=str(product_id),
        )

    @staticmethod
    def _apply_filters(
        offers: list[Offer],
        query: SearchQuery,
    ) -> list[Offer]:
        edition_filter = (query.edition or "").strip().casefold() or None
        filtered: list[Offer] = []
        for offer in offers:
            if edition_filter and not any(
                edition_filter in value.casefold()
                for value in (offer.edition, offer.set_code)
                if value
            ):
                continue
            if query.in_stock_only and offer.stock_qty <= 0:
                continue
            filtered.append(offer)
        return filtered

    async def _enrich_offers(
        self,
        pairs: list[tuple[Offer, dict[str, Any]]],
    ) -> list[Offer]:
        targets = [
            (offer, product)
            for offer, product in pairs
            if bool(product.get("has_options"))
        ][:MAX_ENRICH_PRODUCTS]
        if not targets:
            return [offer for offer, _ in pairs]

        results = await asyncio.gather(
            *(self._enrich_product(offer) for offer, _ in targets)
        )
        enriched = {
            id(offer): variants
            for (offer, _), variants in zip(targets, results, strict=True)
        }
        output: list[Offer] = []
        for offer, _ in pairs:
            output.extend(enriched.get(id(offer), [offer]))
        return output

    async def _enrich_product(self, fallback: Offer) -> list[Offer]:
        client = await get_client()
        try:
            async with host_slot("magicmadhouse.co.uk"):
                response = await client.get(
                    fallback.url,
                    headers={"User-Agent": BROWSER_USER_AGENT},
                )
            response.raise_for_status()
            options = self._parse_condition_options(response.text)
            if not options:
                return [fallback]

            variants: list[Offer] = []
            option_failed = False
            for option_id, value_id, sid, title in options:
                try:
                    async with host_slot("magicmadhouse.co.uk"):
                        variant_response = await client.post(
                            PRODUCT_ATTRIBUTES_URL.format(
                                product_id=fallback.shop_ref
                            ),
                            data={
                                "action": "add",
                                "product_id": fallback.shop_ref or "",
                                "qty[]": "1",
                                f"attribute[{option_id}]": value_id,
                            },
                            headers={
                                "User-Agent": BROWSER_USER_AGENT,
                                "X-Requested-With": "XMLHttpRequest",
                                "Accept": "application/json",
                                "Referer": fallback.url,
                            },
                        )
                    variant_response.raise_for_status()
                    variant = self._offer_from_attributes(
                        variant_response.text,
                        fallback,
                        sid=sid,
                        title=title,
                    )
                except Exception:  # noqa: BLE001 - per-variant best effort
                    variant = None
                if variant is None:
                    option_failed = True
                    continue
                variants.append(variant)
            if option_failed and not any(
                variant.price_czk == fallback.price_czk
                and variant.condition is fallback.condition
                for variant in variants
            ):
                variants.append(fallback)
            return variants or [fallback]
        except Exception:  # noqa: BLE001 - enrichment degrades to BODL
            return [fallback]

    @staticmethod
    def _parse_condition_options(html: str) -> list[ConditionOption]:
        tree = HTMLParser(html)
        options: list[ConditionOption] = []
        for group in tree.css(".form-options--condition"):
            labels = {
                (label.attributes.get("for") or "").strip(): label
                for label in group.css("label[for]")
            }
            for input_node in group.css('input[name^="attribute["][value]'):
                name = (input_node.attributes.get("name") or "").strip()
                match = _ATTRIBUTE_NAME_RE.fullmatch(name)
                value_id = (input_node.attributes.get("value") or "").strip()
                if match is None or not value_id:
                    continue
                input_id = (input_node.attributes.get("id") or "").strip()
                label = labels.get(input_id)
                sid = (
                    input_node.attributes.get("data-sid")
                    or (label.attributes.get("data-sid") if label else "")
                    or ""
                ).strip()
                title = (
                    (label.attributes.get("title") or "").strip()
                    if label is not None
                    else ""
                )
                options.append((match.group(1), value_id, sid, title))
        return options

    def _offer_from_attributes(
        self,
        payload: str | dict[str, Any],
        fallback: Offer,
        *,
        sid: str,
        title: str,
    ) -> Offer | None:
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                return None
        else:
            parsed = payload
        if not isinstance(parsed, dict):
            return None
        data = parsed.get("data")
        if not isinstance(data, dict):
            return None
        price_gbp = self._price_from_mapping(data)
        if price_gbp is None:
            return None

        stock_qty = self._int_stock(data.get("available_to_sell"))
        if stock_qty is None:
            stock_qty = self._int_stock(data.get("available_on_hand"))
        stock_qty = stock_qty or 0
        if data.get("instock") is False or data.get("purchasable") is False:
            stock_qty = 0

        condition = normalize_condition(sid)
        if condition is Condition.UNKNOWN:
            condition = normalize_condition(title)
        return fallback.model_copy(
            update={
                "price_czk": int(round(price_gbp * self._gbp_to_czk)),
                "stock_qty": stock_qty,
                "condition": condition,
            }
        )

    @classmethod
    def _split_name(cls, raw_name: str) -> tuple[str, str | None]:
        if " | " in raw_name:
            name, edition = raw_name.rsplit(" | ", 1)
            edition = edition.strip() or None
        else:
            name, edition = raw_name, None
        name = _FOIL_PAREN_RE.sub("", name)
        name = _REVERSE_HOLO_RE.sub("", name)
        name = _TRAILING_BRACKET_RE.sub("", name)
        return " ".join(name.split()), edition

    @staticmethod
    def _custom_fields(value: Any) -> dict[str, str]:
        if not isinstance(value, list):
            return {}
        result: dict[str, str] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            name = " ".join(str(item.get("name") or "").split()).casefold()
            field_value = " ".join(
                unescape(str(item.get("value") or "")).split()
            )
            if name and field_value:
                result[name] = field_value
        return result

    @staticmethod
    def _price_from_mapping(value: dict[str, Any]) -> float | None:
        price = value.get("price")
        with_tax = price.get("with_tax") if isinstance(price, dict) else None
        raw = with_tax.get("value") if isinstance(with_tax, dict) else None
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            return None
        return amount if math.isfinite(amount) and amount >= 0 else None

    @classmethod
    def _listing_stock(cls, product: dict[str, Any]) -> int:
        stock = cls._int_stock(product.get("stock_level"))
        if stock is not None:
            return stock
        availability = cls._clean_text(product.get("availability")).casefold()
        if any(
            marker in availability
            for marker in ("out of stock", "unavailable", "not available")
        ):
            return 0
        if any(
            marker in availability
            for marker in ("dispatch", "in stock", "available")
        ):
            return 1
        return 0

    @staticmethod
    def _int_stock(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            return max(0, int(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _set_code_from_sku(sku: str) -> str | None:
        parts = sku.split("-")
        if len(parts) < 3 or not parts[1]:
            return None
        return parts[1].upper()

    @staticmethod
    def _foil_from(sku: str, raw_name: str, single_cards: str) -> bool:
        single_cards_folded = single_cards.casefold()
        custom_foil = (
            "foil" in single_cards_folded
            and "non-foil" not in single_cards_folded
            and "non foil" not in single_cards_folded
        )
        number_segment = sku.rsplit("-", 1)[-1]
        sku_foil = bool(re.match(r"[AB]\d", number_segment, re.IGNORECASE))
        return (
            bool(_FOIL_PAREN_RE.search(raw_name))
            or custom_foil
            or sku_foil
        )

    @staticmethod
    def _language_code(language: str | None) -> str | None:
        if not language:
            return None
        codes = {
            "english": "EN",
            "german": "DE",
            "french": "FR",
            "italian": "IT",
            "spanish": "ES",
            "japanese": "JP",
        }
        return codes.get(language.casefold(), language)

    @staticmethod
    def _canonical_url(url: str) -> str:
        if not url:
            return ""
        split = urllib.parse.urlsplit(urllib.parse.urljoin(BASE, url))
        return urllib.parse.urlunsplit(
            (split.scheme, split.netloc, split.path, "", "")
        )

    @staticmethod
    def _clean_text(value: Any) -> str:
        return " ".join(unescape(str(value or "")).replace("\xa0", " ").split())

    @staticmethod
    def _name_matches(wanted: str, candidate: str) -> bool:
        wanted_key = _NAME_TOKEN_RE.sub(" ", wanted.casefold()).strip()
        candidate_key = _NAME_TOKEN_RE.sub(" ", candidate.casefold()).strip()
        return bool(wanted_key and wanted_key in candidate_key)
