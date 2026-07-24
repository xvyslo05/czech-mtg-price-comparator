from __future__ import annotations

import httpx
import pytest
import respx

from cz_mtg_compare.adapters.bazaargames import BazaarGamesAdapter
from cz_mtg_compare.models import Condition, SearchQuery


def _adapter(
    shop_id: str = "bazaarofmagic",
    base_url: str = "https://www.bazaarofmagic.eu",
    enrich_detail: bool = False,
) -> BazaarGamesAdapter:
    return BazaarGamesAdapter(
        shop_id=shop_id,  # type: ignore[arg-type]
        base_url=base_url,
        eur_to_czk=25.0,
        enrich_detail=enrich_detail,
    )


def test_bazaar_search_url_uses_singles_tab():
    adapter = _adapter()
    url = adapter._search_url(SearchQuery(name="Lightning Bolt"))  # noqa: SLF001

    assert url == (
        "https://www.bazaarofmagic.eu/en-WW/query?"
        "name=Lightning+Bolt&tab=singles"
    )


@pytest.mark.asyncio
async def test_bazaar_parses_lightning_bolt_tiles(load_fixture):
    adapter = _adapter()
    offers = await adapter.parse(
        load_fixture("bazaarofmagic_lightning_bolt_singles.html"),
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )

    assert len(offers) == 24
    for offer in offers:
        assert offer.shop == "bazaarofmagic"
        assert offer.price_czk > 0
        assert offer.price_native is not None
        assert offer.currency == "EUR"
        assert offer.condition is Condition.NM
        assert offer.url.startswith("https://www.bazaarofmagic.eu")
        assert (offer.shop_ref or "").isdigit()
    assert any(offer.edition == "Magic 2011" for offer in offers)
    m11 = next(offer for offer in offers if offer.shop_ref == "4065135")
    assert m11.price_czk == round(2.40 * 25.0)
    assert m11.stock_qty == 0


@pytest.mark.asyncio
async def test_bazaar_in_stock_filter(load_fixture):
    adapter = _adapter()
    html = load_fixture("bazaarofmagic_lightning_bolt_singles.html")
    all_offers = await adapter.parse(
        html,
        SearchQuery(name="Lightning Bolt", in_stock_only=False),
    )
    in_stock = await adapter.parse(
        html,
        SearchQuery(name="Lightning Bolt", in_stock_only=True),
    )

    assert len(all_offers) == 24
    assert len(in_stock) == 4
    assert all(offer.stock_qty > 0 for offer in in_stock)


@pytest.mark.asyncio
async def test_bazaar_foil_variant(load_fixture):
    adapter = _adapter()
    offers = await adapter.parse(
        load_fixture("bazaarofmagic_sol_ring_singles.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )

    assert len(offers) == 24
    foil = next(offer for offer in offers if offer.foil)
    assert foil.card_name == "Sol Ring"
    assert foil.edition == "Kaladesh Inventions"
    assert foil.price_czk == round(1540.00 * 25.0)


@pytest.mark.asyncio
async def test_bazaar_edition_filter(load_fixture):
    adapter = _adapter()
    offers = await adapter.parse(
        load_fixture("bazaarofmagic_lightning_bolt_singles.html"),
        SearchQuery(
            name="Lightning Bolt",
            edition="Magic 2011",
            in_stock_only=False,
        ),
    )
    assert len(offers) == 1
    assert offers[0].shop_ref == "4065135"


def test_bazaar_detail_json_ld(load_fixture):
    adapter = _adapter()
    offer = adapter._parse_detail(  # noqa: SLF001
        load_fixture(
            "bazaarofmagic_detail_lightning-bolt-magic-2011.html"
        )
    )

    assert offer is not None
    assert offer.price_czk == 60
    assert offer.stock_qty == 0
    assert offer.condition is Condition.NM
    assert offer.shop_ref == "4065135"
    assert offer.edition == "Magic 2011"


def test_bazaar_detail_missing_availability_keeps_listing_stock():
    adapter = _adapter()
    offer = adapter._parse_detail(  # noqa: SLF001
        """
        <script type="application/ld+json">
        {
          "@type": "Product",
          "name": "Lightning Bolt - Magic 2011",
          "category": "Magic 2011",
          "sku": "4065135",
          "offers": {
            "price": "2.40",
            "url": "https://www.bazaarofmagic.eu/en-WW/p/lightning-bolt/4065135"
          }
        }
        </script>
        """,
        listing_stock_qty=0,
    )

    assert offer is not None
    assert offer.stock_qty == 0


@pytest.mark.asyncio
async def test_bazaar_reapplies_stock_filter_after_enrichment(
    load_fixture,
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _adapter(enrich_detail=True)
    query = SearchQuery(name="Lightning Bolt", in_stock_only=True)

    async def mark_out_of_stock(offers, eur_to_czk):
        assert eur_to_czk == 25.0
        for offer in offers:
            offer.stock_qty = 0

    monkeypatch.setattr(adapter, "_enrich_offers", mark_out_of_stock)
    async with respx.mock:
        respx.get(adapter._search_url(query)).mock(  # noqa: SLF001
            return_value=httpx.Response(
                200,
                text=load_fixture(
                    "bazaarofmagic_lightning_bolt_singles.html"
                ),
            )
        )
        offers = await adapter.search(query)

    assert offers == []


@pytest.mark.asyncio
async def test_spellenwinkel_has_no_singles_in_fixture(load_fixture):
    adapter = _adapter(
        shop_id="spellenwinkel",
        base_url="https://www.spellenwinkel.nl",
    )
    offers = await adapter.parse(
        load_fixture("spellenwinkel_sol_ring_singles.html"),
        SearchQuery(name="Sol Ring", in_stock_only=False),
    )

    assert offers == []
    assert adapter.shop_id == "spellenwinkel"
    assert adapter.base_url == "https://www.spellenwinkel.nl"
