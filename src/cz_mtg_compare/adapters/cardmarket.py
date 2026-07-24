from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .. import fx
from ..http_client import get_client, host_slot
from ..models import Condition, Offer, SearchQuery
from .base import ShopAdapter

log = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.cardmarket.com/ws/v2.0/output.json"
DEFAULT_EUR_TO_CZK = fx.STATIC_DEFAULTS["EUR"]
MTG_GAME_ID = 1


@dataclass(frozen=True)
class MkmCredentials:
    app_token: str
    app_secret: str
    access_token: str
    access_token_secret: str
    api_base: str = DEFAULT_API_BASE

    @classmethod
    def from_env(cls) -> "MkmCredentials | None":
        keys = ("MKM_APP_TOKEN", "MKM_APP_SECRET", "MKM_ACCESS_TOKEN", "MKM_ACCESS_TOKEN_SECRET")
        values = [os.environ.get(k) for k in keys]
        if not all(values):
            return None
        return cls(
            app_token=values[0],
            app_secret=values[1],
            access_token=values[2],
            access_token_secret=values[3],
            api_base=os.environ.get("MKM_API_BASE", DEFAULT_API_BASE),
        )


def _percent_encode(value: str) -> str:
    """RFC 3986 percent-encoding for OAuth1 — slightly stricter than urllib's quote."""
    return urllib.parse.quote(value, safe="")


def build_oauth1_authorization_header(
    method: str,
    url: str,
    creds: MkmCredentials,
    *,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Build an MKM-flavoured OAuth1 Authorization header.

    MKM uses the request URL as the OAuth realm, signs the canonical base string
    with HMAC-SHA1, and uses `app_secret&access_token_secret` as the signing key.
    See https://api.cardmarket.com/ws/documentation/API_2.0:Auth_Header
    """
    nonce = nonce or secrets.token_hex(16)
    timestamp = timestamp or str(int(time.time()))
    realm = url
    oauth_params = {
        "oauth_consumer_key": creds.app_token,
        "oauth_token": creds.access_token,
        "oauth_nonce": nonce,
        "oauth_timestamp": timestamp,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0",
    }

    parameter_string = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}"
        for k, v in sorted(oauth_params.items())
    )
    base_string = "&".join([
        method.upper(),
        _percent_encode(realm),
        _percent_encode(parameter_string),
    ])
    signing_key = f"{_percent_encode(creds.app_secret)}&{_percent_encode(creds.access_token_secret)}"
    signature = base64.b64encode(
        hmac.new(signing_key.encode("ascii"), base_string.encode("ascii"), hashlib.sha1).digest()
    ).decode("ascii")

    header_params = {**oauth_params, "oauth_signature": signature}
    parts = [f'realm="{realm}"'] + [
        f'{k}="{_percent_encode(v)}"' for k, v in sorted(header_params.items())
    ]
    return "OAuth " + ", ".join(parts)


class CardmarketAdapter(ShopAdapter):
    shop_id = "cardmarket"
    base_url = "https://www.cardmarket.com"

    def __init__(
        self,
        credentials: MkmCredentials | None = None,
        *,
        eur_to_czk: float | None = None,
        max_results: int = 5,
    ) -> None:
        self._creds = credentials
        self._eur_to_czk_constructor_override = eur_to_czk
        self._eur_to_czk_override = (
            eur_to_czk
            if eur_to_czk is not None
            else fx.rate_from_env("MKM_EUR_TO_CZK")
        )
        self._eur_to_czk = fx.rate_to_czk_nolive(
            "EUR",
            override=self._eur_to_czk_override,
        )
        self._max_results = max_results

    @property
    def configured(self) -> bool:
        return self._creds is not None

    async def search(self, query: SearchQuery) -> list[Offer]:
        if self._creds is None:
            return []
        if self._eur_to_czk_constructor_override is not None:
            eur_to_czk = self._eur_to_czk
        elif (mkm_rate := fx.rate_from_env("MKM_EUR_TO_CZK")) is not None:
            eur_to_czk = mkm_rate
        else:
            eur_to_czk = await fx.rate_to_czk("EUR")
        return await self._search_with_creds(
            query,
            self._creds,
            eur_to_czk,
        )

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        # Cardmarket doesn't have an HTML fixture path; this slot is unused.
        raise NotImplementedError

    async def _search_with_creds(
        self,
        query: SearchQuery,
        creds: MkmCredentials,
        eur_to_czk: float,
    ) -> list[Offer]:
        client = await get_client()
        find_url = f"{creds.api_base}/products/find"
        find_url_full = f"{find_url}?{urllib.parse.urlencode({'search': query.name, 'idGame': MTG_GAME_ID, 'exact': 'false'})}"
        headers = {"Authorization": build_oauth1_authorization_header("GET", find_url_full, creds)}
        async with host_slot("api.cardmarket.com"):
            resp = await client.get(find_url_full, headers=headers)
        if resp.status_code in (401, 403):
            log.warning("cardmarket auth failed: %s", resp.text[:200])
            return []
        resp.raise_for_status()
        return self._parse_find_payload(resp.json(), query, eur_to_czk)

    def _parse_find_payload(
        self,
        payload: dict[str, Any],
        query: SearchQuery,
        eur_to_czk: float | None = None,
    ) -> list[Offer]:
        rate = self._eur_to_czk if eur_to_czk is None else eur_to_czk
        products = payload.get("product") or []
        offers: list[Offer] = []
        wanted = query.name.lower()
        edition_filter = (query.edition or "").lower().strip() or None

        for product in products[: self._max_results]:
            name = (product.get("enName") or product.get("name") or "").strip()
            if not name or wanted not in name.lower():
                continue
            expansion = (
                product.get("expansionName")
                or (product.get("expansion") or {}).get("enName")
                or None
            )
            set_code = (
                (product.get("expansion") or {}).get("abbreviation")
                or product.get("expansionCode")
                or None
            )
            if edition_filter and (
                (not expansion or edition_filter not in expansion.lower())
                and (not set_code or edition_filter not in set_code.lower())
            ):
                continue

            price_eur = self._extract_price_eur(product)
            if price_eur is None:
                continue
            price_czk = int(round(price_eur * rate))

            url = (
                product.get("website")
                or product.get("url")
                or f"https://www.cardmarket.com/en/Magic/Products/Singles/{urllib.parse.quote(name)}"
            )

            offers.append(
                Offer(
                    shop="cardmarket",
                    card_name=name,
                    edition=expansion,
                    set_code=set_code.upper() if set_code else None,
                    condition=Condition.UNKNOWN,  # priceGuide is an aggregate, not a specific article
                    language=None,
                    foil=False,
                    price_czk=price_czk,
                    price_native=price_eur,
                    currency="EUR",
                    stock_qty=1,  # priceGuide implies sellers exist; specific stock not exposed
                    url=url,
                )
            )

            # If the product carries a foil price guide, surface it as a second offer.
            foil_price = self._extract_foil_price_eur(product)
            if foil_price is not None:
                offers.append(
                    Offer(
                        shop="cardmarket",
                        card_name=name,
                        edition=expansion,
                        set_code=set_code.upper() if set_code else None,
                        condition=Condition.UNKNOWN,
                        language=None,
                        foil=True,
                        price_czk=int(round(foil_price * rate)),
                        price_native=foil_price,
                        currency="EUR",
                        stock_qty=1,
                        url=url,
                    )
                )
        return offers

    @staticmethod
    def _extract_price_eur(product: dict[str, Any]) -> float | None:
        guide = product.get("priceGuide") or {}
        for key in ("TREND", "AVG", "LOW"):
            v = guide.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        # Fallback: top-level price hints
        for key in ("priceFrom", "lowest", "averagePrice"):
            v = product.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return None

    @staticmethod
    def _extract_foil_price_eur(product: dict[str, Any]) -> float | None:
        guide = product.get("priceGuide") or {}
        for key in ("TRENDFOIL", "AVGFOIL", "FOIL_LOW", "LOWFOIL"):
            v = guide.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return None
