"""Request schemas for the HTTP layer. Response shapes reuse the core
pydantic models (Offer, ShopStatus, DecklistOptimization) directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import ShopId
from ..optimizer import Strategy


class OptimizeDecklistRequest(BaseModel):
    decklist: str = Field(min_length=1)
    in_stock_only: bool = True
    include_non_playable: bool = False
    shops: list[ShopId] | None = None
    exclude_shops: list[ShopId] | None = None
    strategy: Strategy = "cheapest"
