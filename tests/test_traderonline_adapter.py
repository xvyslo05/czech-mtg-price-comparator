from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest

from cz_mtg_compare.adapters.traderonline import (
    DEFAULT_EUR_TO_CZK,
    TraderOnlineAdapter,
)
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> TraderOnlineAdapter:
    return TraderOnlineAdapter(eur_to_czk=24.5)


@pytest.mark.asyncio
async def test_parses_sol_ring_fixture(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("traderonline_sol_ring.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )

    assert len(offers) == 27
    for offer in offers:
        assert offer.shop == "traderonline"
        assert "sol ring" in offer.card_name.casefold()
        assert offer.price_czk > 0
        assert offer.price_native is not None
        assert offer.currency == "EUR"
        assert offer.condition in Condition
        assert offer.url.startswith("https://trader-online.de")


@pytest.mark.asyncio
async def test_buylist_tiles_are_excluded(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("traderonline_sol_ring.html"),
        SearchQuery(
            name="Sol Ring",
            in_stock_only=False,
            include_non_playable=True,
        ),
    )

    assert len(offers) == 27
    assert all("/card-purchase" not in offer.url for offer in offers)


@pytest.mark.asyncio
async def test_accessories_are_excluded(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("traderonline_sol_ring.html"),
        SearchQuery(
            name="Gamegenic Prime Playmat",
            in_stock_only=False,
            include_non_playable=True,
        ),
    )

    assert offers == []


@pytest.mark.asyncio
async def test_root_level_sell_tiles_are_included(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("traderonline_lightning_bolt.html"),
        SearchQuery(
            name="Lightning Bolt",
            in_stock_only=False,
            include_non_playable=True,
        ),
    )

    assert len(offers) == 7
    paths = {urlsplit(offer.url).path for offer in offers}
    assert {
        "/en/lightning-bolt-1.html",
        "/en/lightning-bolt.html",
        "/en/lightning-bolt-1-2.html",
        "/en/lightning-bolt-1-3.html",
        "/en/lightning-bolt-blitzschlag.html",
    } <= paths
    assert all("/card-purchase/" not in offer.url for offer in offers)


@pytest.mark.asyncio
async def test_shop_ref_is_oxid_article_id(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("traderonline_sol_ring.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )

    assert all(
        re.fullmatch(r"[0-9a-f]{32}", offer.shop_ref or "")
        for offer in offers
    )


@pytest.mark.asyncio
async def test_set_code_and_printing_language(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("traderonline_sol_ring.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )

    strixhaven = next(offer for offer in offers if offer.set_code == "SOC")
    assert strixhaven.language == "EN"
    assert strixhaven.edition == "Secrets of Strixhaven: Commander"


@pytest.mark.asyncio
async def test_split_eur_price_ignores_other_tile_text(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("traderonline_sol_ring.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )

    strixhaven = next(offer for offer in offers if offer.set_code == "SOC")
    assert strixhaven.price_czk == round(2.99 * 24.5)


@pytest.mark.asyncio
async def test_in_stock_filter(load_fixture, adapter):
    html = load_fixture("traderonline_sol_ring.html")
    all_offers = await adapter.parse(
        html,
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )
    in_stock = await adapter.parse(
        html,
        SearchQuery(name="Sol Ring", in_stock_only=True),
    )

    assert len(in_stock) <= len(all_offers)
    assert all(offer.stock_qty > 0 for offer in in_stock)


@pytest.mark.asyncio
async def test_edition_filter(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("traderonline_sol_ring.html"),
        SearchQuery(
            name="Sol Ring",
            edition="Strixhaven",
            in_stock_only=False,
        ),
    )

    assert offers
    assert all(
        "strixhaven" in (offer.edition or "").casefold()
        for offer in offers
    )


def test_invalid_shared_eur_env_falls_back(monkeypatch):
    monkeypatch.setenv("CZ_MTG_EUR_TO_CZK", "invalid")
    adapter = TraderOnlineAdapter()
    assert adapter._eur_to_czk == DEFAULT_EUR_TO_CZK  # noqa: SLF001
