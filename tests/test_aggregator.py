from __future__ import annotations

import pytest

from cz_mtg_compare.adapters import build_default_adapters
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import SearchQuery

from ._factories import StubAdapter, make_offer

NEW_SHOPS = {
    "axionnow",
    "mtgspot",
    "magiccorporation",
    "jkentertainment",
    "bazaarofmagic",
    "spellenwinkel",
}


def _offer(shop, price):
    return make_offer(shop=shop, price=price, edition="Beta")


@pytest.mark.asyncio
async def test_merges_and_sorts_by_price():
    agg = Aggregator(
        [
            StubAdapter("tolarie", [_offer("tolarie", 50)]),
            StubAdapter("najada", [_offer("najada", 30), _offer("najada", 80)]),
        ]
    )
    offers = await agg.search(SearchQuery(name="Lightning Bolt"))
    assert [o.price_czk for o in offers] == [30, 50, 80]


@pytest.mark.asyncio
async def test_partial_failure_does_not_kill_the_query():
    agg = Aggregator(
        [
            StubAdapter("tolarie", [_offer("tolarie", 50)]),
            StubAdapter("najada", [], raise_exc=RuntimeError("boom")),
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
            StubAdapter("tolarie", [_offer("tolarie", 50)]),
            StubAdapter("najada", [_offer("najada", 30)]),
        ]
    )
    offers = await agg.search(SearchQuery(name="Lightning Bolt"), shops=["tolarie"])
    assert [o.shop for o in offers] == ["tolarie"]


def test_all_new_shops_are_registered_by_default():
    aggregator = Aggregator(build_default_adapters())
    assert all(aggregator.get_adapter(shop) is not None for shop in NEW_SHOPS)
