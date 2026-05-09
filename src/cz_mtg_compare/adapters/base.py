from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Offer, SearchQuery, ShopId


class ShopAdapter(ABC):
    shop_id: ShopId
    base_url: str

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[Offer]:
        """Search the shop and return raw offers (already normalized into the Offer model)."""

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        """Parse pre-fetched HTML. Default impl raises; adapters override when fixture testing."""
        raise NotImplementedError
