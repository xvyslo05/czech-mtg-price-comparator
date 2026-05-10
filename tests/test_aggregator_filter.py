from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.base import ShopAdapter
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import Condition, Offer, SearchQuery, ShopId


class _StubAdapter(ShopAdapter):
    def __init__(self, shop_id: ShopId, offers: list[Offer]):
        self.shop_id = shop_id
        self.base_url = f"https://example.com/{shop_id}"
        self._offers = offers

    async def search(self, query: SearchQuery) -> list[Offer]:
        return list(self._offers)


def _o(shop: ShopId, name: str, *, price: int = 50, edition: str | None = None) -> Offer:
    return Offer(
        shop=shop,
        card_name=name,
        edition=edition,
        condition=Condition.NM,
        foil=False,
        price_czk=price,
        stock_qty=1,
        url="https://example.com/x",
    )


@pytest.mark.asyncio
async def test_aggregator_excludes_non_playable_by_default():
    agg = Aggregator(
        [
            _StubAdapter(
                "najada",
                [
                    _o("najada", "Lightning Bolt", price=50, edition="M10"),
                    _o("najada", "Art Series: Lightning Bolt (V.2 - signed)", price=200),
                    _o("najada", "Lightning Bolt (Borderless)", price=70, edition="2X2"),
                ],
            ),
        ]
    )
    offers = await agg.search(SearchQuery(name="Lightning Bolt"))
    names = {o.card_name for o in offers}
    assert "Art Series: Lightning Bolt (V.2 - signed)" not in names
    assert names == {"Lightning Bolt", "Lightning Bolt (Borderless)"}


@pytest.mark.asyncio
async def test_aggregator_keeps_non_playable_when_opted_in():
    agg = Aggregator(
        [
            _StubAdapter(
                "najada",
                [
                    _o("najada", "Lightning Bolt", price=50),
                    _o("najada", "Art Series: Lightning Bolt", price=200),
                ],
            ),
        ]
    )
    offers = await agg.search(SearchQuery(name="Lightning Bolt", include_non_playable=True))
    names = {o.card_name for o in offers}
    assert "Art Series: Lightning Bolt" in names
