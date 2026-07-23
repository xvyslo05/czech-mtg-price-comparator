"""Service-level tests for the cart/watchlist tools: when a shop adapter
keeps an implementation around but its capability flag is False (e.g. untap,
whose cart works against Prestashop but doesn't persist across logins), the
service layer must refuse the call with ``AccountFeatureNotSupported`` rather
than dispatching to the live adapter. The MCP server (and any future
transport) inherits this behaviour by delegating to the service."""

from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.base import AccountFeatureNotSupported
from cz_mtg_compare.adapters.untap import UntapAdapter
from cz_mtg_compare.server import (
    add_to_cart,
    clear_cart,
    shop_account_capabilities,
    view_cart,
)
from cz_mtg_compare.service import default_service, require_capability

NEW_READ_ONLY_SHOPS = [
    "axionnow",
    "mtgspot",
    "magiccorporation",
    "jkentertainment",
    "bazaarofmagic",
    "spellenwinkel",
]


def test_untap_cart_capability_is_false() -> None:
    """Sanity check: untap's adapter ships with cart code but flags it off."""
    untap = default_service.aggregator.get_adapter("untap")
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


@pytest.mark.parametrize("shop", NEW_READ_ONLY_SHOPS)
def test_new_shop_account_capabilities_are_read_only(shop: str) -> None:
    rows = {row["shop"]: row for row in shop_account_capabilities()}
    assert rows[shop]["supports_login"] is False
    assert rows[shop]["supports_cart"] is False
    assert rows[shop]["supports_watchlist"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("shop", NEW_READ_ONLY_SHOPS)
async def test_mcp_add_to_cart_refuses_new_read_only_shops(shop: str) -> None:
    with pytest.raises(AccountFeatureNotSupported):
        await add_to_cart(shop=shop, shop_ref="fixture-ref", count=1)


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
    """``require_capability`` reads the supports_<feature> flag, not whether
    the adapter overrides the method. UntapAdapter overrides add_to_cart
    (its implementation works); the helper must still refuse."""
    adapter = UntapAdapter()
    assert adapter.supports_cart is False
    with pytest.raises(AccountFeatureNotSupported) as exc:
        require_capability(adapter, "cart")
    assert exc.value.feature == "cart"

    # Login is supported — must NOT raise.
    require_capability(adapter, "login")
