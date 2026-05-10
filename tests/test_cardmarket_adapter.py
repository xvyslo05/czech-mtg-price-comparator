from __future__ import annotations

import pytest

from cz_mtg_compare.adapters.cardmarket import (
    CardmarketAdapter,
    MkmCredentials,
    build_oauth1_authorization_header,
)
from cz_mtg_compare.models import SearchQuery


@pytest.fixture
def fake_creds() -> MkmCredentials:
    return MkmCredentials(
        app_token="app",
        app_secret="appsecret",
        access_token="access",
        access_token_secret="accesssecret",
    )


@pytest.fixture
def adapter(fake_creds: MkmCredentials) -> CardmarketAdapter:
    return CardmarketAdapter(credentials=fake_creds, eur_to_czk=25.0)


def test_oauth1_header_is_deterministic_with_fixed_nonce(fake_creds: MkmCredentials):
    header = build_oauth1_authorization_header(
        "GET",
        "https://api.cardmarket.com/ws/v2.0/output.json/products/find?search=Lightning+Bolt&idGame=1",
        fake_creds,
        nonce="abc123",
        timestamp="1700000000",
    )
    assert header.startswith("OAuth ")
    # Same inputs must produce same signature.
    again = build_oauth1_authorization_header(
        "GET",
        "https://api.cardmarket.com/ws/v2.0/output.json/products/find?search=Lightning+Bolt&idGame=1",
        fake_creds,
        nonce="abc123",
        timestamp="1700000000",
    )
    assert header == again
    # Required fields present.
    for needle in ('oauth_consumer_key="app"', 'oauth_token="access"',
                   'oauth_signature_method="HMAC-SHA1"', 'oauth_signature='):
        assert needle in header


def test_oauth1_header_changes_when_url_changes(fake_creds: MkmCredentials):
    h1 = build_oauth1_authorization_header(
        "GET",
        "https://api.cardmarket.com/ws/v2.0/output.json/products/find?search=A",
        fake_creds, nonce="n", timestamp="t",
    )
    h2 = build_oauth1_authorization_header(
        "GET",
        "https://api.cardmarket.com/ws/v2.0/output.json/products/find?search=B",
        fake_creds, nonce="n", timestamp="t",
    )
    assert h1 != h2


def test_adapter_skipped_silently_when_no_credentials():
    adapter = CardmarketAdapter(credentials=None)
    assert adapter.configured is False


@pytest.mark.asyncio
async def test_adapter_returns_empty_when_unconfigured():
    adapter = CardmarketAdapter(credentials=None)
    offers = await adapter.search(SearchQuery(name="Lightning Bolt"))
    assert offers == []


def test_credentials_from_env_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch):
    for key in ("MKM_APP_TOKEN", "MKM_APP_SECRET", "MKM_ACCESS_TOKEN", "MKM_ACCESS_TOKEN_SECRET"):
        monkeypatch.delenv(key, raising=False)
    assert MkmCredentials.from_env() is None


def test_credentials_from_env_loads_when_present(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MKM_APP_TOKEN", "a")
    monkeypatch.setenv("MKM_APP_SECRET", "b")
    monkeypatch.setenv("MKM_ACCESS_TOKEN", "c")
    monkeypatch.setenv("MKM_ACCESS_TOKEN_SECRET", "d")
    creds = MkmCredentials.from_env()
    assert creds is not None
    assert creds.app_token == "a"
    assert creds.access_token_secret == "d"


def test_parse_find_payload_emits_offers(adapter: CardmarketAdapter):
    payload = {
        "product": [
            {
                "name": "Lightning Bolt",
                "enName": "Lightning Bolt",
                "expansionName": "Magic 2010",
                "expansion": {"abbreviation": "M10"},
                "priceGuide": {
                    "LOW": 0.10,
                    "AVG": 0.30,
                    "TREND": 0.25,
                    "TRENDFOIL": 4.50,
                },
                "website": "https://www.cardmarket.com/en/Magic/Products/Singles/Magic-2010/Lightning-Bolt",
            }
        ]
    }
    offers = adapter._parse_find_payload(payload, SearchQuery(name="Lightning Bolt"))  # noqa: SLF001
    # Expect non-foil + foil offer.
    assert len(offers) == 2
    non_foil = next(o for o in offers if not o.foil)
    foil = next(o for o in offers if o.foil)
    # 0.25 EUR * 25.0 CZK/EUR = 6.25, rounded to 6
    assert non_foil.price_czk == 6
    assert non_foil.set_code == "M10"
    assert non_foil.edition == "Magic 2010"
    # 4.50 EUR * 25.0 = 112.5 -> 113 (round half-to-even rounds 112)
    assert foil.price_czk in (112, 113)
    assert all(o.shop == "cardmarket" for o in offers)


def test_parse_find_payload_filters_by_name(adapter: CardmarketAdapter):
    payload = {
        "product": [
            {"name": "Lightning Bolt", "priceGuide": {"TREND": 0.5}, "expansionName": "M10"},
            {"name": "Lightning Strike", "priceGuide": {"TREND": 0.3}, "expansionName": "M19"},
        ]
    }
    offers = adapter._parse_find_payload(payload, SearchQuery(name="Lightning Bolt"))  # noqa: SLF001
    assert all("lightning bolt" in o.card_name.lower() for o in offers)
    assert all(o.card_name == "Lightning Bolt" for o in offers)


def test_parse_find_payload_skips_when_no_price(adapter: CardmarketAdapter):
    payload = {"product": [{"name": "Lightning Bolt", "priceGuide": {}}]}
    offers = adapter._parse_find_payload(payload, SearchQuery(name="Lightning Bolt"))  # noqa: SLF001
    assert offers == []
