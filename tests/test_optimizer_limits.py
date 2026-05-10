"""Hard cap on unique cards per optimize_decklist call."""
from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.base import ShopAdapter
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import Condition, Offer, SearchQuery, ShopId
from cz_mtg_compare.optimizer import (
    DEFAULT_MAX_UNIQUE_CARDS,
    MAX_UNIQUE_CARDS_ENV,
    DecklistOptimizer,
)


class _StubAdapter(ShopAdapter):
    def __init__(self, shop_id: ShopId, offers: list[Offer]):
        self.shop_id = shop_id
        self.base_url = f"https://example.com/{shop_id}"
        self._offers = offers

    async def search(self, query: SearchQuery) -> list[Offer]:
        return [
            Offer(
                shop=self.shop_id,
                card_name=query.name,
                edition="X",
                condition=Condition.NM,
                foil=False,
                price_czk=10,
                stock_qty=1,
                url="https://example.com",
            )
        ]


def _decklist(unique: int) -> str:
    """Build a decklist with `unique` distinct cards summing to <= MAX_TOTAL_CARDS."""
    return "\n".join(f"1 Card{i}" for i in range(unique))


def _agg() -> Aggregator:
    return Aggregator([_StubAdapter("tolarie", [])])


@pytest.mark.asyncio
async def test_at_default_unique_limit_is_allowed():
    """Exactly DEFAULT_MAX_UNIQUE_CARDS unique entries passes."""
    optimizer = DecklistOptimizer(_agg())
    result = await optimizer.optimize(_decklist(DEFAULT_MAX_UNIQUE_CARDS))
    assert result.unique_cards == DEFAULT_MAX_UNIQUE_CARDS


@pytest.mark.asyncio
async def test_above_default_unique_limit_rejected_by_total_first():
    """101 unique × qty 1 = 101 total, which trips the parser's MAX_TOTAL_CARDS
    check first. The error mentions the total-card overflow."""
    optimizer = DecklistOptimizer(_agg())
    with pytest.raises(ValueError, match="exceeds"):
        await optimizer.optimize(_decklist(DEFAULT_MAX_UNIQUE_CARDS + 1))


@pytest.mark.asyncio
async def test_unique_limit_env_override_allows_higher(monkeypatch: pytest.MonkeyPatch):
    """When the parser's total-cards limit is also bumped, raising the unique
    cap actually lets through more cards."""
    # Bump both limits via the env var + module-level constant patch.
    monkeypatch.setenv(MAX_UNIQUE_CARDS_ENV, "150")
    monkeypatch.setattr("cz_mtg_compare.decklist.MAX_TOTAL_CARDS", 150)

    optimizer = DecklistOptimizer(_agg())
    result = await optimizer.optimize(_decklist(120))
    assert result.unique_cards == 120


@pytest.mark.asyncio
async def test_unique_limit_env_below_request_rejects(monkeypatch: pytest.MonkeyPatch):
    """Setting a smaller limit via env triggers the unique-cards rejection
    explicitly (rather than the parser's total-cards limit)."""
    monkeypatch.setenv(MAX_UNIQUE_CARDS_ENV, "5")
    optimizer = DecklistOptimizer(_agg())
    with pytest.raises(ValueError, match="unique cards"):
        await optimizer.optimize(_decklist(10))


@pytest.mark.asyncio
async def test_unique_limit_invalid_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    """A non-integer env value is ignored; default (100) applies."""
    monkeypatch.setenv(MAX_UNIQUE_CARDS_ENV, "not-a-number")
    optimizer = DecklistOptimizer(_agg())
    # 80 unique passes under the default 100 limit.
    result = await optimizer.optimize(_decklist(80))
    assert result.unique_cards == 80


@pytest.mark.asyncio
async def test_unique_limit_zero_or_negative_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(MAX_UNIQUE_CARDS_ENV, "0")
    optimizer = DecklistOptimizer(_agg())
    # 80 unique still passes under the default 100 limit.
    result = await optimizer.optimize(_decklist(80))
    assert result.unique_cards == 80
