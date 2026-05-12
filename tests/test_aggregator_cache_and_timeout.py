from __future__ import annotations

import pytest

import cz_mtg_compare.aggregator as agg_mod
from cz_mtg_compare.adapters.base import ShopAdapter
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import SearchQuery

from ._factories import StubAdapter, make_offer


@pytest.mark.asyncio
async def test_repeat_query_within_ttl_hits_cache():
    adapter = StubAdapter("tolarie", [make_offer("tolarie", "Lightning Bolt")])
    agg = Aggregator([adapter])

    await agg.search(SearchQuery(name="Lightning Bolt"))
    await agg.search(SearchQuery(name="Lightning Bolt"))
    await agg.search(SearchQuery(name="Lightning Bolt"))

    assert adapter.call_count == 1


@pytest.mark.asyncio
async def test_different_queries_each_hit_adapter():
    adapter = StubAdapter("tolarie", [make_offer("tolarie", "X")])
    agg = Aggregator([adapter])

    await agg.search(SearchQuery(name="A"))
    await agg.search(SearchQuery(name="B"))
    await agg.search(SearchQuery(name="A", in_stock_only=False))  # different flag
    await agg.search(SearchQuery(name="A", edition="M10"))         # different edition

    assert adapter.call_count == 4


@pytest.mark.asyncio
async def test_cache_key_normalises_name_case_and_whitespace():
    adapter = StubAdapter("tolarie", [make_offer("tolarie", "Lightning Bolt")])
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

    fast = StubAdapter("tolarie", [make_offer("tolarie", "X")])
    slow = StubAdapter("najada", [], delay_s=2.0)
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
            return [make_offer("tolarie", "X")]

    agg = Aggregator([_Flaky()])

    offers1 = await agg.search(SearchQuery(name="A"))
    assert offers1 == []
    assert agg.status()[0].ok is False

    state["raise"] = False
    offers2 = await agg.search(SearchQuery(name="B"))  # different key avoids cache
    assert len(offers2) == 1
    assert agg.status()[0].ok is True
