from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.cernyrytir import CernyRytirAdapter
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> CernyRytirAdapter:
    return CernyRytirAdapter()


@pytest.mark.asyncio
async def test_parses_lightning_bolt_fixture(load_fixture, adapter: CernyRytirAdapter):
    html = load_fixture("cernyrytir_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))

    assert offers, "expected at least one offer"
    for o in offers:
        assert o.shop == "cernyrytir"
        assert "lightning bolt" in o.card_name.lower()
        assert o.price_czk > 0
        assert o.url.startswith("https://www.cernyrytir.cz/")


@pytest.mark.asyncio
async def test_in_stock_filter_drops_zero(load_fixture, adapter: CernyRytirAdapter):
    html = load_fixture("cernyrytir_lightning_bolt.html")
    in_stock = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=True))
    assert in_stock, "expected at least one stocked Lightning Bolt"
    assert all(o.stock_qty > 0 for o in in_stock)


@pytest.mark.asyncio
async def test_set_code_and_edition_extracted(load_fixture, adapter: CernyRytirAdapter):
    html = load_fixture("cernyrytir_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    assert any(o.set_code for o in offers)
    assert any(o.edition for o in offers)


@pytest.mark.asyncio
async def test_foil_and_condition_suffix_parsed(load_fixture, adapter: CernyRytirAdapter):
    html = load_fixture("cernyrytir_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    # Fixture contains 'Lightning Bolt - lightly played' rows.
    assert any(o.condition == Condition.LP for o in offers), \
        "expected at least one Lightly Played offer"
    # Foil rows may or may not exist for Lightning Bolt; just check name is clean if so.
    for o in offers:
        assert "- foil" not in o.card_name.lower()
        assert "- lightly" not in o.card_name.lower()
