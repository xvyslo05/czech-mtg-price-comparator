from __future__ import annotations

import re

import pytest

from cz_mtg_compare.adapters.magiccorporation import (
    DEFAULT_EUR_TO_CZK,
    MagicCorporationAdapter,
)
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> MagicCorporationAdapter:
    return MagicCorporationAdapter(eur_to_czk=25.0)


@pytest.mark.asyncio
async def test_parses_sol_ring_fixture(
    load_fixture,
    adapter: MagicCorporationAdapter,
):
    offers = await adapter.parse(
        load_fixture("magiccorporation_sol_ring.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )

    assert len(offers) == 3
    for offer in offers:
        assert offer.shop == "magiccorporation"
        assert offer.card_name == "Sol Ring"
        assert offer.url.startswith(
            "https://boutique.magiccorporation.com/carte/"
        )
        assert offer.price_czk > 0
        assert offer.price_native is not None
        assert offer.currency == "EUR"


@pytest.mark.asyncio
async def test_language_mapping(load_fixture, adapter: MagicCorporationAdapter):
    offers = await adapter.parse(
        load_fixture("magiccorporation_sol_ring.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )
    assert {offer.language for offer in offers} == {"EN", "FR"}


@pytest.mark.asyncio
async def test_foil_detection_and_price(
    load_fixture,
    adapter: MagicCorporationAdapter,
):
    offers = await adapter.parse(
        load_fixture("magiccorporation_sol_ring.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )
    foil = next(offer for offer in offers if offer.foil)
    nonfoil = [offer for offer in offers if not offer.foil]

    assert foil.shop_ref == "18189:vo_foil"
    assert foil.price_czk == round(29.90 * 25.0)
    assert all(foil.price_czk > offer.price_czk for offer in nonfoil)


@pytest.mark.asyncio
async def test_all_variant_types_from_richer_fixture(
    load_fixture,
    adapter: MagicCorporationAdapter,
):
    offers = await adapter.parse(
        load_fixture("magiccorporation_lightning_bolt.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert len(offers) == 17
    refs = {offer.shop_ref or "" for offer in offers}
    assert any(ref.endswith(":vo") for ref in refs)
    assert any(ref.endswith(":vf") for ref in refs)
    assert any(ref.endswith(":vo_foil") for ref in refs)
    assert any(ref.endswith(":vf_foil") for ref in refs)
    assert any(offer.language == "FR" and offer.foil for offer in offers)


@pytest.mark.asyncio
async def test_stock_condition_shop_ref_and_edition(
    load_fixture,
    adapter: MagicCorporationAdapter,
):
    offers = await adapter.parse(
        load_fixture("magiccorporation_sol_ring.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )

    assert any(offer.stock_qty == 5 for offer in offers)
    assert all(offer.condition is Condition.NM for offer in offers)
    assert all(
        re.fullmatch(r"\d+:(?:vo|vf|vo_foil|vf_foil)", offer.shop_ref or "")
        for offer in offers
    )
    assert {offer.edition for offer in offers} == {
        "Revised",
        "From The Vault: Relics",
    }


@pytest.mark.asyncio
async def test_in_stock_and_edition_filters(
    load_fixture,
    adapter: MagicCorporationAdapter,
):
    html = load_fixture("magiccorporation_sol_ring.html")
    in_stock = await adapter.parse(
        html,
        SearchQuery(name="Sol Ring", in_stock_only=True),
    )
    revised = await adapter.parse(
        html,
        SearchQuery(
            name="Sol Ring",
            edition="Revised",
            in_stock_only=False,
        ),
    )

    assert len(in_stock) == 3
    assert all(offer.stock_qty > 0 for offer in in_stock)
    assert len(revised) == 2
    assert all(offer.edition == "Revised" for offer in revised)


@pytest.mark.asyncio
async def test_edition_filter_matches_set_code(
    load_fixture,
    adapter: MagicCorporationAdapter,
):
    offers = await adapter.parse(
        load_fixture("magiccorporation_sol_ring.html"),
        SearchQuery(
            name="Sol Ring",
            edition="3ED",
            in_stock_only=False,
        ),
    )

    assert len(offers) == 2
    assert all(offer.edition == "Revised" for offer in offers)
    assert all(offer.set_code == "3ED" for offer in offers)


def test_invalid_eur_env_falls_back(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CZ_MTG_EUR_TO_CZK", "invalid")
    adapter = MagicCorporationAdapter()
    assert adapter._eur_to_czk == DEFAULT_EUR_TO_CZK  # noqa: SLF001
