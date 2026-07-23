from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.mtgspot import (
    DEFAULT_PLN_TO_CZK,
    MtgspotAdapter,
)
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> MtgspotAdapter:
    return MtgspotAdapter(pln_to_czk=6.0)


@pytest.mark.asyncio
async def test_parses_lightning_bolt_json(
    load_fixture,
    adapter: MtgspotAdapter,
):
    payload = load_fixture("mtgspot_lightning_bolt.json")
    offers = await adapter.parse(
        payload,
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert len(offers) == 16
    for offer in offers:
        assert offer.shop == "mtgspot"
        assert "lightning bolt" in offer.card_name.lower()
        assert offer.price_czk > 0
        assert offer.condition in Condition
        assert offer.url.startswith("https://mtgspot.pl/single")


@pytest.mark.asyncio
async def test_in_stock_filter(load_fixture, adapter: MtgspotAdapter):
    payload = load_fixture("mtgspot_lightning_bolt.json")
    all_offers = await adapter.parse(
        payload,
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    in_stock = await adapter.parse(
        payload,
        SearchQuery(name="Lightning Bolt", in_stock_only=True),
    )

    assert len(all_offers) == 16
    assert len(in_stock) == 14
    assert all(offer.stock_qty > 0 for offer in in_stock)


@pytest.mark.asyncio
async def test_foil_carried_through(load_fixture, adapter: MtgspotAdapter):
    offers = await adapter.parse(
        load_fixture("mtgspot_lightning_bolt.json"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    assert any(offer.foil for offer in offers)
    assert any(not offer.foil for offer in offers)


@pytest.mark.asyncio
async def test_edition_and_language(load_fixture, adapter: MtgspotAdapter):
    offers = await adapter.parse(
        load_fixture("mtgspot_lightning_bolt.json"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert any(offer.edition for offer in offers)
    assert any(offer.language == "EN" for offer in offers)
    assert any(offer.language == "JP" for offer in offers)


@pytest.mark.asyncio
async def test_non_singles_and_art_series_excluded(
    load_fixture,
    adapter: MtgspotAdapter,
):
    payload = load_fixture("mtgspot_lightning_bolt.json")
    offers = await adapter.parse(
        payload,
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    with_non_playable = await adapter.parse(
        payload,
        SearchQuery(
            name="Lightning Bolt",
            in_stock_only=False,
            include_non_playable=True,
        ),
    )

    assert all("art series" not in offer.card_name.lower() for offer in offers)
    assert len(with_non_playable) == 18
    assert sum(
        "art series" in offer.card_name.lower()
        for offer in with_non_playable
    ) == 2


@pytest.mark.asyncio
async def test_price_conversion_and_condition(
    load_fixture,
    adapter: MtgspotAdapter,
):
    offers = await adapter.parse(
        load_fixture("mtgspot_lightning_bolt.json"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    by_ref = {offer.shop_ref: offer for offer in offers}

    assert by_ref["e134c9347d53199"].price_czk == round(8.41 * 6.0)
    assert by_ref["373295ba7300af5"].condition is Condition.GD
    assert by_ref["5f42e7445adbbbb"].condition is Condition.LP
    assert by_ref["95fe67a31997f9c"].condition is Condition.PL


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["", "{}", "[]", "not-json"])
async def test_empty_payload(
    adapter: MtgspotAdapter,
    body: str,
):
    assert await adapter.parse(body, SearchQuery(name="Lightning Bolt")) == []


def test_invalid_pln_env_falls_back(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CZ_MTG_PLN_TO_CZK", "invalid")
    adapter = MtgspotAdapter()
    assert adapter._pln_to_czk == DEFAULT_PLN_TO_CZK  # noqa: SLF001
