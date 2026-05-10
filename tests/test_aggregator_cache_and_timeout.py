from __future__ import annotations

import asyncio

import pytest

import cz_mtg_compare.aggregator as agg_mod
from cz_mtg_compare.adapters.base import ShopAdapter
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import Condition, Offer, SearchQuery, ShopId


class _CountingAdapter(ShopAdapter):
    def __init__(self, shop_id: ShopId, offers: list[Offer]):
        self.shop_id = shop_id
        self.base_url = f"https://example.com/{shop_id}"
        self._offers = offers
        self.call_count = 0

    async def search(self, query: SearchQuery) -> list[Offer]:
        self.call_count += 1
        return list(self._offers)


class _SlowAdapter(ShopAdapter):
    def __init__(self, shop_id: ShopId, delay_s: float):
        self.shop_id = shop_id
        self.base_url = f"https://example.com/{shop_id}"
        self._delay = delay_s

    async def search(self, query: SearchQuery) -> list[Offer]:
        await asyncio.sleep(self._delay)
        return []


def _o(shop: ShopId, name: str, price: int = 50) -> Offer:
    return Offer(
        shop=shop,
        card_name=name,
        edition="X",
        condition=Condition.NM,
        foil=False,
        price_czk=price,
        stock_qty=1,
        url="https://example.com",
    )


@pytest.mark.asyncio
async def test_repeat_query_within_ttl_hits_cache():
    adapter = _CountingAdapter("tolarie", [_o("tolarie", "Lightning Bolt")])
    agg = Aggregator([adapter])

    await agg.search(SearchQuery(name="Lightning Bolt"))
    await agg.search(SearchQuery(name="Lightning Bolt"))
    await agg.search(SearchQuery(name="Lightning Bolt"))

    assert adapter.call_count == 1


@pytest.mark.asyncio
async def test_different_queries_each_hit_adapter():
    adapter = _CountingAdapter("tolarie", [_o("tolarie", "X")])
    agg = Aggregator([adapter])

    await agg.search(SearchQuery(name="A"))
    await agg.search(SearchQuery(name="B"))
    await agg.search(SearchQuery(name="A", in_stock_only=False))  # different flag
    await agg.search(SearchQuery(name="A", edition="M10"))         # different edition

    assert adapter.call_count == 4


@pytest.mark.asyncio
async def test_cache_key_normalises_name_case_and_whitespace():
    adapter = _CountingAdapter("tolarie", [_o("tolarie", "Lightning Bolt")])
    agg = Aggregator([adapter])

    await agg.search(SearchQuery(name="Lightning Bolt"))
    await agg.search(SearchQuery(name="  lightning bolt  "))
    await agg.search(SearchQuery(name="LIGHTNING BOLT"))

    assert adapter.call_count == 1


@pytest.mark.asyncio
async def test_slow_adapter_is_killed_at_per_shop_timeout(monkeypatch):
    """Adapters that exceed PER_SHOP_TIMEOUT_S must be cancelled, not block
    the whole query. Failure surfaces through list_shops()."""
    monkeypatch.setattr(agg_mod, "PER_SHOP_TIMEOUT_S", 0.05)

    fast = _CountingAdapter("tolarie", [_o("tolarie", "X")])
    slow = _SlowAdapter("najada", delay_s=2.0)
    agg = Aggregator([fast, slow])

    offers = await agg.search(SearchQuery(name="X"))

    # Fast shop's results come through.
    assert any(o.shop == "tolarie" for o in offers)
    assert not any(o.shop == "najada" for o in offers)

    # Status reflects the timeout.
    statuses = {s.shop: s for s in agg.status()}
    assert statuses["tolarie"].ok is True
    assert statuses["najada"].ok is False
    assert statuses["najada"].last_error is not None
    assert "Timeout" in statuses["najada"].last_error or "timeout" in statuses["najada"].last_error.lower()


@pytest.mark.asyncio
async def test_failed_shop_recovery_on_next_call():
    """A shop that failed once should get retried on the next query."""
    state = {"raise": True}

    class _Flaky(ShopAdapter):
        shop_id = "tolarie"
        base_url = "https://example.com"
        async def search(self, query):
            if state["raise"]:
                raise RuntimeError("transient")
            return [_o("tolarie", "X")]

    agg = Aggregator([_Flaky()])

    offers1 = await agg.search(SearchQuery(name="A"))
    assert offers1 == []
    assert agg.status()[0].ok is False

    state["raise"] = False
    offers2 = await agg.search(SearchQuery(name="B"))  # different key avoids cache
    assert len(offers2) == 1
    assert agg.status()[0].ok is True
