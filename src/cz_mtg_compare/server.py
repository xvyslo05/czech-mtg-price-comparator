from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from .aggregator import Aggregator
from .http_client import close_client
from .models import Offer, SearchQuery, ShopId, ShopStatus

log = logging.getLogger("cz_mtg_compare")

mcp = FastMCP("cz-mtg-compare")
_aggregator = Aggregator()


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
