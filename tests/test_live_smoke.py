"""Live smoke tests — opt in with `pytest -m live`. These hit real shop websites."""
from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.blacklotus import BlackLotusAdapter
from cz_mtg_compare.adapters.cernyrytir import CernyRytirAdapter
from cz_mtg_compare.adapters.najada import NajadaAdapter
from cz_mtg_compare.adapters.rishada import RishadaAdapter
from cz_mtg_compare.adapters.tolarie import TolarieAdapter
from cz_mtg_compare.adapters.untap import UntapAdapter
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.http_client import close_client
from cz_mtg_compare.models import SearchQuery


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter_cls, shop",
    [
        (TolarieAdapter, "tolarie"),
        (NajadaAdapter, "najada"),
        (BlackLotusAdapter, "blacklotus"),
        (CernyRytirAdapter, "cernyrytir"),
        (RishadaAdapter, "rishada"),
        (UntapAdapter, "untap"),
    ],
)
async def test_adapter_live_lightning_bolt(adapter_cls, shop):
    adapter = adapter_cls()
    offers = await adapter.search(SearchQuery(name="Lightning Bolt", in_stock_only=True))
    # Out-of-stock is acceptable for some shops at any moment, but we still want a parse.
    assert all(o.shop == shop for o in offers)
    assert all(o.price_czk > 0 for o in offers)


@pytest.mark.live
@pytest.mark.asyncio
async def test_aggregator_live_returns_offers_from_multiple_shops():
    agg = Aggregator()
    try:
        offers = await agg.search(SearchQuery(name="Lightning Bolt", in_stock_only=True))
    finally:
        await close_client()
    shops = {o.shop for o in offers}
    # Lightning Bolt is ubiquitous; expect at least 2 shops to return something.
    assert len(shops) >= 2, f"expected >=2 shops to return offers, got {shops}"
    # Sorted ascending by price.
    prices = [o.price_czk for o in offers]
    assert prices == sorted(prices)
