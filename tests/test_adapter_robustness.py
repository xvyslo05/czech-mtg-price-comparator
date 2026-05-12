"""Adapters must not crash on empty / malformed input — they should return []."""
from __future__ import annotations

import json

import pytest

from cz_mtg_compare.adapters.blacklotus import BlackLotusAdapter
from cz_mtg_compare.adapters.cernyrytir import CernyRytirAdapter
from cz_mtg_compare.adapters.najada import NajadaAdapter
from cz_mtg_compare.adapters.rishada import RishadaAdapter
from cz_mtg_compare.adapters.tolarie import TolarieAdapter
from cz_mtg_compare.adapters.untap import UntapAdapter
from cz_mtg_compare.models import SearchQuery


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter_cls",
    [TolarieAdapter, BlackLotusAdapter, CernyRytirAdapter, RishadaAdapter, UntapAdapter],
)
async def test_html_adapter_returns_empty_for_empty_html(adapter_cls):
    adapter = adapter_cls()
    offers = await adapter.parse("", SearchQuery(name="Lightning Bolt"))
    assert offers == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter_cls",
    [TolarieAdapter, BlackLotusAdapter, CernyRytirAdapter, RishadaAdapter, UntapAdapter],
)
async def test_html_adapter_handles_garbage_input(adapter_cls):
    adapter = adapter_cls()
    offers = await adapter.parse("<html><body>nothing useful</body></html>",
                                 SearchQuery(name="Lightning Bolt"))
    assert offers == []


@pytest.mark.asyncio
async def test_najada_adapter_handles_empty_payload():
    adapter = NajadaAdapter()
    offers = await adapter.parse(json.dumps({"results": []}),
                                 SearchQuery(name="Lightning Bolt"))
    assert offers == []


@pytest.mark.asyncio
async def test_najada_adapter_handles_missing_results_key():
    adapter = NajadaAdapter()
    offers = await adapter.parse(json.dumps({}),
                                 SearchQuery(name="Lightning Bolt"))
    assert offers == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["", "   ", "not json {{{", '"not a dict"', "[]", "null"])
async def test_najada_adapter_handles_empty_or_malformed_body(body):
    """Empty / non-JSON / non-dict root must yield [] instead of raising."""
    adapter = NajadaAdapter()
    offers = await adapter.parse(body, SearchQuery(name="Lightning Bolt"))
    assert offers == []


@pytest.mark.asyncio
async def test_najada_adapter_skips_articles_without_price():
    adapter = NajadaAdapter()
    payload = {
        "results": [
            {
                "name": "Lightning Bolt",
                "expansion": {"localized_name": "M10", "short_code": "M10"},
                "articles": [
                    {"condition": "NM", "language_code": "EN"},  # no price fields
                    {
                        "condition": "NM",
                        "language_code": "EN",
                        "effective_price_czk": 50,
                        "total_availability": 1,
                        "additional_properties": {"is_foil": False},
                    },
                ],
            }
        ]
    }
    offers = await adapter.parse(json.dumps(payload),
                                 SearchQuery(name="Lightning Bolt", in_stock_only=False))
    assert len(offers) == 1
    assert offers[0].price_czk == 50


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "Krenko, Mob Boss",
        "Inventors' Fair",
        "Siege-Gang Commander",
        "Emeritus of Conflict // Lightning Bolt",
        "Sting, the Glinting Dagger",
    ],
)
async def test_tolarie_adapter_handles_special_characters_in_name(name):
    """Punctuation and split-card '//' must not break the search URL or filter."""
    adapter = TolarieAdapter()
    url = adapter._search_url(SearchQuery(name=name))  # noqa: SLF001
    # URL-encoded form should not contain raw spaces or '//' issues.
    assert " " not in url
    assert "%20" in url or "+" in url


def test_cernyrytir_split_name_suffix_handles_stacked_modifiers():
    name, foil, cond = CernyRytirAdapter._split_name_suffix("Lightning Bolt - foil - lightly played")
    assert name == "Lightning Bolt"
    assert foil is True
    from cz_mtg_compare.models import Condition
    assert cond == Condition.LP


def test_cernyrytir_split_name_suffix_keeps_unknown_token():
    """Unrecognized suffixes should be folded back into the name, not lost."""
    name, foil, cond = CernyRytirAdapter._split_name_suffix("Lightning Bolt - promo")
    assert "promo" in name.lower()
    assert foil is False
    from cz_mtg_compare.models import Condition
    assert cond is Condition.UNKNOWN


@pytest.mark.asyncio
async def test_cernyrytir_in_stock_filter_drops_sentinel_9999_price(load_fixture):
    """The shop quotes 9999 Kč with 0 ks for items it could special-order. Those
    are noise on a "what's available now" query and must be filtered out when
    in_stock_only=True."""
    html = load_fixture("cernyrytir_lightning_bolt.html")
    adapter = CernyRytirAdapter()
    in_stock = await adapter.parse(html, SearchQuery(name="Lightning Bolt", in_stock_only=True))
    assert all(o.price_czk < 9999 for o in in_stock)
    assert all(o.stock_qty > 0 for o in in_stock)
