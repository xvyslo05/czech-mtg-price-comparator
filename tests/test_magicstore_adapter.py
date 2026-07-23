from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.magicstore import (
    DEFAULT_EUR_TO_CZK,
    MagicStoreAdapter,
)
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> MagicStoreAdapter:
    return MagicStoreAdapter(eur_to_czk=25.0)


@pytest.mark.asyncio
async def test_parses_italian_lightning_bolt_results(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("magicstore_lightning_bolt.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert len(offers) == 5
    assert any(offer.card_name == "FULMINE" for offer in offers)
    for offer in offers:
        assert offer.shop == "magicstore"
        assert offer.url.startswith("https://www.magicstore.it/")
        assert offer.condition is Condition.NM
        assert offer.price_czk > 0


@pytest.mark.asyncio
async def test_foil_detection_from_sol_ring_fixture(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("magicstore_sol_ring.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )

    foil = next(offer for offer in offers if offer.foil)
    assert "foil" in foil.card_name.casefold()


@pytest.mark.asyncio
async def test_in_stock_filter_and_unavailable_prices(load_fixture, adapter):
    html = load_fixture("magicstore_lightning_bolt.html")
    all_offers = await adapter.parse(
        html,
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    in_stock = await adapter.parse(
        html,
        SearchQuery(name="Lightning Bolt", in_stock_only=True),
    )

    assert len(in_stock) <= len(all_offers)
    assert all(offer.stock_qty > 0 for offer in in_stock)
    assert all("fulmine-37265" not in offer.url for offer in all_offers)


@pytest.mark.asyncio
async def test_eur_price_converted(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("magicstore_lightning_bolt.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    avatar = next(
        offer
        for offer in offers
        if offer.edition
        == "Magic: The Gathering | Avatar: The Last Airbender: Eternal"
    )
    assert avatar.price_czk == round(4.0 * 25.0)


@pytest.mark.asyncio
async def test_shop_ref_is_numeric_cart_id(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("magicstore_lightning_bolt.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert all((offer.shop_ref or "").isdigit() for offer in offers)
    assert all("/ritiro/" not in offer.url for offer in offers)


@pytest.mark.asyncio
async def test_valutazione_buyback_is_not_a_sale_offer(
    load_fixture,
    adapter,
):
    offers = await adapter.parse(
        load_fixture("magicstore_lightning_bolt.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert all("fulmine-foil-35932" not in offer.url for offer in offers)
    assert all(offer.price_czk >= 100 for offer in offers)


@pytest.mark.asyncio
async def test_edition_filter_uses_italian_set_label(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("magicstore_lightning_bolt.html"),
        SearchQuery(
            name="Lightning Bolt",
            edition="Avatar",
            in_stock_only=False,
        ),
    )

    assert len(offers) == 1
    assert offers[0].card_name == "FULMINE"


def test_search_url_requires_mtg_singles_category(adapter):
    url = adapter._search_url(SearchQuery(name="Lightning Bolt"))  # noqa: SLF001
    assert url == (
        "https://www.magicstore.it/ricerca.php?"
        "q=Lightning+Bolt&id_cat=9"
    )


def test_invalid_shared_eur_env_falls_back(monkeypatch):
    monkeypatch.setenv("CZ_MTG_EUR_TO_CZK", "invalid")
    adapter = MagicStoreAdapter()
    assert adapter._eur_to_czk == DEFAULT_EUR_TO_CZK  # noqa: SLF001
