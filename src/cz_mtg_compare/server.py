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
) -> list[Offer]:
    """Search a Magic: The Gathering single card across Czech shops.

    Returns a flat list of offers sorted by price_czk ascending. Each offer has shop, edition,
    condition, language, foil, price_czk, stock_qty, and a deep-link url.
    """
    query = SearchQuery(name=name, edition=edition, in_stock_only=in_stock_only)
    return await _aggregator.search(query, shops=shops)


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
) -> DecklistOptimization:
    """Resolve a Magic decklist (Arena/MTGO text) against all shops in parallel
    and return:

    - `picks`: cheapest in-stock offer per card (multi-shop "greedy split")
    - `cheapest_split_total_czk`: grand total of that split
    - `per_shop_bundles`: how each individual shop covers the decklist (cards
      covered, cards missing, single-shop total in CZK), sorted best-to-worst
    - `cheapest_split_missing`: cards no shop has in stock

    The decklist must contain at most 100 cards (Commander deck size). Format
    example:

        4 Lightning Bolt
        4 Counterspell
        2 Sol Ring (CMR) 263

        Sideboard
        1 Negate
    """
    return await _optimizer.optimize(decklist, in_stock_only=in_stock_only)


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
