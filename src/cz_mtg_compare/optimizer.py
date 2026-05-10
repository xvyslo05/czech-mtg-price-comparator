from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from pydantic import BaseModel, Field

from .aggregator import Aggregator
from .decklist import DeckSection, DecklistEntry, parse_decklist
from .models import Condition, Offer, SearchQuery, ShopId

log = logging.getLogger(__name__)


# When picking "best" per card we prefer lower price; among equal prices, we
# prefer better condition and non-foil (foil is usually a different SKU,
# but if a foil is somehow cheaper we still use it).
_CONDITION_RANK: dict[Condition, int] = {
    Condition.NM: 0,
    Condition.EX: 1,
    Condition.LP: 2,
    Condition.GD: 3,
    Condition.PL: 4,
    Condition.HP: 5,
    Condition.UNKNOWN: 6,
}


class CardPick(BaseModel):
    """One card resolved to a chosen offer (or marked missing)."""

    name: str
    quantity: int
    section: DeckSection
    chosen: Offer | None = None
    chosen_total_czk: int | None = None  # quantity * chosen.price_czk
    alternatives: list[Offer] = Field(default_factory=list)
    missing: bool = False


class ShopBundle(BaseModel):
    """How well a single shop covers the decklist on its own."""

    shop: ShopId
    covered_cards: int
    missing_cards: list[str]
    total_czk: int  # sum of cheapest in-stock offer per covered card
    coverage_pct: float  # covered_cards / total_unique_cards * 100


class ShoppingLine(BaseModel):
    """One line in the shopping plan: a card to buy from a specific shop."""

    quantity: int
    name: str
    edition: str | None = None
    condition: Condition
    foil: bool
    unit_price_czk: int
    subtotal_czk: int  # quantity * unit_price_czk
    url: str


class ShoppingGroup(BaseModel):
    """All cards to buy from a single shop, grouped from the cheapest split."""

    shop: ShopId
    lines: list[ShoppingLine]
    items_count: int   # number of distinct entries (lines)
    cards_count: int   # sum of quantities
    subtotal_czk: int  # sum of line subtotals


class DecklistOptimization(BaseModel):
    total_cards: int
    unique_cards: int
    parser_errors: list[str] = Field(default_factory=list)

    picks: list[CardPick]
    cheapest_split_total_czk: int
    cheapest_split_missing: list[str]

    # Same data as `picks`, regrouped by shop for direct rendering as a
    # shopping plan / chart. Sorted by descending shop subtotal.
    shopping_plan: list[ShoppingGroup] = Field(default_factory=list)

    per_shop_bundles: list[ShopBundle]


def _offer_score(offer: Offer) -> tuple[int, int, int]:
    """Sort key for picking the best offer for a card.

    Cheaper wins; ties broken by better condition; then non-foil preferred.
    """
    return (
        offer.price_czk,
        _CONDITION_RANK.get(offer.condition, 99),
        1 if offer.foil else 0,
    )


def _pick_best(offers: Iterable[Offer]) -> Offer | None:
    return min(offers, key=_offer_score, default=None)


class DecklistOptimizer:
    def __init__(self, aggregator: Aggregator | None = None) -> None:
        self._aggregator = aggregator or Aggregator()

    async def optimize(
        self,
        decklist_text: str,
        in_stock_only: bool = True,
        include_non_playable: bool = False,
    ) -> DecklistOptimization:
        parsed = parse_decklist(decklist_text)

        # Sum quantities per unique (case-insensitive) card name; keep first-seen casing
        # and section, plus aggregated quantity.
        per_name: dict[str, DecklistEntry] = {}
        for e in parsed.entries:
            key = e.name.lower()
            if key in per_name:
                per_name[key] = per_name[key].model_copy(
                    update={"quantity": per_name[key].quantity + e.quantity}
                )
            else:
                per_name[key] = e

        unique_entries = list(per_name.values())

        # Fan out one aggregator.search() per unique card. The aggregator already
        # parallelizes across shops; httpx semaphore caps per-host concurrency.
        async def _search(entry: DecklistEntry) -> tuple[DecklistEntry, list[Offer]]:
            offers = await self._aggregator.search(
                SearchQuery(
                    name=entry.name,
                    in_stock_only=in_stock_only,
                    include_non_playable=include_non_playable,
                )
            )
            return entry, offers

        results = await asyncio.gather(*(_search(e) for e in unique_entries))

        picks: list[CardPick] = []
        for entry, offers in results:
            chosen = _pick_best(offers)
            picks.append(
                CardPick(
                    name=entry.name,
                    quantity=entry.quantity,
                    section=entry.section,
                    chosen=chosen,
                    chosen_total_czk=(chosen.price_czk * entry.quantity) if chosen else None,
                    alternatives=sorted(offers, key=_offer_score)[:5],
                    missing=chosen is None,
                )
            )

        cheapest_split_total = sum(p.chosen_total_czk or 0 for p in picks if p.chosen)
        cheapest_split_missing = [p.name for p in picks if p.missing]

        per_shop_bundles = self._build_shop_bundles(unique_entries, results)
        shopping_plan = _build_shopping_plan(picks)

        return DecklistOptimization(
            total_cards=parsed.total_cards,
            unique_cards=len(unique_entries),
            parser_errors=parsed.errors,
            picks=picks,
            cheapest_split_total_czk=cheapest_split_total,
            cheapest_split_missing=cheapest_split_missing,
            shopping_plan=shopping_plan,
            per_shop_bundles=per_shop_bundles,
        )

    def _build_shop_bundles(
        self,
        unique_entries: list[DecklistEntry],
        results: list[tuple[DecklistEntry, list[Offer]]],
    ) -> list[ShopBundle]:
        # Per-card per-shop best offer.
        per_card_per_shop: dict[str, dict[ShopId, Offer]] = {}
        for entry, offers in results:
            best_in_shop: dict[ShopId, Offer] = {}
            for o in offers:
                cur = best_in_shop.get(o.shop)
                if cur is None or _offer_score(o) < _offer_score(cur):
                    best_in_shop[o.shop] = o
            per_card_per_shop[entry.name.lower()] = best_in_shop

        total_unique = max(len(unique_entries), 1)
        bundles: list[ShopBundle] = []
        for shop in self._aggregator.shop_ids:
            covered = 0
            missing: list[str] = []
            total = 0
            for entry in unique_entries:
                offer = per_card_per_shop.get(entry.name.lower(), {}).get(shop)
                if offer is None:
                    missing.append(entry.name)
                else:
                    covered += 1
                    total += offer.price_czk * entry.quantity
            bundles.append(
                ShopBundle(
                    shop=shop,
                    covered_cards=covered,
                    missing_cards=missing,
                    total_czk=total,
                    coverage_pct=round(covered / total_unique * 100, 1),
                )
            )

        # Sort bundles best-to-worst: highest coverage, then lowest total.
        bundles.sort(key=lambda b: (-b.coverage_pct, b.total_czk))
        return bundles


def _build_shopping_plan(picks: list[CardPick]) -> list[ShoppingGroup]:
    """Regroup the cheapest-split `picks` by shop for chart-friendly output."""
    by_shop: dict[ShopId, list[ShoppingLine]] = {}
    for p in picks:
        if p.chosen is None:
            continue
        line = ShoppingLine(
            quantity=p.quantity,
            name=p.name,
            edition=p.chosen.edition,
            condition=p.chosen.condition,
            foil=p.chosen.foil,
            unit_price_czk=p.chosen.price_czk,
            subtotal_czk=p.quantity * p.chosen.price_czk,
            url=p.chosen.url,
        )
        by_shop.setdefault(p.chosen.shop, []).append(line)

    groups: list[ShoppingGroup] = []
    for shop, lines in by_shop.items():
        # Stable, readable order within a shop: cheapest first, then by name.
        lines.sort(key=lambda l: (l.unit_price_czk, l.name.lower()))
        groups.append(
            ShoppingGroup(
                shop=shop,
                lines=lines,
                items_count=len(lines),
                cards_count=sum(l.quantity for l in lines),
                subtotal_czk=sum(l.subtotal_czk for l in lines),
            )
        )

    # Most cards / largest spend first — the "primary shop" surfaces at the top.
    groups.sort(key=lambda g: (-g.subtotal_czk, -g.cards_count, g.shop))
    return groups
