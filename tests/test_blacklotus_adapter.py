from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.blacklotus import BlackLotusAdapter
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> BlackLotusAdapter:
    return BlackLotusAdapter()


@pytest.mark.asyncio
async def test_parses_lightning_bolt_fixture(load_fixture, adapter: BlackLotusAdapter):
    html = load_fixture("blacklotus_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))

    assert offers, "expected at least one offer"
    for o in offers:
        assert o.shop == "blacklotus"
        assert "lightning bolt" in o.card_name.lower()
        assert o.price_czk > 0
        assert o.url.startswith("https://www.blacklotus.cz/")


@pytest.mark.asyncio
async def test_in_stock_filter(load_fixture, adapter: BlackLotusAdapter):
    html = load_fixture("blacklotus_lightning_bolt.html")
    all_offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    in_stock = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=True))

    assert len(in_stock) <= len(all_offers)
    assert all(o.stock_qty > 0 for o in in_stock)


@pytest.mark.asyncio
async def test_alt_text_foil_and_condition(load_fixture, adapter: BlackLotusAdapter):
    html = load_fixture("blacklotus_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))

    # Fixture is known to contain Foil ANO, Foil NE, and various Stav values.
    assert any(o.foil for o in offers), "expected at least one foil offer"
    assert any(o.condition == Condition.NM for o in offers)
    assert any(o.condition == Condition.LP for o in offers), \
        "expected at least one Light Played offer"


@pytest.mark.asyncio
async def test_edition_extracted_from_description(load_fixture, adapter: BlackLotusAdapter):
    html = load_fixture("blacklotus_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    # Many products carry edition; at least one should resolve.
    assert any(o.edition for o in offers), "expected at least one offer with edition"
