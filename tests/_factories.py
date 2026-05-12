"""Shared test factories.

Before this module existed, each test file defined its own near-identical
``_StubAdapter`` + ``_o`` / ``_offer`` helpers — 7 copies with subtly different
signatures, which made refactors painful (a new ``Offer`` field meant editing
seven places). This module centralises both: one ``make_offer`` builder and
one ``StubAdapter`` that covers every mode the suite needs.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping

from cz_mtg_compare.adapters.base import ShopAdapter
from cz_mtg_compare.models import Condition, Offer, SearchQuery, ShopId


def make_offer(
    shop: ShopId = "tolarie",
    name: str = "Lightning Bolt",
    price: int = 50,
    *,
    edition: str | None = "X",
    set_code: str | None = None,
    condition: Condition = Condition.NM,
    language: str | None = None,
    foil: bool = False,
    stock_qty: int = 1,
    url: str | None = None,
) -> Offer:
    return Offer(
        shop=shop,
        card_name=name,
        edition=edition,
        set_code=set_code,
        condition=condition,
        language=language,
        foil=foil,
        price_czk=price,
        stock_qty=stock_qty,
        url=url or f"https://example.com/{shop}",
    )


class StubAdapter(ShopAdapter):
    """Test adapter covering every mode the suite needs.

    Pick one of:
      * ``offers``  — list returned for every query.
      * ``table``   — mapping ``query.name`` (case-insensitive) → offers.

    Optional knobs:
      * ``raise_exc``         — raise this on search (covers _Flaky-style cases).
      * ``delay_s``           — sleep before responding (timeout / concurrency).
      * ``track_concurrency`` — record peak in-flight on ``self.peak_concurrency``.

    Always exposes ``self.call_count``. ``in_stock_only`` queries automatically
    drop offers with ``stock_qty <= 0``, matching real adapter semantics.
    """

    def __init__(
        self,
        shop_id: ShopId,
        offers: Iterable[Offer] | None = None,
        *,
        table: Mapping[str, Iterable[Offer]] | None = None,
        raise_exc: BaseException | None = None,
        delay_s: float = 0.0,
        track_concurrency: bool = False,
    ) -> None:
        if offers is not None and table is not None:
            raise ValueError("StubAdapter accepts either offers or table, not both")
        self.shop_id = shop_id
        self.base_url = f"https://example.com/{shop_id}"
        self._offers: list[Offer] | None = list(offers) if offers is not None else None
        self._table: dict[str, list[Offer]] = (
            {k.lower(): list(v) for k, v in table.items()} if table else {}
        )
        self._raise = raise_exc
        self._delay = delay_s
        self._track = track_concurrency
        self.call_count = 0
        self.peak_concurrency = 0
        self._in_flight = 0
        self._lock = asyncio.Lock()

    async def search(self, query: SearchQuery) -> list[Offer]:
        self.call_count += 1
        if self._track:
            async with self._lock:
                self._in_flight += 1
                self.peak_concurrency = max(self.peak_concurrency, self._in_flight)
        try:
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            if self._raise is not None:
                raise self._raise
            if self._offers is not None:
                offers = list(self._offers)
            else:
                offers = list(self._table.get(query.name.lower(), []))
            if query.in_stock_only:
                offers = [o for o in offers if o.stock_qty > 0]
            return offers
        finally:
            if self._track:
                async with self._lock:
                    self._in_flight -= 1
