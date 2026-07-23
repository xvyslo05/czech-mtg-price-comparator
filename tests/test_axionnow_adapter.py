from __future__ import annotations

import json

import httpx
import pytest
import respx

from cz_mtg_compare.adapters.axionnow import (
    DEFAULT_GBP_TO_CZK,
    AxionNowAdapter,
)
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> AxionNowAdapter:
    return AxionNowAdapter(gbp_to_czk=30.0)


@pytest.mark.asyncio
async def test_parses_lightning_bolt_variants(
    load_fixture,
    adapter: AxionNowAdapter,
):
    payload = load_fixture("axionnow_lightning_bolt_product.js.json")
    offers = await adapter.parse(
        payload,
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert len(offers) == 3
    for offer in offers:
        assert offer.shop == "axionnow"
        assert "lightning bolt" in offer.card_name.lower()
        assert offer.price_czk > 0
        assert offer.condition in Condition
        assert offer.url.startswith("https://axionnow.com/products/")


@pytest.mark.asyncio
async def test_in_stock_filter(load_fixture, adapter: AxionNowAdapter):
    payload = load_fixture("axionnow_lightning_bolt_product.js.json")
    all_offers = await adapter.parse(
        payload,
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    in_stock = await adapter.parse(
        payload,
        SearchQuery(name="Lightning Bolt", in_stock_only=True),
    )

    assert len(all_offers) == 3
    assert len(in_stock) == 1
    assert all(offer.stock_qty > 0 for offer in in_stock)


@pytest.mark.asyncio
async def test_foil_and_condition_parsed(
    load_fixture,
    adapter: AxionNowAdapter,
):
    payload = load_fixture("axionnow_lightning_bolt_product.js.json")
    offers = await adapter.parse(
        payload,
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert any(offer.foil and offer.condition is Condition.NM for offer in offers)
    assert any(
        not offer.foil and offer.condition is Condition.EX
        for offer in offers
    )


@pytest.mark.asyncio
async def test_set_code_from_title(load_fixture, adapter: AxionNowAdapter):
    payload = load_fixture("axionnow_lightning_bolt_product.js.json")
    offers = await adapter.parse(
        payload,
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert {offer.set_code for offer in offers} == {"CLB"}


@pytest.mark.asyncio
async def test_price_is_pence_gbp_converted(
    load_fixture,
    adapter: AxionNowAdapter,
):
    payload = load_fixture("axionnow_lightning_bolt_product.js.json")
    offers = await adapter.parse(
        payload,
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    by_ref = {offer.shop_ref: offer for offer in offers}

    assert by_ref["47592596996413"].price_czk == round(1.20 * 30.0)
    assert by_ref["47592597029181"].price_czk == round(2.26 * 30.0)


def test_suggest_maps_name_to_handles(
    load_fixture,
    adapter: AxionNowAdapter,
):
    payload = load_fixture("axionnow_lightning_bolt_suggest.json")
    products = adapter._parse_suggest(  # noqa: SLF001
        payload,
        SearchQuery(name="Lightning Bolt"),
    )

    assert len(products) == 9
    clb = next(
        product
        for product in products
        if product["handle"] == "mtg-singles-clb-lightningbolt-401"
    )
    assert clb["card_name"] == "Lightning Bolt"
    assert clb["set_code"] == "CLB"
    assert clb["edition"] == "Commander Legends: Battle for Baldur's Gate"
    assert all(not str(product["handle"]).startswith("lor-") for product in products)


@pytest.mark.asyncio
async def test_search_keeps_successful_products_after_one_fetch_fails(
    load_fixture,
    adapter: AxionNowAdapter,
):
    successful_handle = "mtg-singles-clb-lightningbolt-401"
    failed_handle = "broken-lightning-bolt"
    suggest_payload = json.dumps(
        {
            "resources": {
                "results": {
                    "products": [
                        {
                            "type": "singles",
                            "tags": ["Magic"],
                            "title": "Lightning Bolt (401) - CLB",
                            "handle": successful_handle,
                        },
                        {
                            "type": "singles",
                            "tags": ["Magic"],
                            "title": "Lightning Bolt (999) - TST",
                            "handle": failed_handle,
                        },
                    ]
                }
            }
        }
    )

    async with respx.mock:
        respx.get(
            "https://axionnow.com/search/suggest.json"
        ).mock(return_value=httpx.Response(200, text=suggest_payload))
        respx.get(
            f"https://axionnow.com/products/{successful_handle}.js"
        ).mock(
            return_value=httpx.Response(
                200,
                text=load_fixture(
                    "axionnow_lightning_bolt_product.js.json"
                ),
            )
        )
        respx.get(
            f"https://axionnow.com/products/{failed_handle}.js"
        ).mock(return_value=httpx.Response(503))
        offers = await adapter.search(
            SearchQuery(name="Lightning Bolt", in_stock_only=False)
        )

    assert len(offers) == 3
    assert all(offer.set_code == "CLB" for offer in offers)


def test_invalid_gbp_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("CZ_MTG_GBP_TO_CZK", "not-a-number")
    adapter = AxionNowAdapter()
    assert adapter._gbp_to_czk == DEFAULT_GBP_TO_CZK  # noqa: SLF001
