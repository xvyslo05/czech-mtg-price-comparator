from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from .adapters.base import AccountFeatureNotSupported
from .aggregator import Aggregator
from .credentials import CredentialError, has_credentials
from .http_client import close_client
from .models import Offer, SearchQuery, ShopId, ShopStatus
from .optimizer import DecklistOptimization, DecklistOptimizer, Strategy
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
    strategy: Strategy = "cheapest",
) -> DecklistOptimization:
    """Resolve a Magic decklist (Arena/MTGO text) against all shops in parallel
    and return a `shopping_plan` built under one of two strategies.

    Strategies (`strategy` param):
    - `"cheapest"` (default): per-card greedy lowest price across all shops.
      Minimises total CZK; may fragment the order across many shops.
    - `"fewest_shops"`: minimises the number of distinct shops in the final
      plan so the user places fewer separate orders / pays less shipping.
      Stays within 10% of the cheapest-split total by default; override the
      tolerance via the `CZ_MTG_CONSOLIDATE_TOLERANCE_PCT` env var (integer
      percent). When no single shop can cover everything, cards missing from
      the chosen set fall back to the globally-cheapest offer (which may add
      one or more shops to the plan).

    Response shape:
    - `strategy`: echoes the strategy that produced the picks/plan.
    - `picks`: chosen offer per card under the active strategy.
    - `shopping_plan`: the picks regrouped per shop — render as a summary
      table. Each group has the shop name, a `lines` list (quantity, card
      name, edition, condition, foil, unit price, subtotal, url) and a
      `subtotal_czk`. Groups sorted by descending shop subtotal.
    - `cheapest_split_total_czk`: total of the per-card cheapest split,
      always populated as a reference even in fewest_shops mode.
    - `consolidated_total_czk`: total of the consolidated plan in
      fewest_shops mode (equals the sum of `shopping_plan` subtotals).
      `null` in cheapest mode.
    - `per_shop_bundles`: how each individual shop covers the decklist on
      its own (cards covered/missing, single-shop total CZK), sorted
      best-to-worst.
    - `cheapest_split_missing`: cards no shop has in stock.

    When presenting results to the user, render the `shopping_plan` as a
    per-shop chart: one section per shop with a table of cards to buy from
    it, plus the shop subtotal. Then show the headline total (use
    `consolidated_total_czk` for fewest_shops, otherwise
    `cheapest_split_total_czk`) and any `cheapest_split_missing`. In
    fewest_shops mode it's useful to also surface the delta vs. the cheapest
    split so the user can see the consolidation premium.

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
        strategy=strategy,
    )


def _require_adapter(shop: ShopId):
    adapter = _aggregator.get_adapter(shop)
    if adapter is None:
        raise ValueError(
            f"shop '{shop}' is not enabled "
            "(check `list_shops`; the shop may be disabled via CZ_MTG_DISABLED_SHOPS)"
        )
    return adapter


@mcp.tool()
def shop_account_capabilities() -> list[dict[str, Any]]:
    """Per-shop account-feature support and credential status.

    Returns one entry per enabled shop with:
    - ``shop``: shop id
    - ``supports_login`` / ``supports_cart`` / ``supports_watchlist``: capability flags
    - ``credentials_configured``: whether ``CZ_MTG_<SHOP>_USER`` and
      ``CZ_MTG_<SHOP>_PASS`` env vars are both set (does NOT resolve
      ``op://...`` 1Password references — just a presence check).

    Use this to tell the user which shops they can actually log into and which
    account actions are available before attempting them.
    """
    out: list[dict[str, Any]] = []
    for adapter in _aggregator.adapters:
        out.append(
            {
                "shop": adapter.shop_id,
                "supports_login": adapter.supports_login,
                "supports_cart": adapter.supports_cart,
                "supports_watchlist": adapter.supports_watchlist,
                "credentials_configured": has_credentials(adapter.shop_id),
            }
        )
    return out


@mcp.tool()
async def shop_login(shop: ShopId) -> dict[str, Any]:
    """Authenticate against a shop using its CZ_MTG_<SHOP>_USER / _PASS credentials.

    The credentials may be literal strings or 1Password secret references of the
    form ``op://Vault/Item/Field`` — the latter is resolved via the ``op`` CLI
    on first use. Login sessions are kept in-process for as long as the MCP
    server runs and are reused automatically by ``add_to_cart`` / ``view_cart``.

    Returns ``{"shop": ..., "ok": true}`` on success. Raises with a clear message
    if the shop doesn't support login, credentials are missing/invalid, or the
    1Password reference can't be resolved.
    """
    adapter = _require_adapter(shop)
    await adapter.login()
    return {"shop": shop, "ok": True}


@mcp.tool()
async def add_to_cart(shop: ShopId, offer: Offer, count: int = 1) -> dict[str, Any]:
    """Add a previously-found Offer to the shop's online cart.

    ``offer`` must come from ``search_card`` / ``optimize_decklist`` on the SAME
    server run so that the per-shop ``shop_ref`` (product/article id) is
    populated. Logs in automatically on first call if not already authenticated.

    Returns the shop's raw response (e.g. updated cart item record). Raises if
    the shop doesn't support cart operations, the offer is missing ``shop_ref``,
    or credentials are misconfigured.
    """
    adapter = _require_adapter(shop)
    return await adapter.add_to_cart(offer, count=count)


@mcp.tool()
async def view_cart(shop: ShopId) -> dict[str, Any]:
    """Return the current contents of the shop's online cart for the
    authenticated user. Logs in automatically if needed.
    """
    adapter = _require_adapter(shop)
    return await adapter.view_cart()


@mcp.tool()
async def clear_cart(shop: ShopId) -> dict[str, Any]:
    """Remove every item from the shop's online cart for the authenticated user.

    Returns ``{"removed_items": <int>}``. Destructive — confirm with the user
    before calling.
    """
    adapter = _require_adapter(shop)
    return await adapter.clear_cart()


@mcp.tool()
async def add_to_watchlist(shop: ShopId, offer: Offer) -> dict[str, Any]:
    """Add an Offer to the shop's wishlist / watchlist / wantlist if the shop
    supports it. Raises with a clear message otherwise — check
    ``shop_account_capabilities`` first.
    """
    adapter = _require_adapter(shop)
    return await adapter.add_to_watchlist(offer)


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
