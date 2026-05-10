from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.rishada import RishadaAdapter
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> RishadaAdapter:
    return RishadaAdapter()


@pytest.mark.asyncio
async def test_parses_lightning_bolt_fixture(load_fixture, adapter: RishadaAdapter):
    html = load_fixture("rishada_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))

    assert offers, "expected at least one parsed offer"
    for o in offers:
        assert o.shop == "rishada"
        assert "lightning bolt" in o.card_name.lower()
        assert o.url.startswith("https://www.rishada.cz/")
        # price may be 0 for cards listed without a current quote
        assert o.price_czk >= 0


@pytest.mark.asyncio
async def test_in_stock_filter_drops_zero_qty(load_fixture, adapter: RishadaAdapter):
    html = load_fixture("rishada_mountain.html")
    in_stock = await adapter.parse(html, SearchQuery(name="Mountain", in_stock_only=True))
    assert in_stock, "Mountain fixture should have in-stock entries"
    assert all(o.stock_qty > 0 for o in in_stock)
    # Most in-stock rows have a real price; the rare 0 Kč ones (promo / "ask us")
    # are kept because the filter is on stock, not price.
    assert any(o.price_czk > 0 for o in in_stock)


@pytest.mark.asyncio
async def test_judge_foil_marks_foil_true(load_fixture, adapter: RishadaAdapter):
    html = load_fixture("rishada_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    judge_foil = next((o for o in offers if "judge foil" in o.card_name.lower()), None)
    assert judge_foil is not None, "fixture contains a 'Lightning Bolt (judge foil)' row"
    assert judge_foil.foil is True


@pytest.mark.asyncio
async def test_default_condition_is_nm(load_fixture, adapter: RishadaAdapter):
    html = load_fixture("rishada_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    # Rishada displays "Near Mint" by default; non-NM rows would carry a different label.
    assert any(o.condition == Condition.NM for o in offers)


@pytest.mark.asyncio
async def test_edition_extracted(load_fixture, adapter: RishadaAdapter):
    html = load_fixture("rishada_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    editions = {o.edition for o in offers if o.edition}
    # Fixture is known to contain at least these edition names.
    assert any("strixhaven" in e.lower() or "baldur" in e.lower() for e in editions)


@pytest.mark.asyncio
async def test_edition_filter(load_fixture, adapter: RishadaAdapter):
    html = load_fixture("rishada_mountain.html")
    offers = await adapter.parse(
        html,
        SearchQuery(name="Mountain", edition="Shadowmoor", in_stock_only=False),
    )
    assert offers
    assert all("shadowmoor" in (o.edition or "").lower() for o in offers)
