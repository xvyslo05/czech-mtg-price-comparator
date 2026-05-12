"""HTTP-level adapter tests: cover the .search() fetch+parse path with mocked
responses (200 happy path, empty body, 5xx, timeout). These complement the
per-shop `.parse(html)` fixture tests, which only exercise parsing in isolation.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx

from cz_mtg_compare.adapters.base import ShopAdapter
from cz_mtg_compare.adapters.blacklotus import BlackLotusAdapter
from cz_mtg_compare.adapters.cernyrytir import CernyRytirAdapter
from cz_mtg_compare.adapters.najada import NajadaAdapter
from cz_mtg_compare.adapters.rishada import RishadaAdapter
from cz_mtg_compare.adapters.tolarie import TolarieAdapter
from cz_mtg_compare.adapters.untap import UntapAdapter
from cz_mtg_compare.models import SearchQuery


# One entry per adapter: factory, fixture filename, host-matching regex, method,
# wire encoding. `factory` returns a fresh adapter — blacklotus needs
# `enrich_detail=False` so we don't also have to mock every detail-page URL.
# Cernyrytir ships windows-1250 bytes and the adapter pins that encoding before
# decoding `resp.text`; serving the fixture as UTF-8 would produce mojibake and
# the CSS selectors wouldn't match.
_ADAPTERS: list[tuple[str, Callable[[], ShopAdapter], str, re.Pattern[str], str, str]] = [
    ("tolarie", lambda: TolarieAdapter(), "tolarie_lightning_bolt.html",
     re.compile(r"https://www\.tolarie\.cz/.*"), "GET", "utf-8"),
    ("blacklotus", lambda: BlackLotusAdapter(enrich_detail=False),
     "blacklotus_lightning_bolt.html",
     re.compile(r"https://www\.blacklotus\.cz/.*"), "GET", "utf-8"),
    ("cernyrytir", lambda: CernyRytirAdapter(), "cernyrytir_lightning_bolt.html",
     re.compile(r"https://www\.cernyrytir\.cz/.*"), "POST", "windows-1250"),
    ("rishada", lambda: RishadaAdapter(), "rishada_lightning_bolt.html",
     re.compile(r"https://www\.rishada\.cz/.*"), "GET", "utf-8"),
    ("untap", lambda: UntapAdapter(), "untap_lightning_bolt.html",
     re.compile(r"https://untap\.cz/.*"), "GET", "utf-8"),
    ("najada", lambda: NajadaAdapter(), "najada_lightning_bolt.json",
     re.compile(r"https://wizardshop\.cz/.*"), "GET", "utf-8"),
]


def _route(mock: respx.MockRouter, method: str, url_pattern: re.Pattern[str]):
    if method == "GET":
        return mock.get(url__regex=url_pattern.pattern)
    if method == "POST":
        return mock.post(url__regex=url_pattern.pattern)
    raise AssertionError(f"unsupported method {method}")


def _load_fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def _encode_body(text: str, encoding: str) -> bytes:
    """Encode fixture text using the shop's wire encoding. Some characters in
    the UTF-8 fixture may not be representable in windows-1250; replace them
    rather than fail — the CSS structure the parser depends on is ASCII."""
    return text.encode(encoding, errors="replace")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shop_id, factory, fixture_name, url_pattern, method, encoding",
    _ADAPTERS,
    ids=[a[0] for a in _ADAPTERS],
)
async def test_search_returns_offers_on_200(shop_id, factory, fixture_name, url_pattern, method, encoding):
    """Wire 200 + saved fixture body and confirm `.search()` parses to a
    non-empty offer list (same shape as the .parse() fixture tests)."""
    body = _load_fixture(fixture_name)
    adapter = factory()

    async with respx.mock(assert_all_called=False) as mock:
        _route(mock, method, url_pattern).mock(
            return_value=httpx.Response(200, content=_encode_body(body, encoding))
        )
        offers = await adapter.search(SearchQuery(name="Lightning Bolt", in_stock_only=False))

    assert offers, f"{shop_id} returned no offers on 200 + fixture body"
    assert all(o.shop == shop_id for o in offers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shop_id, factory, _fixture, url_pattern, method, _encoding",
    _ADAPTERS,
    ids=[a[0] for a in _ADAPTERS],
)
async def test_search_returns_empty_on_empty_body(shop_id, factory, _fixture, url_pattern, method, _encoding):
    """200 + empty body must not crash; adapter returns []."""
    adapter = factory()

    async with respx.mock(assert_all_called=False) as mock:
        _route(mock, method, url_pattern).mock(return_value=httpx.Response(200, text=""))
        offers = await adapter.search(SearchQuery(name="Lightning Bolt"))

    assert offers == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shop_id, factory, _fixture, url_pattern, method, _encoding",
    _ADAPTERS,
    ids=[a[0] for a in _ADAPTERS],
)
async def test_search_raises_on_5xx(shop_id, factory, _fixture, url_pattern, method, _encoding):
    """A 5xx response must raise an HTTPStatusError so the aggregator can mark
    the shop as failed (and not silently swallow the outage)."""
    adapter = factory()

    async with respx.mock(assert_all_called=False) as mock:
        _route(mock, method, url_pattern).mock(return_value=httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.search(SearchQuery(name="Lightning Bolt"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shop_id, factory, _fixture, url_pattern, method, _encoding",
    _ADAPTERS,
    ids=[a[0] for a in _ADAPTERS],
)
async def test_search_raises_on_timeout(shop_id, factory, _fixture, url_pattern, method, _encoding):
    """A request-side timeout must surface as httpx.TimeoutException so the
    aggregator's per-shop timeout / failure handling can see it."""
    adapter = factory()

    async with respx.mock(assert_all_called=False) as mock:
        _route(mock, method, url_pattern).mock(side_effect=httpx.ConnectTimeout("simulated"))
        with pytest.raises(httpx.TimeoutException):
            await adapter.search(SearchQuery(name="Lightning Bolt"))


@pytest.mark.asyncio
async def test_najada_search_returns_empty_on_malformed_json():
    """If the upstream API returns 200 with non-JSON content (e.g. an HTML
    error page slipped through with a 200 status), the adapter should fall
    back to an empty result rather than crashing the whole aggregator query."""
    adapter = NajadaAdapter()

    async with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"https://wizardshop\.cz/.*").mock(
            return_value=httpx.Response(200, text="not json {{{")
        )
        offers = await adapter.search(SearchQuery(name="Lightning Bolt"))

    assert offers == []
