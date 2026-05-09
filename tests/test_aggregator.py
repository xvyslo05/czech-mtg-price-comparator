from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.base import ShopAdapter
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import Condition, Offer, SearchQuery, ShopId


class _StubAdapter(ShopAdapter):
    def __init__(self, shop_id: ShopId, offers: list[Offer], raise_exc: Exception | None = None):
        self.shop_id = shop_id
        self.base_url = f"https://example.com/{shop_id}"
        self._offers = offers
        self._raise = raise_exc

    async def search(self, query: SearchQuery) -> list[Offer]:
        if self._raise is not None:
            raise self._raise
        return list(self._offers)


def _offer(shop: ShopId, price: int) -> Offer:
    return Offer(
        shop=shop,
        card_name="Lightning Bolt",
        edition="Beta",
        condition=Condition.NM,
        foil=False,
        price_czk=price,
        stock_qty=1,
        url="https://example.com/x",
    )


@pytest.mark.asyncio
async def test_merges_and_sorts_by_price():
    agg = Aggregator(
        [
            _StubAdapter("tolarie", [_offer("tolarie", 50)]),
            _StubAdapter("najada", [_offer("najada", 30), _offer("najada", 80)]),
        ]
    )
    offers = await agg.search(SearchQuery(name="Lightning Bolt"))
    assert [o.price_czk for o in offers] == [30, 50, 80]


@pytest.mark.asyncio
async def test_partial_failure_does_not_kill_the_query():
    agg = Aggregator(
        [
            _StubAdapter("tolarie", [_offer("tolarie", 50)]),
            _StubAdapter("najada", [], raise_exc=RuntimeError("boom")),
        ]
    )
    offers = await agg.search(SearchQuery(name="Lightning Bolt"))
    assert len(offers) == 1
    assert offers[0].shop == "tolarie"

    statuses = {s.shop: s for s in agg.status()}
    assert statuses["tolarie"].ok is True
    assert statuses["najada"].ok is False
    assert "boom" in (statuses["najada"].last_error or "")


@pytest.mark.asyncio
async def test_shop_filter():
    agg = Aggregator(
        [
            _StubAdapter("tolarie", [_offer("tolarie", 50)]),
            _StubAdapter("najada", [_offer("najada", 30)]),
        ]
    )
    offers = await agg.search(SearchQuery(name="Lightning Bolt"), shops=["tolarie"])
    assert [o.shop for o in offers] == ["tolarie"]
