from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.base import ShopAdapter
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import Condition, Offer, SearchQuery, ShopId
from cz_mtg_compare.optimizer import DecklistOptimizer


class _StubAdapter(ShopAdapter):
    """Adapter that returns a precomputed `card_name -> [offers]` map."""

    def __init__(self, shop_id: ShopId, table: dict[str, list[Offer]]):
        self.shop_id = shop_id
        self.base_url = f"https://example.com/{shop_id}"
        self._table = {k.lower(): v for k, v in table.items()}

    async def search(self, query: SearchQuery) -> list[Offer]:
        offers = self._table.get(query.name.lower(), [])
        if query.in_stock_only:
            offers = [o for o in offers if o.stock_qty > 0]
        return list(offers)


def _o(shop: ShopId, name: str, price: int, *, qty: int = 1, cond: Condition = Condition.NM, foil: bool = False) -> Offer:
    return Offer(
        shop=shop,
        card_name=name,
        edition="X",
        condition=cond,
        foil=foil,
        price_czk=price,
        stock_qty=qty,
        url=f"https://example.com/{shop}",
    )


@pytest.mark.asyncio
async def test_cheapest_split_picks_lowest_per_card_across_shops():
    agg = Aggregator(
        [
            _StubAdapter(
                "tolarie",
                {"Lightning Bolt": [_o("tolarie", "Lightning Bolt", 50)],
                 "Counterspell": [_o("tolarie", "Counterspell", 30)]},
            ),
            _StubAdapter(
                "najada",
                {"Lightning Bolt": [_o("najada", "Lightning Bolt", 35)],
                 "Counterspell": [_o("najada", "Counterspell", 80)]},
            ),
        ]
    )
    optimizer = DecklistOptimizer(agg)
    result = await optimizer.optimize("4 Lightning Bolt\n2 Counterspell\n")

    assert result.total_cards == 6
    assert result.unique_cards == 2

    by_name = {p.name: p for p in result.picks}
    assert by_name["Lightning Bolt"].chosen.shop == "najada"  # 35 < 50
    assert by_name["Lightning Bolt"].chosen_total_czk == 35 * 4
    assert by_name["Counterspell"].chosen.shop == "tolarie"  # 30 < 80
    assert by_name["Counterspell"].chosen_total_czk == 30 * 2

    assert result.cheapest_split_total_czk == 35 * 4 + 30 * 2
    assert result.cheapest_split_missing == []


@pytest.mark.asyncio
async def test_per_shop_bundles_count_coverage_and_totals():
    # tolarie has both cards, najada only has one.
    agg = Aggregator(
        [
            _StubAdapter(
                "tolarie",
                {"Lightning Bolt": [_o("tolarie", "Lightning Bolt", 50)],
                 "Counterspell": [_o("tolarie", "Counterspell", 30)]},
            ),
            _StubAdapter(
                "najada",
                {"Lightning Bolt": [_o("najada", "Lightning Bolt", 35)]},
            ),
        ]
    )
    optimizer = DecklistOptimizer(agg)
    result = await optimizer.optimize("4 Lightning Bolt\n2 Counterspell\n")

    bundles = {b.shop: b for b in result.per_shop_bundles}
    assert bundles["tolarie"].covered_cards == 2
    assert bundles["tolarie"].missing_cards == []
    assert bundles["tolarie"].total_czk == 50 * 4 + 30 * 2

    assert bundles["najada"].covered_cards == 1
    assert bundles["najada"].missing_cards == ["Counterspell"]
    assert bundles["najada"].total_czk == 35 * 4


@pytest.mark.asyncio
async def test_missing_cards_are_surfaced_globally():
    agg = Aggregator(
        [
            _StubAdapter("tolarie", {"Lightning Bolt": [_o("tolarie", "Lightning Bolt", 50)]}),
        ]
    )
    optimizer = DecklistOptimizer(agg)
    result = await optimizer.optimize("4 Lightning Bolt\n1 Black Lotus\n")

    by_name = {p.name: p for p in result.picks}
    assert by_name["Black Lotus"].missing is True
    assert by_name["Black Lotus"].chosen is None
    assert "Black Lotus" in result.cheapest_split_missing


@pytest.mark.asyncio
async def test_quantities_sum_when_card_appears_twice():
    agg = Aggregator(
        [_StubAdapter("tolarie", {"Lightning Bolt": [_o("tolarie", "Lightning Bolt", 50)]})]
    )
    optimizer = DecklistOptimizer(agg)
    result = await optimizer.optimize("3 Lightning Bolt\n1 Lightning Bolt\n")

    assert result.unique_cards == 1
    assert result.picks[0].quantity == 4
    assert result.picks[0].chosen_total_czk == 50 * 4


@pytest.mark.asyncio
async def test_shopping_plan_groups_picks_by_shop():
    agg = Aggregator(
        [
            _StubAdapter(
                "tolarie",
                {
                    "Lightning Bolt": [_o("tolarie", "Lightning Bolt", 50)],
                    "Sol Ring": [_o("tolarie", "Sol Ring", 25)],
                },
            ),
            _StubAdapter(
                "najada",
                {
                    "Lightning Bolt": [_o("najada", "Lightning Bolt", 35)],
                    "Counterspell": [_o("najada", "Counterspell", 30)],
                },
            ),
        ]
    )
    optimizer = DecklistOptimizer(agg)
    result = await optimizer.optimize(
        "4 Lightning Bolt\n2 Counterspell\n2 Sol Ring\n"
    )

    by_shop = {g.shop: g for g in result.shopping_plan}
    # Lightning Bolt and Counterspell are cheaper at najada;
    # Sol Ring only available at tolarie.
    assert set(by_shop.keys()) == {"najada", "tolarie"}

    najada = by_shop["najada"]
    najada_names = {l.name for l in najada.lines}
    assert najada_names == {"Lightning Bolt", "Counterspell"}
    assert najada.cards_count == 4 + 2
    assert najada.items_count == 2
    assert najada.subtotal_czk == 35 * 4 + 30 * 2

    tolarie = by_shop["tolarie"]
    assert {l.name for l in tolarie.lines} == {"Sol Ring"}
    assert tolarie.subtotal_czk == 25 * 2

    # Plan is sorted by descending subtotal -> najada first (200 > 50).
    assert result.shopping_plan[0].shop == "najada"

    # Sum of plan subtotals must match the headline total.
    plan_sum = sum(g.subtotal_czk for g in result.shopping_plan)
    assert plan_sum == result.cheapest_split_total_czk


@pytest.mark.asyncio
async def test_shopping_plan_skips_missing_cards():
    agg = Aggregator(
        [_StubAdapter("tolarie", {"Lightning Bolt": [_o("tolarie", "Lightning Bolt", 50)]})]
    )
    optimizer = DecklistOptimizer(agg)
    result = await optimizer.optimize("4 Lightning Bolt\n1 Black Lotus\n")

    assert len(result.shopping_plan) == 1
    only = result.shopping_plan[0]
    assert {l.name for l in only.lines} == {"Lightning Bolt"}


@pytest.mark.asyncio
async def test_picks_lowest_among_ties_by_condition():
    # Two shops at identical prices, NM beats LP.
    agg = Aggregator(
        [
            _StubAdapter(
                "tolarie", {"Lightning Bolt": [_o("tolarie", "Lightning Bolt", 50, cond=Condition.LP)]}
            ),
            _StubAdapter(
                "najada", {"Lightning Bolt": [_o("najada", "Lightning Bolt", 50, cond=Condition.NM)]}
            ),
        ]
    )
    optimizer = DecklistOptimizer(agg)
    result = await optimizer.optimize("1 Lightning Bolt\n")
    assert result.picks[0].chosen.shop == "najada"
