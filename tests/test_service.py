"""Tests for the transport-agnostic service layer.

``CardCompareService`` is the contract every delivery surface (MCP server,
FastAPI app, future hosted MCP) depends on. These tests exercise it
directly — no MCP, no HTTP — to lock down:

- Construction with explicit dependency injection (the whole reason for
  the extraction; if this breaks, A2 / G4 / tests all break).
- Each tool method routes through its injected dependency, not a global.
- ``_require_adapter`` rejects unknown shop ids with a clear ValueError.
- ``require_capability`` / ``require_login`` enforce flags regardless of
  whether the adapter happens to implement the underlying method.
- ``default_service`` is a fully-constructed ``CardCompareService`` so
  existing module-level callers (server.py, the MCP wrappers) keep working.
"""

from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.base import AccountFeatureNotSupported, ShopAdapter
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import SearchQuery, ShopId
from cz_mtg_compare.optimizer import DecklistOptimizer
from cz_mtg_compare.service import (
    CardCompareService,
    default_service,
    require_capability,
    require_login,
)

from ._factories import StubAdapter, make_offer


def _service(*adapters: ShopAdapter) -> CardCompareService:
    agg = Aggregator(adapters=list(adapters))
    opt = DecklistOptimizer(agg)
    return CardCompareService(aggregator=agg, optimizer=opt)


def test_default_service_is_card_compare_service() -> None:
    """The module-level singleton must be a CardCompareService instance and
    expose all the methods the MCP server forwards to. Catches a regression
    where someone replaces it with a shim or removes a method."""
    assert isinstance(default_service, CardCompareService)
    for name in (
        "search_card",
        "list_shops",
        "lookup_card",
        "optimize_decklist",
        "shop_account_capabilities",
        "shop_login",
        "add_to_cart",
        "view_cart",
        "clear_cart",
        "add_to_watchlist",
    ):
        assert callable(getattr(default_service, name))


def test_constructs_with_injected_dependencies() -> None:
    """Passing aggregator/optimizer explicitly must override the defaults —
    not just be ignored. This is the contract A2 and any future transport
    relies on; without it, every test would have to fight a global."""
    stub = StubAdapter("tolarie", offers=[])
    agg = Aggregator(adapters=[stub])
    opt = DecklistOptimizer(agg)
    svc = CardCompareService(aggregator=agg, optimizer=opt)
    assert svc.aggregator is agg
    assert svc.optimizer is opt
    assert svc.list_shops()[0].shop == "tolarie"


def test_constructs_with_no_args_uses_real_adapters() -> None:
    """Default construction must still work — otherwise the
    module-level ``default_service`` would fail at import time and the
    MCP server would never start."""
    svc = CardCompareService()
    shops = {s.shop for s in svc.list_shops()}
    assert shops  # whatever the real default is, must be non-empty


@pytest.mark.asyncio
async def test_search_card_uses_injected_aggregator() -> None:
    """search_card builds a SearchQuery and dispatches via the injected
    aggregator — proves the service isn't accidentally talking to a
    global aggregator."""
    tol = StubAdapter("tolarie", offers=[make_offer(shop="tolarie", price=70)])
    naj = StubAdapter("najada", offers=[make_offer(shop="najada", price=30)])
    svc = _service(tol, naj)

    offers = await svc.search_card(name="Lightning Bolt")
    assert [o.shop for o in offers] == ["najada", "tolarie"]
    assert tol.call_count == 1
    assert naj.call_count == 1


@pytest.mark.asyncio
async def test_search_card_passes_through_filters() -> None:
    """The keyword arguments aren't just accepted — they're forwarded to the
    aggregator's filter pipeline. Exercises shops + exclude_shops + the
    in_stock_only / include_non_playable plumbing."""
    tol = StubAdapter("tolarie", offers=[make_offer(shop="tolarie", price=70)])
    naj = StubAdapter("najada", offers=[make_offer(shop="najada", price=30)])
    bla = StubAdapter("blacklotus", offers=[make_offer(shop="blacklotus", price=50)])
    svc = _service(tol, naj, bla)

    # Allow-list with deny-list applied after.
    offers = await svc.search_card(
        name="Lightning Bolt",
        shops=["tolarie", "najada"],
        exclude_shops=["najada"],
    )
    assert [o.shop for o in offers] == ["tolarie"]


@pytest.mark.asyncio
async def test_optimize_decklist_uses_injected_optimizer() -> None:
    tol = StubAdapter(
        "tolarie",
        table={"Lightning Bolt": [make_offer(shop="tolarie", price=35, stock_qty=4)]},
    )
    svc = _service(tol)
    result = await svc.optimize_decklist("4 Lightning Bolt")
    assert result.strategy == "cheapest"
    assert result.cheapest_split_total_czk == 140
    assert result.picks[0].chosen is not None
    assert result.picks[0].chosen.shop == "tolarie"


def test_require_adapter_raises_for_unknown_shop() -> None:
    svc = _service(StubAdapter("tolarie", offers=[]))
    with pytest.raises(ValueError) as exc:
        svc._require_adapter("najada")
    assert "najada" in str(exc.value)
    assert "list_shops" in str(exc.value)


def test_require_adapter_returns_adapter_for_known_shop() -> None:
    tol = StubAdapter("tolarie", offers=[])
    svc = _service(tol)
    assert svc._require_adapter("tolarie") is tol


def test_require_capability_uses_flag_not_method_presence() -> None:
    """Sanity duplicate of the gate test — pinned in service_test so that the
    capability semantics are anchored to the service module itself, not just
    to the MCP wrapper."""
    stub = StubAdapter("tolarie", offers=[])
    # StubAdapter inherits supports_cart=False from ShopAdapter
    assert stub.supports_cart is False
    with pytest.raises(AccountFeatureNotSupported) as exc:
        require_capability(stub, "cart")
    assert exc.value.shop_id == "tolarie"
    assert exc.value.feature == "cart"


def test_require_login_raises_when_login_unsupported() -> None:
    stub = StubAdapter("tolarie", offers=[])
    with pytest.raises(AccountFeatureNotSupported) as exc:
        require_login(stub)
    assert exc.value.feature == "login"


@pytest.mark.asyncio
async def test_add_to_cart_refuses_when_capability_off() -> None:
    """End-to-end: the service-level cart entry point must refuse before
    even consulting the adapter when supports_cart is False."""
    stub = StubAdapter("tolarie", offers=[])
    svc = _service(stub)
    with pytest.raises(AccountFeatureNotSupported) as exc:
        await svc.add_to_cart("tolarie", "anything", count=1)
    assert exc.value.feature == "cart"


@pytest.mark.asyncio
async def test_shop_login_refuses_when_login_off() -> None:
    stub = StubAdapter("tolarie", offers=[])
    svc = _service(stub)
    with pytest.raises(AccountFeatureNotSupported):
        await svc.shop_login("tolarie")


def test_shop_account_capabilities_reflects_adapter_flags() -> None:
    """The capability summary surface — used by the MCP tool and the future
    web settings page — must echo each adapter's flags as-is."""

    class _LoginOnly(ShopAdapter):
        shop_id: ShopId = "untap"
        base_url = "https://example.com/untap"
        supports_login = True

        async def search(self, query: SearchQuery):  # pragma: no cover — unused
            return []

    svc = _service(StubAdapter("tolarie", offers=[]), _LoginOnly())
    rows = {row["shop"]: row for row in svc.shop_account_capabilities()}
    assert rows["tolarie"]["supports_login"] is False
    assert rows["tolarie"]["supports_cart"] is False
    assert rows["untap"]["supports_login"] is True
    assert rows["untap"]["supports_cart"] is False
