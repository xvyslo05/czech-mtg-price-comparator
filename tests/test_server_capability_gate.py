"""Server-level tests for the MCP cart/watchlist tools: when a shop adapter
keeps an implementation around but its capability flag is False (e.g. untap,
whose cart works against Prestashop but doesn't persist across logins), the
MCP-layer wrappers in ``server.py`` must refuse the call with
``AccountFeatureNotSupported`` rather than dispatching to the live adapter."""

from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.base import AccountFeatureNotSupported
from cz_mtg_compare.adapters.untap import UntapAdapter
from cz_mtg_compare.server import (
    _aggregator,
    _require_capability,
    add_to_cart,
    clear_cart,
    shop_account_capabilities,
    view_cart,
)


def test_untap_cart_capability_is_false() -> None:
    """Sanity check: untap's adapter ships with cart code but flags it off."""
    untap = _aggregator.get_adapter("untap")
    assert untap is not None
    assert untap.supports_login is True
    assert untap.supports_cart is False
    # The adapter methods themselves still exist — we're disabling at the
    # capability layer, not by removing the implementation.
    assert hasattr(untap, "add_to_cart")
    assert hasattr(untap, "view_cart")
    assert hasattr(untap, "clear_cart")


def test_shop_account_capabilities_reports_untap_cart_off() -> None:
    rows = {row["shop"]: row for row in shop_account_capabilities()}
    assert rows["untap"]["supports_login"] is True
    assert rows["untap"]["supports_cart"] is False


@pytest.mark.asyncio
async def test_mcp_add_to_cart_refuses_untap() -> None:
    """The MCP wrapper must short-circuit on the capability flag — never
    reach UntapAdapter.add_to_cart, never produce a misleading success."""
    with pytest.raises(AccountFeatureNotSupported) as exc:
        await add_to_cart(shop="untap", shop_ref="21061", count=1)
    assert exc.value.shop_id == "untap"
    assert exc.value.feature == "cart"


@pytest.mark.asyncio
async def test_mcp_view_cart_refuses_untap() -> None:
    with pytest.raises(AccountFeatureNotSupported):
        await view_cart(shop="untap")


@pytest.mark.asyncio
async def test_mcp_clear_cart_refuses_untap() -> None:
    with pytest.raises(AccountFeatureNotSupported):
        await clear_cart(shop="untap")


def test_require_capability_helper_uses_flag_not_method_presence() -> None:
    """``_require_capability`` reads the supports_<feature> flag, not whether
    the adapter overrides the method. UntapAdapter overrides add_to_cart
    (its implementation works); the helper must still refuse."""
    adapter = UntapAdapter()
    assert adapter.supports_cart is False
    with pytest.raises(AccountFeatureNotSupported) as exc:
        _require_capability(adapter, "cart")
    assert exc.value.feature == "cart"

    # Login is supported — must NOT raise.
    _require_capability(adapter, "login")
