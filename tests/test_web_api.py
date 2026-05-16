"""HTTP-surface tests for the FastAPI app.

Uses an in-memory ``CardCompareService`` wired to ``StubAdapter`` instances so
the suite stays offline. The point is to verify wiring (route → service →
serialization) and the error handlers, not to re-test the engine.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cz_mtg_compare.adapters.base import AccountFeatureNotSupported
from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.optimizer import DecklistOptimizer
from cz_mtg_compare.scryfall import CardInfo
from cz_mtg_compare.service import CardCompareService
from cz_mtg_compare.web.app import app as default_app
from cz_mtg_compare.web.app import create_app

from ._factories import StubAdapter, make_offer


class _FakeScryfall:
    """Duck-typed stand-in for ScryfallClient. The service only calls
    ``.resolve``; using a fake here keeps the suite offline."""

    def __init__(self, table: dict[str, CardInfo | None]) -> None:
        self._table = {k.lower(): v for k, v in table.items()}
        self.calls: list[tuple[str, bool]] = []

    async def resolve(self, name: str, *, exact: bool = False) -> CardInfo | None:
        self.calls.append((name, exact))
        return self._table.get(name.lower())


def _service(
    *adapters: StubAdapter, scryfall: _FakeScryfall | None = None
) -> CardCompareService:
    agg = Aggregator(adapters=list(adapters))
    opt = DecklistOptimizer(agg)
    return CardCompareService(aggregator=agg, optimizer=opt, scryfall=scryfall)


@pytest.fixture
def client_factory():
    """Yields a function (svc) -> TestClient so each test owns its wiring."""

    def _make(svc: CardCompareService) -> TestClient:
        return TestClient(create_app(svc))

    return _make


def test_health(client_factory):
    client = client_factory(_service(StubAdapter("tolarie", offers=[])))
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_list_shops(client_factory):
    svc = _service(StubAdapter("tolarie", offers=[]), StubAdapter("najada", offers=[]))
    client = client_factory(svc)
    r = client.get("/v1/shops")
    assert r.status_code == 200
    rows = r.json()
    assert {row["shop"] for row in rows} == {"tolarie", "najada"}
    for row in rows:
        assert row["ok"] is True


def test_search_card_returns_offers_sorted_by_price(client_factory):
    tol = StubAdapter("tolarie", offers=[make_offer(shop="tolarie", price=80)])
    naj = StubAdapter("najada", offers=[make_offer(shop="najada", price=35)])
    client = client_factory(_service(tol, naj))

    r = client.get("/v1/cards/search", params={"name": "Lightning Bolt"})
    assert r.status_code == 200
    payload = r.json()
    assert [o["shop"] for o in payload] == ["najada", "tolarie"]
    assert payload[0]["price_czk"] == 35
    assert payload[1]["price_czk"] == 80


def test_search_card_shop_filter_via_repeated_query(client_factory):
    tol = StubAdapter("tolarie", offers=[make_offer(shop="tolarie", price=80)])
    naj = StubAdapter("najada", offers=[make_offer(shop="najada", price=35)])
    client = client_factory(_service(tol, naj))

    r = client.get(
        "/v1/cards/search",
        params=[("name", "Lightning Bolt"), ("shops", "najada")],
    )
    assert r.status_code == 200
    payload = r.json()
    assert [o["shop"] for o in payload] == ["najada"]


def test_search_card_rejects_empty_name(client_factory):
    client = client_factory(_service(StubAdapter("tolarie", offers=[])))
    r = client.get("/v1/cards/search", params={"name": ""})
    assert r.status_code == 422


def test_optimize_decklist_happy_path(client_factory):
    tol = StubAdapter(
        "tolarie",
        table={"Lightning Bolt": [make_offer(shop="tolarie", price=35, stock_qty=4)]},
    )
    client = client_factory(_service(tol))
    r = client.post(
        "/v1/decklists/optimize",
        json={"decklist": "4 Lightning Bolt"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["strategy"] == "cheapest"
    assert body["cheapest_split_total_czk"] == 140
    assert len(body["picks"]) == 1
    assert body["picks"][0]["chosen"]["shop"] == "tolarie"


def test_optimize_decklist_rejects_empty_body(client_factory):
    client = client_factory(_service(StubAdapter("tolarie", offers=[])))
    r = client.post("/v1/decklists/optimize", json={"decklist": ""})
    assert r.status_code == 422


def test_shop_capabilities_lists_flags(client_factory):
    client = client_factory(_service(StubAdapter("tolarie", offers=[])))
    r = client.get("/v1/shops/capabilities")
    assert r.status_code == 200
    rows = {row["shop"]: row for row in r.json()}
    assert rows["tolarie"]["supports_login"] is False  # StubAdapter default


def test_value_error_becomes_400(client_factory):
    """The service raises ValueError for unknown shops in fan-out via the
    shop allow-list when the id isn't recognized. We exercise that path
    indirectly: an unknown shop in ``shops`` results in zero adapters
    matching and an empty result — *not* an error. The 400 handler is
    still wired and covered by the capability_handler test below; this
    test guards against a future regression where someone wires a raw
    ValueError into a handler.
    """
    svc = _service(StubAdapter("tolarie", offers=[]))
    app = create_app(svc)

    @app.get("/_test/value-error")
    def _raise() -> None:
        raise ValueError("boom")

    client = TestClient(app)
    r = client.get("/_test/value-error")
    assert r.status_code == 400
    assert r.json() == {"detail": "boom"}


def test_capability_error_becomes_409(client_factory):
    svc = _service(StubAdapter("tolarie", offers=[]))
    app = create_app(svc)

    @app.get("/_test/capability-error")
    def _raise() -> None:
        raise AccountFeatureNotSupported("tolarie", "cart")

    client = TestClient(app)
    r = client.get("/_test/capability-error")
    assert r.status_code == 409
    body = r.json()
    assert body["shop"] == "tolarie"
    assert body["feature"] == "cart"


def test_search_card_exclude_shops_applied_after_allow_list(client_factory):
    tol = StubAdapter("tolarie", offers=[make_offer(shop="tolarie", price=80)])
    naj = StubAdapter("najada", offers=[make_offer(shop="najada", price=35)])
    bla = StubAdapter("blacklotus", offers=[make_offer(shop="blacklotus", price=50)])
    client = client_factory(_service(tol, naj, bla))

    r = client.get(
        "/v1/cards/search",
        params=[
            ("name", "Lightning Bolt"),
            ("shops", "tolarie"),
            ("shops", "najada"),
            ("exclude_shops", "najada"),
        ],
    )
    assert r.status_code == 200
    assert [o["shop"] for o in r.json()] == ["tolarie"]


def test_lookup_card_returns_scryfall_data(client_factory):
    info = CardInfo(
        name="Lightning Bolt",
        set_code="LEA",
        set_name="Limited Edition Alpha",
        oracle_text="Lightning Bolt deals 3 damage to any target.",
    )
    fake = _FakeScryfall({"Lightning Bolt": info})
    client = client_factory(_service(StubAdapter("tolarie", offers=[]), scryfall=fake))

    r = client.get("/v1/cards/lookup", params={"name": "Lightning Bolt"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Lightning Bolt"
    assert body["set_code"] == "LEA"
    # Fuzzy by default — verify the flag forwards.
    assert fake.calls == [("Lightning Bolt", False)]


def test_lookup_card_forwards_exact_flag(client_factory):
    fake = _FakeScryfall({})
    client = client_factory(_service(StubAdapter("tolarie", offers=[]), scryfall=fake))
    r = client.get("/v1/cards/lookup", params={"name": "Nope", "exact": "true"})
    assert r.status_code == 200
    assert r.json() is None
    assert fake.calls == [("Nope", True)]


def test_lookup_card_rejects_empty_name(client_factory):
    fake = _FakeScryfall({})
    client = client_factory(_service(StubAdapter("tolarie", offers=[]), scryfall=fake))
    r = client.get("/v1/cards/lookup", params={"name": ""})
    assert r.status_code == 422


def test_optimize_decklist_fewest_shops_strategy(client_factory):
    """Strategy passes through to the optimizer. With two shops covering
    the same card, fewest_shops should pick one of them in the picks."""
    tol = StubAdapter(
        "tolarie",
        table={"Lightning Bolt": [make_offer(shop="tolarie", price=40, stock_qty=4)]},
    )
    naj = StubAdapter(
        "najada",
        table={"Lightning Bolt": [make_offer(shop="najada", price=35, stock_qty=4)]},
    )
    client = client_factory(_service(tol, naj))

    r = client.post(
        "/v1/decklists/optimize",
        json={"decklist": "4 Lightning Bolt", "strategy": "fewest_shops"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["strategy"] == "fewest_shops"
    assert body["consolidated_total_czk"] is not None
    # fewest_shops with one card: still exactly one shop in the plan.
    assert len(body["shopping_plan"]) == 1


def test_optimize_decklist_shops_filter_in_body(client_factory):
    """``shops`` in the request body restricts the per-shop bundles and picks
    to the listed shops — proves the JSON body's allow-list reaches the
    optimizer (not just the search_card query path)."""
    tol = StubAdapter(
        "tolarie",
        table={"Lightning Bolt": [make_offer(shop="tolarie", price=40, stock_qty=4)]},
    )
    naj = StubAdapter(
        "najada",
        table={"Lightning Bolt": [make_offer(shop="najada", price=35, stock_qty=4)]},
    )
    client = client_factory(_service(tol, naj))

    r = client.post(
        "/v1/decklists/optimize",
        json={"decklist": "4 Lightning Bolt", "shops": ["tolarie"]},
    )
    assert r.status_code == 200
    body = r.json()
    bundles = {b["shop"] for b in body["per_shop_bundles"]}
    assert bundles == {"tolarie"}
    assert body["picks"][0]["chosen"]["shop"] == "tolarie"


def test_default_app_uses_default_service():
    """``app = create_app()`` at the module bottom must wire the real
    default_service — otherwise ``cz-mtg-compare-web`` boots into an
    empty/broken state. We don't want to hit the network so we just check
    the health probe + that /v1/shops returns the real adapter set
    (non-empty)."""
    client = TestClient(default_app)
    assert client.get("/v1/health").json() == {"status": "ok"}
    r = client.get("/v1/shops")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_openapi_schema_lists_all_v1_routes():
    """A failed import or a missing decorator silently drops a route from
    OpenAPI but not from the test client. Pin every advertised route so a
    regression surfaces immediately."""
    client = TestClient(default_app)
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"].keys())
    assert {
        "/v1/health",
        "/v1/shops",
        "/v1/shops/capabilities",
        "/v1/cards/search",
        "/v1/cards/lookup",
        "/v1/decklists/optimize",
    }.issubset(paths)
