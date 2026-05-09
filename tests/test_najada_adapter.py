from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.najada import NajadaAdapter
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> NajadaAdapter:
    return NajadaAdapter()


@pytest.mark.asyncio
async def test_parses_lightning_bolt_json(load_fixture, adapter: NajadaAdapter):
    payload = load_fixture("najada_lightning_bolt.json")
    offers = await adapter.parse(payload, SearchQuery(name="Lightning Bolt", in_stock_only=False))

    assert offers, "expected at least one offer"
    for o in offers:
        assert o.shop == "najada"
        assert "lightning bolt" in o.card_name.lower()
        assert o.price_czk > 0
        assert o.condition in Condition
        assert o.url.startswith("https://najada.games/vyhledavani")


@pytest.mark.asyncio
async def test_in_stock_filter(load_fixture, adapter: NajadaAdapter):
    payload = load_fixture("najada_lightning_bolt.json")
    all_offers = await adapter.parse(payload, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    in_stock = await adapter.parse(payload, SearchQuery(name="Lightning Bolt", in_stock_only=True))

    assert len(in_stock) <= len(all_offers)
    assert all(o.stock_qty > 0 for o in in_stock)


@pytest.mark.asyncio
async def test_foil_and_language_carried_through(load_fixture, adapter: NajadaAdapter):
    payload = load_fixture("najada_lightning_bolt.json")
    offers = await adapter.parse(payload, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    # Fixture is known to have at least one foil article and at least one EN-language article.
    assert any(o.foil for o in offers), "expected at least one foil offer"
    assert any(o.language == "EN" for o in offers), "expected at least one EN offer"


@pytest.mark.asyncio
async def test_set_code_resolved(load_fixture, adapter: NajadaAdapter):
    payload = load_fixture("najada_lightning_bolt.json")
    offers = await adapter.parse(payload, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    assert any(o.set_code for o in offers), "expected expansion.short_code to populate set_code"
