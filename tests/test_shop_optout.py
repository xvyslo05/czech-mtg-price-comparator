"""Opt-out paths: per-call exclude_shops on tools + CZ_MTG_DISABLED_SHOPS env var."""
from __future__ import annotations

import pytest

from cz_mtg_compare.adapters import build_default_adapters
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import SearchQuery
from cz_mtg_compare.optimizer import DecklistOptimizer

from ._factories import StubAdapter as _StubAdapter
from ._factories import make_offer as _o

UNCONDITIONAL_SHOPS = {
    "tolarie",
    "najada",
    "blacklotus",
    "cernyrytir",
    "rishada",
    "untap",
    "axionnow",
    "mtgspot",
    "magiccorporation",
    "jkentertainment",
    "bazaarofmagic",
    "spellenwinkel",
}


# ---------------------------------------------------------------------------
# Per-call opt-out via Aggregator.search(exclude_shops=...)


@pytest.mark.asyncio
async def test_aggregator_exclude_shops_filters_named_adapters():
    agg = Aggregator(
        [
            _StubAdapter("tolarie", [_o("tolarie", "X", 50)]),
            _StubAdapter("najada", [_o("najada", "X", 30)]),
            _StubAdapter("blacklotus", [_o("blacklotus", "X", 70)]),
        ]
    )
    offers = await agg.search(SearchQuery(name="X"), exclude_shops=["najada"])
    shops = {o.shop for o in offers}
    assert shops == {"tolarie", "blacklotus"}


@pytest.mark.asyncio
async def test_aggregator_exclude_shops_combined_with_allow_list():
    """When both `shops` (allow) and `exclude_shops` (deny) are given, the
    deny list is applied AFTER the allow list — so explicit deny wins."""
    agg = Aggregator(
        [
            _StubAdapter("tolarie", [_o("tolarie", "X", 50)]),
            _StubAdapter("najada", [_o("najada", "X", 30)]),
            _StubAdapter("blacklotus", [_o("blacklotus", "X", 70)]),
        ]
    )
    offers = await agg.search(
        SearchQuery(name="X"),
        shops=["tolarie", "najada", "blacklotus"],
        exclude_shops=["najada"],
    )
    shops = {o.shop for o in offers}
    assert shops == {"tolarie", "blacklotus"}


@pytest.mark.asyncio
async def test_aggregator_exclude_shops_empty_list_is_a_noop():
    agg = Aggregator([_StubAdapter("tolarie", [_o("tolarie", "X", 50)])])
    offers = await agg.search(SearchQuery(name="X"), exclude_shops=[])
    assert len(offers) == 1


# ---------------------------------------------------------------------------
# Per-call opt-out via DecklistOptimizer.optimize


@pytest.mark.asyncio
async def test_optimizer_exclude_shops_drops_picks_and_bundles():
    agg = Aggregator(
        [
            _StubAdapter(
                "tolarie",
                [_o("tolarie", "Lightning Bolt", 50), _o("tolarie", "Counterspell", 60)],
            ),
            _StubAdapter(
                "najada",
                [_o("najada", "Lightning Bolt", 30), _o("najada", "Counterspell", 80)],
            ),
        ]
    )
    optimizer = DecklistOptimizer(agg)
    result = await optimizer.optimize(
        "1 Lightning Bolt\n1 Counterspell\n",
        exclude_shops=["najada"],
    )
    # No offer chosen from najada anywhere.
    chosen_shops = {p.chosen.shop for p in result.picks if p.chosen}
    assert "najada" not in chosen_shops
    # Bundles only show non-excluded shops.
    bundle_shops = {b.shop for b in result.per_shop_bundles}
    assert bundle_shops == {"tolarie"}


@pytest.mark.asyncio
async def test_optimizer_exclude_shops_does_not_change_total_when_excluded_shop_was_not_winning():
    agg = Aggregator(
        [
            _StubAdapter("tolarie", [_o("tolarie", "Card", 50)]),
            _StubAdapter("najada", [_o("najada", "Card", 9999)]),  # never wins
        ]
    )
    optimizer = DecklistOptimizer(agg)
    full = await optimizer.optimize("1 Card\n")
    excluded = await optimizer.optimize("1 Card\n", exclude_shops=["najada"])
    assert full.cheapest_split_total_czk == excluded.cheapest_split_total_czk == 50


# ---------------------------------------------------------------------------
# Server-wide opt-out via CZ_MTG_DISABLED_SHOPS env var


def test_build_default_adapters_drops_disabled_via_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "CZ_MTG_DISABLED_SHOPS",
        "blacklotus,untap,axionnow,spellenwinkel",
    )
    adapters = build_default_adapters()
    shops = {a.shop_id for a in adapters}
    assert "blacklotus" not in shops
    assert "untap" not in shops
    assert "axionnow" not in shops
    assert "spellenwinkel" not in shops
    # Other shops still present.
    assert "tolarie" in shops
    assert "najada" in shops


def test_disabled_shops_env_is_case_insensitive(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "CZ_MTG_DISABLED_SHOPS",
        "BlackLotus, UNTAP, MagicCorporation",
    )
    adapters = build_default_adapters()
    shops = {a.shop_id for a in adapters}
    assert "blacklotus" not in shops
    assert "untap" not in shops
    assert "magiccorporation" not in shops


def test_disabled_shops_env_unset_keeps_all(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CZ_MTG_DISABLED_SHOPS", raising=False)
    adapters = build_default_adapters()
    shops = {a.shop_id for a in adapters}
    # Cardmarket only present if creds are also set; ignore for this check.
    assert UNCONDITIONAL_SHOPS.issubset(shops)


def test_disabled_shops_env_empty_string_keeps_all(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CZ_MTG_DISABLED_SHOPS", "")
    adapters = build_default_adapters()
    shops = {a.shop_id for a in adapters}
    assert UNCONDITIONAL_SHOPS.issubset(shops)


def test_disabled_shops_env_unknown_name_does_not_break(monkeypatch: pytest.MonkeyPatch):
    """A typoed or stale shop name in the env var is silently ignored — we
    don't want a typo to brick the server."""
    monkeypatch.setenv("CZ_MTG_DISABLED_SHOPS", "doesnotexist,tolarie")
    adapters = build_default_adapters()
    shops = {a.shop_id for a in adapters}
    assert "tolarie" not in shops
    # Other shops still present.
    assert "najada" in shops
