from __future__ import annotations

import re

import pytest

from cz_mtg_compare.adapters.jkentertainment import JkEntertainmentAdapter
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> JkEntertainmentAdapter:
    return JkEntertainmentAdapter(eur_to_czk=25.0)


@pytest.mark.asyncio
async def test_parses_lightning_bolt_fixture(
    load_fixture,
    adapter: JkEntertainmentAdapter,
):
    offers = await adapter.parse(
        load_fixture("jkentertainment_lightning_bolt.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert len(offers) == 5
    for offer in offers:
        assert offer.shop == "jkentertainment"
        assert "lightning bolt" in offer.card_name.lower()
        assert offer.price_czk > 0
        assert offer.price_native is not None
        assert offer.currency == "EUR"
        assert offer.url.startswith("https://www.jk-entertainment.de/")


@pytest.mark.asyncio
async def test_condition_and_foil_from_item_id(
    load_fixture,
    adapter: JkEntertainmentAdapter,
):
    offers = await adapter.parse(
        load_fixture("jkentertainment_lightning_bolt.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert any(offer.condition is Condition.GD for offer in offers)
    assert any(offer.condition is Condition.NM for offer in offers)
    assert all(offer.foil is False for offer in offers)


@pytest.mark.asyncio
async def test_edition_split_from_item_name(
    load_fixture,
    adapter: JkEntertainmentAdapter,
):
    offers = await adapter.parse(
        load_fixture("jkentertainment_lightning_bolt.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    fourth = next(offer for offer in offers if offer.edition == "Fourth Edition")

    assert fourth.card_name == "Lightning Bolt"
    assert " - " not in fourth.card_name


@pytest.mark.asyncio
async def test_eur_to_czk_conversion(
    load_fixture,
    adapter: JkEntertainmentAdapter,
):
    offers = await adapter.parse(
        load_fixture("jkentertainment_lightning_bolt.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    fourth = next(offer for offer in offers if offer.edition == "Fourth Edition")
    assert fourth.price_czk == round(1.63 * 25.0)


@pytest.mark.asyncio
async def test_language_mapping(
    load_fixture,
    adapter: JkEntertainmentAdapter,
):
    offers = await adapter.parse(
        load_fixture("jkentertainment_lightning_bolt.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    assert {"German", "English"}.issubset(
        {offer.language for offer in offers}
    )


@pytest.mark.asyncio
async def test_multi_tcg_filtered_out(
    load_fixture,
    adapter: JkEntertainmentAdapter,
):
    offers = await adapter.parse(
        load_fixture("jkentertainment_sol_ring.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )

    assert offers
    assert all("sol ring" in offer.card_name.lower() for offer in offers)
    assert all("millennium ring" not in offer.card_name.lower() for offer in offers)
    assert all("ring announcer" not in offer.card_name.lower() for offer in offers)


@pytest.mark.asyncio
async def test_edition_filter(
    load_fixture,
    adapter: JkEntertainmentAdapter,
):
    offers = await adapter.parse(
        load_fixture("jkentertainment_lightning_bolt.html"),
        SearchQuery(
            name="Lightning Bolt",
            edition="Fourth",
            in_stock_only=False,
        ),
    )
    assert len(offers) == 1
    assert offers[0].edition == "Fourth Edition"


@pytest.mark.asyncio
async def test_edition_filter_uses_aligned_dom_card(
    load_fixture,
    adapter: JkEntertainmentAdapter,
):
    offers = await adapter.parse(
        load_fixture("jkentertainment_lightning_bolt.html"),
        SearchQuery(
            name="Lightning Bolt",
            edition="Anthologies",
            in_stock_only=False,
        ),
    )

    assert len(offers) == 1
    assert offers[0].edition == "Anthologies"


@pytest.mark.asyncio
async def test_shop_ref_is_uuid(
    load_fixture,
    adapter: JkEntertainmentAdapter,
):
    offers = await adapter.parse(
        load_fixture("jkentertainment_counterspell.html"),
        SearchQuery(name="Counterspell", in_stock_only=False),
    )

    assert len(offers) == 9
    assert all(
        re.fullmatch(r"[0-9a-f]{32}", offer.shop_ref or "")
        for offer in offers
    )
    assert all((offer.shop_ref or "") in offer.url for offer in offers)


@pytest.mark.asyncio
async def test_stock_qty_uses_aligned_dom_max(
    load_fixture,
    adapter: JkEntertainmentAdapter,
):
    offers = await adapter.parse(
        load_fixture("jkentertainment_counterspell.html"),
        SearchQuery(name="Counterspell", in_stock_only=False),
    )

    assert any(offer.stock_qty > 1 for offer in offers)
