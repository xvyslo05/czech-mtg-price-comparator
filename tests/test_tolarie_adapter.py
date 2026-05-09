from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.tolarie import TolarieAdapter
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> TolarieAdapter:
    return TolarieAdapter()


@pytest.mark.asyncio
async def test_parses_lightning_bolt_fixture(load_fixture, adapter: TolarieAdapter):
    html = load_fixture("tolarie_lightning_bolt.html")
    query = SearchQuery(name="Lightning Bolt", in_stock_only=False)
    offers = await adapter.parse(html, query)

    assert offers, "expected at least one offer"
    for o in offers:
        assert o.shop == "tolarie"
        assert "lightning bolt" in o.card_name.lower()
        assert o.price_czk > 0
        assert o.url.startswith("https://www.tolarie.cz/vyhledavani/")
        assert o.condition in Condition  # any valid enum value


@pytest.mark.asyncio
async def test_in_stock_filter(load_fixture, adapter: TolarieAdapter):
    html = load_fixture("tolarie_lightning_bolt.html")

    all_offers = await adapter.parse(
        html, SearchQuery(name="Lightning Bolt", in_stock_only=False)
    )
    in_stock = await adapter.parse(
        html, SearchQuery(name="Lightning Bolt", in_stock_only=True)
    )

    assert len(in_stock) <= len(all_offers)
    assert all(o.stock_qty > 0 for o in in_stock)


@pytest.mark.asyncio
async def test_detects_foil_via_priznak_or_suffix(load_fixture, adapter: TolarieAdapter):
    html = load_fixture("tolarie_lightning_bolt.html")
    offers = await adapter.parse(
        html, SearchQuery(name="Lightning Bolt", in_stock_only=False)
    )
    # Fixture is known to contain at least one foil row.
    assert any(o.foil for o in offers), "expected at least one foil offer in fixture"
    # Foil should not stay in the canonical card_name.
    assert all("(foil)" not in o.card_name.lower() for o in offers)


@pytest.mark.asyncio
async def test_played_condition_detected(load_fixture, adapter: TolarieAdapter):
    html = load_fixture("tolarie_lightning_bolt.html")
    offers = await adapter.parse(
        html, SearchQuery(name="Lightning Bolt", in_stock_only=False)
    )
    # Fixture contains at least one Played + one Slightly played row (per priznak inspection).
    assert any(o.condition == Condition.PL for o in offers), "expected a Played offer"
    assert any(o.condition == Condition.LP for o in offers), "expected a Slightly Played offer"


@pytest.mark.asyncio
async def test_edition_filter(load_fixture, adapter: TolarieAdapter):
    html = load_fixture("tolarie_lightning_bolt.html")
    offers = await adapter.parse(
        html,
        SearchQuery(name="Lightning Bolt", edition="Strixhaven", in_stock_only=False),
    )
    assert offers
    assert all("strixhaven" in (o.edition or "").lower() for o in offers)
