from __future__ import annotations

import httpx
import pytest
import respx

from cz_mtg_compare.adapters.magicmadhouse import (
    BROWSER_USER_AGENT,
    DEFAULT_GBP_TO_CZK,
    MagicMadhouseAdapter,
)
from cz_mtg_compare.models import Condition, SearchQuery


@pytest.fixture
def adapter() -> MagicMadhouseAdapter:
    return MagicMadhouseAdapter(gbp_to_czk=30.0)


@pytest.mark.asyncio
async def test_parses_ragavan_bodl(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("magicmadhouse_search_ragavan.html"),
        SearchQuery(
            name="Ragavan, Nimble Pilferer",
            in_stock_only=False,
        ),
    )

    assert len(offers) == 2
    for offer in offers:
        assert offer.shop == "magicmadhouse"
        assert offer.card_name == "Ragavan, Nimble Pilferer"
        assert offer.price_czk > 0
        assert offer.price_native is not None
        assert offer.currency == "GBP"
        assert offer.condition in Condition
        assert offer.url.startswith("https://magicmadhouse.co.uk/")


@pytest.mark.asyncio
async def test_brand_gate_excludes_non_mtg_products(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("magicmadhouse_search_ragavan.html"),
        SearchQuery(name="Nimble", in_stock_only=False),
    )

    assert {offer.shop_ref for offer in offers} == {
        "558094",
        "557625",
        "551315",
        "551251",
    }


@pytest.mark.asyncio
async def test_foil_detection(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("magicmadhouse_search_ragavan.html"),
        SearchQuery(name="Nimble", in_stock_only=False),
    )
    by_ref = {offer.shop_ref: offer for offer in offers}

    assert by_ref["558094"].foil is True
    assert by_ref["557625"].foil is False
    assert by_ref["551315"].foil is True
    assert by_ref["551251"].foil is False


@pytest.mark.asyncio
async def test_edition_and_set_code(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("magicmadhouse_search_ragavan.html"),
        SearchQuery(
            name="Ragavan, Nimble Pilferer",
            in_stock_only=False,
        ),
    )

    assert {offer.edition for offer in offers} == {
        "FINAL FANTASY: Through the Ages"
    }
    assert {offer.set_code for offer in offers} == {"FCA"}


@pytest.mark.asyncio
async def test_listing_price_gbp_converted(load_fixture, adapter):
    offers = await adapter.parse(
        load_fixture("magicmadhouse_search_ragavan.html"),
        SearchQuery(name="Sun-Spider", in_stock_only=False),
    )

    assert len(offers) == 2
    assert all(offer.price_czk == round(0.4 * 30.0) for offer in offers)
    assert all(offer.condition is Condition.UNKNOWN for offer in offers)


def test_product_attributes_exact_price_stock_and_condition(
    load_fixture,
    adapter,
):
    products = adapter._decode_bodl(  # noqa: SLF001
        load_fixture("magicmadhouse_search_ragavan.html")
    )
    product = next(item for item in products if item.get("id") == 557625)
    fallback = adapter._product_to_offer(product)  # noqa: SLF001
    assert fallback is not None
    options = adapter._parse_condition_options(  # noqa: SLF001
        load_fixture("magicmadhouse_product_sun_spider.html")
    )

    assert options == [("264334", "442966", "nm", "Mint / Near Mint")]
    enriched = adapter._offer_from_attributes(  # noqa: SLF001
        load_fixture("magicmadhouse_attributes_sun_spider.json"),
        fallback,
        sid=options[0][2],
        title=options[0][3],
    )
    assert enriched is not None
    assert enriched.price_czk == round(0.4 * 30.0)
    assert enriched.stock_qty == 64
    assert enriched.condition is Condition.NM


@pytest.mark.asyncio
async def test_in_stock_filter(load_fixture, adapter):
    html = load_fixture("magicmadhouse_search_ragavan.html")
    all_offers = await adapter.parse(
        html,
        SearchQuery(name="Nimble", in_stock_only=False),
    )
    in_stock = await adapter.parse(
        html,
        SearchQuery(name="Nimble", in_stock_only=True),
    )

    assert len(all_offers) == 4
    assert len(in_stock) == 2
    assert all(offer.stock_qty > 0 for offer in in_stock)


@pytest.mark.asyncio
async def test_enriched_search_uses_browser_headers_and_falls_back(
    load_fixture,
):
    adapter = MagicMadhouseAdapter(
        gbp_to_czk=30.0,
        enrich_variants=True,
    )
    query = SearchQuery(name="Sun-Spider", in_stock_only=False)
    search_html = load_fixture("magicmadhouse_search_ragavan.html")
    product_html = load_fixture("magicmadhouse_product_sun_spider.html")
    attributes_json = load_fixture(
        "magicmadhouse_attributes_sun_spider.json"
    )
    nonfoil_url = (
        "https://magicmadhouse.co.uk/"
        "magic-the-gathering-marvels-spider-man-sun-spider-nimble-webber"
    )
    foil_url = f"{nonfoil_url}-foil"

    def assert_browser_get(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == BROWSER_USER_AGENT
        return httpx.Response(200, text=search_html)

    def assert_variant_post(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-requested-with"] == "XMLHttpRequest"
        assert request.headers["user-agent"] == BROWSER_USER_AGENT
        return httpx.Response(200, text=attributes_json)

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(adapter._search_url(query)).mock(  # noqa: SLF001
            side_effect=assert_browser_get
        )
        mock.get(nonfoil_url).mock(
            return_value=httpx.Response(200, text=product_html)
        )
        mock.get(foil_url).mock(return_value=httpx.Response(503))
        mock.post(
            "https://magicmadhouse.co.uk/"
            "remote/v1/product-attributes/557625"
        ).mock(side_effect=assert_variant_post)
        offers = await adapter.search(query)

    by_ref = {offer.shop_ref: offer for offer in offers}
    assert by_ref["557625"].condition is Condition.NM
    assert by_ref["557625"].stock_qty == 64
    assert by_ref["558094"].condition is Condition.UNKNOWN
    assert by_ref["558094"].stock_qty == 1


@pytest.mark.asyncio
async def test_partial_variant_failure_keeps_bodl_fallback(
    load_fixture,
    monkeypatch,
    adapter,
):
    products = adapter._decode_bodl(  # noqa: SLF001
        load_fixture("magicmadhouse_search_ragavan.html")
    )
    product = next(item for item in products if item.get("id") == 557625)
    fallback = adapter._product_to_offer(product)  # noqa: SLF001
    assert fallback is not None
    monkeypatch.setattr(
        adapter,
        "_parse_condition_options",
        lambda _: [
            ("264334", "442966", "nm", "Mint / Near Mint"),
            ("264334", "442967", "lp", "Lightly Played"),
        ],
    )
    attributes_json = load_fixture(
        "magicmadhouse_attributes_sun_spider.json"
    )
    post_count = 0

    def resolve_one_option(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        post_count += 1
        if post_count == 1:
            return httpx.Response(200, text=attributes_json)
        raise httpx.ConnectError("variant unavailable", request=request)

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(fallback.url).mock(return_value=httpx.Response(200))
        mock.post(
            "https://magicmadhouse.co.uk/"
            "remote/v1/product-attributes/557625"
        ).mock(side_effect=resolve_one_option)
        offers = await adapter._enrich_product(fallback)  # noqa: SLF001

    assert [offer.condition for offer in offers] == [
        Condition.NM,
        Condition.UNKNOWN,
    ]
    assert offers[1] is fallback


def test_invalid_shared_gbp_env_falls_back(monkeypatch):
    monkeypatch.setenv("CZ_MTG_GBP_TO_CZK", "invalid")
    adapter = MagicMadhouseAdapter()
    assert adapter._gbp_to_czk == DEFAULT_GBP_TO_CZK  # noqa: SLF001
