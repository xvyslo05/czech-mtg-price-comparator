"""Transport-agnostic service layer.

All tool logic lives here. The MCP server (``server.py``) and any future
transport (FastAPI, hosted MCP, CLI) construct a ``CardCompareService`` and
forward calls. Keeping this module free of MCP/HTTP imports lets the same
implementation power multiple delivery surfaces without coupling.
"""

from __future__ import annotations

from typing import Any

from .adapters.base import AccountFeatureNotSupported
from .aggregator import Aggregator
from .credentials import has_credentials
from .models import Offer, SearchQuery, ShopId, ShopStatus
from .optimizer import DecklistOptimization, DecklistOptimizer, Strategy
from .scryfall import CardInfo, ScryfallClient


class CardCompareService:
    def __init__(
        self,
        aggregator: Aggregator | None = None,
        optimizer: DecklistOptimizer | None = None,
        scryfall: ScryfallClient | None = None,
    ) -> None:
        self.aggregator = aggregator or Aggregator()
        self.optimizer = optimizer or DecklistOptimizer(self.aggregator)
        self.scryfall = scryfall or ScryfallClient()

    async def search_card(
        self,
        name: str,
        edition: str | None = None,
        in_stock_only: bool = True,
        shops: list[ShopId] | None = None,
        exclude_shops: list[ShopId] | None = None,
        include_non_playable: bool = False,
    ) -> list[Offer]:
        query = SearchQuery(
            name=name,
            edition=edition,
            in_stock_only=in_stock_only,
            include_non_playable=include_non_playable,
        )
        return await self.aggregator.search(query, shops=shops, exclude_shops=exclude_shops)

    def list_shops(self) -> list[ShopStatus]:
        return self.aggregator.status()

    async def lookup_card(self, name: str, exact: bool = False) -> CardInfo | None:
        return await self.scryfall.resolve(name, exact=exact)

    async def optimize_decklist(
        self,
        decklist: str,
        in_stock_only: bool = True,
        include_non_playable: bool = False,
        shops: list[ShopId] | None = None,
        exclude_shops: list[ShopId] | None = None,
        strategy: Strategy = "cheapest",
    ) -> DecklistOptimization:
        return await self.optimizer.optimize(
            decklist,
            in_stock_only=in_stock_only,
            include_non_playable=include_non_playable,
            shops=shops,
            exclude_shops=exclude_shops,
            strategy=strategy,
        )

    def shop_account_capabilities(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for adapter in self.aggregator.adapters:
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

    async def shop_login(self, shop: ShopId) -> dict[str, Any]:
        adapter = self._require_adapter(shop)
        require_login(adapter)
        await adapter.login()
        return {"shop": shop, "ok": True}

    async def add_to_cart(self, shop: ShopId, shop_ref: str, count: int = 1) -> dict[str, Any]:
        adapter = self._require_adapter(shop)
        require_capability(adapter, "cart")
        return await adapter.add_to_cart(shop_ref, count=count)

    async def view_cart(self, shop: ShopId) -> dict[str, Any]:
        adapter = self._require_adapter(shop)
        require_capability(adapter, "cart")
        return await adapter.view_cart()

    async def clear_cart(self, shop: ShopId) -> dict[str, Any]:
        adapter = self._require_adapter(shop)
        require_capability(adapter, "cart")
        return await adapter.clear_cart()

    async def add_to_watchlist(self, shop: ShopId, shop_ref: str) -> dict[str, Any]:
        adapter = self._require_adapter(shop)
        require_capability(adapter, "watchlist")
        return await adapter.add_to_watchlist(shop_ref)

    def _require_adapter(self, shop: ShopId):
        adapter = self.aggregator.get_adapter(shop)
        if adapter is None:
            raise ValueError(
                f"shop '{shop}' is not enabled "
                "(check `list_shops`; the shop may be disabled via CZ_MTG_DISABLED_SHOPS)"
            )
        return adapter


def require_capability(adapter, feature: str) -> None:
    """Refuse a cart/watchlist call when the adapter's capability flag is False,
    even if the underlying method happens to be implemented. The flag is the
    source of truth for what's exposed at the transport layer.
    """
    flag = f"supports_{feature}"
    if not getattr(adapter, flag, False):
        raise AccountFeatureNotSupported(adapter.shop_id, feature)


def require_login(adapter) -> None:
    if not getattr(adapter, "supports_login", False):
        raise AccountFeatureNotSupported(adapter.shop_id, "login")


default_service = CardCompareService()
