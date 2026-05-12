from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import Offer, SearchQuery, ShopId


class AccountFeatureNotSupported(RuntimeError):
    """A shop adapter doesn't implement the requested account feature."""

    def __init__(self, shop_id: str, feature: str) -> None:
        self.shop_id = shop_id
        self.feature = feature
        super().__init__(f"shop '{shop_id}' does not support {feature}")


class ShopAdapter(ABC):
    shop_id: ShopId
    base_url: str

    # Account-feature capability flags. Adapters override individually as
    # features come online; the default (all False) keeps existing
    # read-only adapters honest about what they can do.
    supports_login: bool = False
    supports_cart: bool = False
    supports_watchlist: bool = False

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[Offer]:
        """Search the shop and return raw offers (already normalized into the Offer model)."""

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        """Parse pre-fetched HTML. Default impl raises; adapters override when fixture testing."""
        raise NotImplementedError

    # --- Account features ---------------------------------------------------
    # Default impls raise AccountFeatureNotSupported. Adapters that support a
    # feature override the matching method *and* set the matching flag to True.

    async def login(self) -> None:
        raise AccountFeatureNotSupported(self.shop_id, "login")

    async def logout(self) -> None:
        raise AccountFeatureNotSupported(self.shop_id, "logout")

    async def add_to_cart(self, shop_ref: str, count: int = 1) -> dict[str, Any]:
        raise AccountFeatureNotSupported(self.shop_id, "add_to_cart")

    async def view_cart(self) -> dict[str, Any]:
        raise AccountFeatureNotSupported(self.shop_id, "view_cart")

    async def clear_cart(self) -> dict[str, Any]:
        raise AccountFeatureNotSupported(self.shop_id, "clear_cart")

    async def add_to_watchlist(self, shop_ref: str) -> dict[str, Any]:
        raise AccountFeatureNotSupported(self.shop_id, "add_to_watchlist")
