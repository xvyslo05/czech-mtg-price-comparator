from .base import ShopAdapter
from .blacklotus import BlackLotusAdapter
from .cardmarket import CardmarketAdapter, MkmCredentials
from .cernyrytir import CernyRytirAdapter
from .najada import NajadaAdapter
from .tolarie import TolarieAdapter

__all__ = [
    "ShopAdapter",
    "BlackLotusAdapter",
    "CardmarketAdapter",
    "CernyRytirAdapter",
    "MkmCredentials",
    "NajadaAdapter",
    "TolarieAdapter",
    "build_default_adapters",
]


def build_default_adapters() -> list[ShopAdapter]:
    """Adapters enabled by default. Adapters are added here as they come online.

    Cardmarket is only included when its OAuth1 credentials are present in the
    environment; otherwise it's silently dropped to avoid useless 401s.
    """
    adapters: list[ShopAdapter] = [
        TolarieAdapter(),
        NajadaAdapter(),
        BlackLotusAdapter(),
        CernyRytirAdapter(),
    ]
    creds = MkmCredentials.from_env()
    if creds is not None:
        adapters.append(CardmarketAdapter(credentials=creds))
    return adapters
