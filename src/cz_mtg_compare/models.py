from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

ShopId = Literal[
    "cernyrytir",
    "najada",
    "blacklotus",
    "tolarie",
    "rishada",
    "untap",
    "cardmarket",
]
ALL_SHOPS: tuple[ShopId, ...] = (
    "cernyrytir",
    "najada",
    "blacklotus",
    "tolarie",
    "rishada",
    "untap",
    "cardmarket",
)


class Condition(str, Enum):
    NM = "NM"
    EX = "EX"
    GD = "GD"
    LP = "LP"
    PL = "PL"
    HP = "HP"
    UNKNOWN = "?"


class SearchQuery(BaseModel):
    name: str = Field(min_length=1)
    edition: str | None = None
    in_stock_only: bool = True
    # Display-only products (Art Series, oversized, helper cards) are filtered
    # out by default because they aren't legal in constructed formats. Set
    # this to True for collectors who explicitly want them.
    include_non_playable: bool = False


class Offer(BaseModel):
    shop: ShopId
    card_name: str
    edition: str | None = None
    set_code: str | None = None
    condition: Condition = Condition.UNKNOWN
    language: str | None = None
    foil: bool = False
    price_czk: int
    stock_qty: int = 0
    url: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ShopStatus(BaseModel):
    shop: ShopId
    ok: bool
    last_error: str | None = None
    last_offer_count: int | None = None
