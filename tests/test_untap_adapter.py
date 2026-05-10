from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.untap import UntapAdapter
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> UntapAdapter:
    return UntapAdapter()


@pytest.mark.asyncio
async def test_parses_lightning_bolt_fixture(load_fixture, adapter: UntapAdapter):
    html = load_fixture("untap_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))

    assert offers, "expected at least one parsed offer"
    for o in offers:
        assert o.shop == "untap"
        assert "lightning bolt" in o.card_name.lower()
        assert o.price_czk > 0
        assert o.url.startswith("https://untap.cz/")


@pytest.mark.asyncio
async def test_in_stock_filter_via_unavailable_badge(load_fixture, adapter: UntapAdapter):
    """Out-of-stock offers carry a `product-unavailable` Prestashop badge; the
    listing for Lightning Bolt is fully sold out, so in_stock_only=True must
    return zero offers without crashing."""
    html = load_fixture("untap_lightning_bolt.html")
    in_stock = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=True))
    all_offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    assert all_offers
    assert in_stock == []
    assert all(o.stock_qty == 0 for o in all_offers)


@pytest.mark.asyncio
async def test_in_stock_offers_are_returned(load_fixture, adapter: UntapAdapter):
    """The Counterspell fixture has at least a few in-stock entries."""
    html = load_fixture("untap_counterspell.html")
    in_stock = await adapter.parse(html, SearchQuery(name="Counterspell", in_stock_only=True))
    assert in_stock, "Counterspell fixture should have in-stock entries"
    assert all(o.stock_qty > 0 for o in in_stock)
    assert all(o.price_czk > 0 for o in in_stock)


@pytest.mark.asyncio
async def test_set_code_extracted_from_reference(load_fixture, adapter: UntapAdapter):
    """untap encodes set + collector# + condition in the product reference,
    e.g. M10#146#N. The set code (uppercased) must end up in offer.set_code."""
    html = load_fixture("untap_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    set_codes = {o.set_code for o in offers if o.set_code}
    assert "M10" in set_codes or "2X2" in set_codes


@pytest.mark.asyncio
async def test_foil_detected_from_reference_suffix(load_fixture, adapter: UntapAdapter):
    """A trailing #F on the product reference marks a foil variant."""
    html = load_fixture("untap_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    foils = [o for o in offers if o.foil]
    assert foils, "fixture contains foil variants (#F suffix in product reference)"
    # Foil flag should be parsed without leaving "- Foil" / "(Foil)" suffixes in the name.
    for o in foils:
        assert "foil" not in o.card_name.lower()


@pytest.mark.asyncio
async def test_default_condition_is_nm(load_fixture, adapter: UntapAdapter):
    html = load_fixture("untap_lightning_bolt.html")
    offers = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=False))
    assert all(o.condition == Condition.NM for o in offers)


@pytest.mark.asyncio
async def test_edition_filter(load_fixture, adapter: UntapAdapter):
    html = load_fixture("untap_lightning_bolt.html")
    offers = await adapter.parse(
        html,
        SearchQuery(name="Lightning Bolt", edition="2X2", in_stock_only=False),
    )
    assert offers
    assert all(
        ("2x2" in (o.set_code or "").lower())
        or ("double masters" in (o.edition or "").lower())
        for o in offers
    )
