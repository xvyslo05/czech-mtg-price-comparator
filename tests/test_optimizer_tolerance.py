"""Env-var override behavior for the fewest_shops tolerance."""
from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.base import ShopAdapter
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import Condition, Offer, SearchQuery, ShopId
from cz_mtg_compare.optimizer import (
    CONSOLIDATE_TOLERANCE_ENV,
    DEFAULT_CONSOLIDATE_TOLERANCE_PCT,
    DecklistOptimizer,
)


class _StubAdapter(ShopAdapter):
    def __init__(self, shop_id: ShopId, table: dict[str, list[Offer]]):
        self.shop_id = shop_id
        self.base_url = f"https://example.com/{shop_id}"
        self._table = {k.lower(): v for k, v in table.items()}

    async def search(self, query: SearchQuery) -> list[Offer]:
        offers = self._table.get(query.name.lower(), [])
        if query.in_stock_only:
            offers = [o for o in offers if o.stock_qty > 0]
        return list(offers)


def _o(shop: ShopId, name: str, price: int) -> Offer:
    return Offer(
        shop=shop,
        card_name=name,
        edition="X",
        condition=Condition.NM,
        foil=False,
        price_czk=price,
        stock_qty=1,
        url=f"https://example.com/{shop}",
    )


def _agg() -> Aggregator:
    """Cheapest split = A(100) + B(80) = 180.
    cernyrytir alone = 170 + 80 = 250 → +38.9% over baseline.
    Fits at 50% tolerance (budget 270), rejected at 10% (budget 198)."""
    return Aggregator(
        [
            _StubAdapter("najada", {"A": [_o("najada", "A", 100)]}),
            _StubAdapter(
                "cernyrytir",
                {"A": [_o("cernyrytir", "A", 170)],
                 "B": [_o("cernyrytir", "B", 80)]},
            ),
        ]
    )


def test_default_tolerance_constant_is_ten_pct():
    """Guard: bumping the default silently changes user-facing behavior."""
    assert DEFAULT_CONSOLIDATE_TOLERANCE_PCT == 10


@pytest.mark.asyncio
async def test_tolerance_env_override_allows_consolidation(monkeypatch: pytest.MonkeyPatch):
    """At 50% tolerance a single-shop plan that's rejected at the 10% default
    becomes the chosen plan."""
    monkeypatch.setenv(CONSOLIDATE_TOLERANCE_ENV, "50")
    optimizer = DecklistOptimizer(_agg())
    result = await optimizer.optimize("1 A\n1 B\n", strategy="fewest_shops")

    assert result.consolidated_total_czk == 250
    assert len(result.shopping_plan) == 1
    assert result.shopping_plan[0].shop == "cernyrytir"


@pytest.mark.asyncio
async def test_default_tolerance_keeps_split_when_consolidation_too_expensive():
    """Sanity check: without env override the same setup falls back to the
    cheapest split because cernyrytir-alone exceeds the 10% budget."""
    optimizer = DecklistOptimizer(_agg())
    result = await optimizer.optimize("1 A\n1 B\n", strategy="fewest_shops")

    assert result.consolidated_total_czk == 180  # = cheapest_split_total_czk
    assert {g.shop for g in result.shopping_plan} == {"najada", "cernyrytir"}


@pytest.mark.asyncio
async def test_tolerance_invalid_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    """A non-integer value is ignored; default (10%) applies."""
    monkeypatch.setenv(CONSOLIDATE_TOLERANCE_ENV, "not-a-number")
    optimizer = DecklistOptimizer(_agg())
    result = await optimizer.optimize("1 A\n1 B\n", strategy="fewest_shops")

    # Same as the default-tolerance case: split wins.
    assert result.consolidated_total_czk == 180


@pytest.mark.asyncio
async def test_tolerance_zero_or_negative_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    """0 and negative are rejected; default (10%) applies."""
    monkeypatch.setenv(CONSOLIDATE_TOLERANCE_ENV, "0")
    optimizer = DecklistOptimizer(_agg())
    result = await optimizer.optimize("1 A\n1 B\n", strategy="fewest_shops")
    assert result.consolidated_total_czk == 180

    monkeypatch.setenv(CONSOLIDATE_TOLERANCE_ENV, "-5")
    result_neg = await optimizer.optimize("1 A\n1 B\n", strategy="fewest_shops")
    assert result_neg.consolidated_total_czk == 180
