from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from .aggregator import Aggregator
from .http_client import close_client
from .models import Offer, SearchQuery, ShopId, ShopStatus
from .optimizer import DecklistOptimization, DecklistOptimizer
from .scryfall import CardInfo, ScryfallClient

log = logging.getLogger("cz_mtg_compare")

mcp = FastMCP("cz-mtg-compare")
_aggregator = Aggregator()
_optimizer = DecklistOptimizer(_aggregator)
_scryfall = ScryfallClient()


@mcp.tool()
async def search_card(
    name: str,
    edition: str | None = None,
    in_stock_only: bool = True,
    shops: list[ShopId] | None = None,
    exclude_shops: list[ShopId] | None = None,
    include_non_playable: bool = False,
) -> list[Offer]:
    """Search a Magic: The Gathering single card across Czech shops.

    Returns a flat list of offers sorted by price_czk ascending. Each offer has shop, edition,
    condition, language, foil, price_czk, stock_qty, and a deep-link url.

    Shop selection:
    - `shops`: optional allow-list. If given, only these shops are queried. None = all.
    - `exclude_shops`: optional deny-list. Any shop here is dropped after `shops` is applied.
      Use this when the user wants results "from everywhere except shop X".

    Display-only products (Art Series, oversized, helper / tip / checklist cards,
    spindowns) are excluded by default because they aren't legal in constructed
    Magic formats. Pass `include_non_playable=True` if you specifically want them
    (e.g. for a collector / art print query).
    """
    query = SearchQuery(
        name=name,
        edition=edition,
        in_stock_only=in_stock_only,
        include_non_playable=include_non_playable,
    )
    return await _aggregator.search(query, shops=shops, exclude_shops=exclude_shops)


@mcp.tool()
def list_shops() -> list[ShopStatus]:
    """List configured shops with their last-call status."""
    return _aggregator.status()


@mcp.tool()
async def lookup_card(name: str, exact: bool = False) -> CardInfo | None:
    """Resolve a Magic: The Gathering card name via Scryfall.

    Returns canonical name, set code/name, oracle text, mana cost, image URL,
    Scryfall URI. Useful for disambiguating reprints, validating spelling, or
    fetching multilingual names.

    Set `exact=True` to require an exact name match; default is fuzzy.
    Returns null if no card matches.
    """
    return await _scryfall.resolve(name, exact=exact)


@mcp.tool()
async def optimize_decklist(
    decklist: str,
    in_stock_only: bool = True,
    include_non_playable: bool = False,
    shops: list[ShopId] | None = None,
    exclude_shops: list[ShopId] | None = None,
) -> DecklistOptimization:
    """Resolve a Magic decklist (Arena/MTGO text) against all shops in parallel
    and return:

    - `picks`: cheapest in-stock offer per card (multi-shop "greedy split")
    - `cheapest_split_total_czk`: grand total of that split
    - `shopping_plan`: the same picks regrouped per shop — render this as a
      summary table for the user. Each group has the shop name, an `lines`
      list (each: quantity, card name, edition, condition, foil, unit price,
      subtotal, url) and a `subtotal_czk`. Groups are sorted by descending
      shop subtotal so the "primary shop" surfaces first.
    - `per_shop_bundles`: how each individual shop covers the decklist on its
      own (cards covered, cards missing, single-shop total CZK), sorted
      best-to-worst
    - `cheapest_split_missing`: cards no shop has in stock

    When presenting results to the user for a multi-card query, render the
    `shopping_plan` as a per-shop chart: one section per shop with a table
    of cards to buy from it, plus the shop subtotal. Then show the grand
    total `cheapest_split_total_czk` and any `cheapest_split_missing`.

    The decklist must contain at most 100 cards in total (Commander deck size)
    AND at most 100 *unique* cards (one HTTP request per unique card per shop —
    the unique-cards cap exists to keep a single tool call from spawning a
    runaway number of requests). Override the unique cap via the
    `CZ_MTG_MAX_UNIQUE_CARDS` env var if you need a higher limit. Format
    example:

        4 Lightning Bolt
        4 Counterspell
        2 Sol Ring (CMR) 263

        Sideboard
        1 Negate

    Display-only products (Art Series, oversized, helper cards) are excluded by
    default. Pass `include_non_playable=True` to keep them in the picks.

    Shop selection mirrors `search_card`:
    - `shops`: allow-list (None = all configured shops).
    - `exclude_shops`: deny-list (applied after the allow-list).
    Excluded shops also disappear from `per_shop_bundles` so the chart only
    shows the shops the user actually wants to see.
    """
    return await _optimizer.optimize(
        decklist,
        in_stock_only=in_stock_only,
        include_non_playable=include_non_playable,
        shops=shops,
        exclude_shops=exclude_shops,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        mcp.run()
    finally:
        import asyncio

        try:
            asyncio.run(close_client())
        except RuntimeError:
            pass


if __name__ == "__main__":
    main()
