import logging
import os

from .base import ShopAdapter
from .blacklotus import BlackLotusAdapter
from .cardmarket import CardmarketAdapter, MkmCredentials
from .cernyrytir import CernyRytirAdapter
from .najada import NajadaAdapter
from .rishada import RishadaAdapter
from .tolarie import TolarieAdapter
from .untap import UntapAdapter

__all__ = [
    "ShopAdapter",
    "BlackLotusAdapter",
    "CardmarketAdapter",
    "CernyRytirAdapter",
    "MkmCredentials",
    "NajadaAdapter",
    "RishadaAdapter",
    "TolarieAdapter",
    "UntapAdapter",
    "build_default_adapters",
    "DISABLED_SHOPS_ENV",
]

DISABLED_SHOPS_ENV = "CZ_MTG_DISABLED_SHOPS"

log = logging.getLogger(__name__)


def _disabled_from_env() -> set[str]:
    raw = os.environ.get(DISABLED_SHOPS_ENV, "")
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def build_default_adapters() -> list[ShopAdapter]:
    """Adapters enabled by default. Adapters are added here as they come online.

    Cardmarket is only included when its OAuth1 credentials are present in the
    environment; otherwise it's silently dropped to avoid useless 401s.

    Shops listed in the ``CZ_MTG_DISABLED_SHOPS`` env var (comma-separated, case
    insensitive — e.g. ``CZ_MTG_DISABLED_SHOPS=blacklotus,untap``) are removed
    here so they never even get instantiated. Useful for users who have a
    standing reason to never query a particular shop.
    """
    candidates: list[ShopAdapter] = [
        TolarieAdapter(),
        NajadaAdapter(),
        BlackLotusAdapter(),
        CernyRytirAdapter(),
        RishadaAdapter(),
        UntapAdapter(),
    ]
    creds = MkmCredentials.from_env()
    if creds is not None:
        candidates.append(CardmarketAdapter(credentials=creds))

    disabled = _disabled_from_env()
    if not disabled:
        return candidates

    kept: list[ShopAdapter] = []
    dropped: list[str] = []
    for adapter in candidates:
        if adapter.shop_id.lower() in disabled:
            dropped.append(adapter.shop_id)
        else:
            kept.append(adapter)
    if dropped:
        log.info("disabled adapters via %s: %s", DISABLED_SHOPS_ENV, ", ".join(dropped))
    return kept
