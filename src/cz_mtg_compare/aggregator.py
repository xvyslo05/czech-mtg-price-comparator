from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from .adapters import ShopAdapter, build_default_adapters
from .cache import TTLCache
from .filters import filter_playable
from .models import Offer, SearchQuery, ShopId, ShopStatus

log = logging.getLogger(__name__)

PER_SHOP_TIMEOUT_S = 20.0
CACHE_TTL_S = 600.0  # 10 min


class Aggregator:
    def __init__(self, adapters: Iterable[ShopAdapter] | None = None) -> None:
        self._adapters: list[ShopAdapter] = list(adapters) if adapters is not None else build_default_adapters()
        self._cache: TTLCache[list[Offer]] = TTLCache(CACHE_TTL_S)
        self._last_status: dict[ShopId, ShopStatus] = {
            a.shop_id: ShopStatus(shop=a.shop_id, ok=True) for a in self._adapters
        }

    @property
    def shop_ids(self) -> list[ShopId]:
        return [a.shop_id for a in self._adapters]

    async def search(
        self, query: SearchQuery, shops: Iterable[ShopId] | None = None
    ) -> list[Offer]:
        target_ids = set(shops) if shops else None
        adapters = [a for a in self._adapters if target_ids is None or a.shop_id in target_ids]

        coros = [self._run_one(a, query) for a in adapters]
        results = await asyncio.gather(*coros, return_exceptions=True)

        offers: list[Offer] = []
        for adapter, result in zip(adapters, results):
            if isinstance(result, BaseException):
                self._last_status[adapter.shop_id] = ShopStatus(
                    shop=adapter.shop_id,
                    ok=False,
                    last_error=f"{type(result).__name__}: {result}",
                )
                log.warning("adapter %s failed: %r", adapter.shop_id, result)
                continue
            self._last_status[adapter.shop_id] = ShopStatus(
                shop=adapter.shop_id, ok=True, last_offer_count=len(result)
            )
            offers.extend(result)

        if not query.include_non_playable:
            offers = filter_playable(offers)

        offers.sort(key=lambda o: (o.price_czk, o.shop))
        return offers

    async def _run_one(self, adapter: ShopAdapter, query: SearchQuery) -> list[Offer]:
        key = f"{adapter.shop_id}|{query.name.lower().strip()}|{(query.edition or '').lower().strip()}|{int(query.in_stock_only)}"

        async def fetch() -> list[Offer]:
            return await asyncio.wait_for(adapter.search(query), timeout=PER_SHOP_TIMEOUT_S)

        return await self._cache.get_or_compute(key, fetch)

    def status(self) -> list[ShopStatus]:
        return [self._last_status[a.shop_id] for a in self._adapters]
