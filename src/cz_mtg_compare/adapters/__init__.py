from .base import ShopAdapter
from .blacklotus import BlackLotusAdapter
from .cernyrytir import CernyRytirAdapter
from .najada import NajadaAdapter
from .tolarie import TolarieAdapter

__all__ = [
    "ShopAdapter",
    "BlackLotusAdapter",
    "CernyRytirAdapter",
    "NajadaAdapter",
    "TolarieAdapter",
    "build_default_adapters",
]


def build_default_adapters() -> list[ShopAdapter]:
    """Adapters enabled by default. Adapters are added here as they come online."""
    return [
        TolarieAdapter(),
        NajadaAdapter(),
        BlackLotusAdapter(),
        CernyRytirAdapter(),
    ]
