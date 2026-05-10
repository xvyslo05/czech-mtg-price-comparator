from __future__ import annotations

import json
from pathlib import Path

import pytest

from cz_mtg_compare.scryfall import ScryfallClient


SAMPLE_PAYLOAD = {
    "object": "card",
    "id": "abc",
    "oracle_id": "oracle-xyz",
    "name": "Lightning Bolt",
    "lang": "en",
    "set": "lea",
    "set_name": "Limited Edition Alpha",
    "collector_number": "161",
    "rarity": "common",
    "mana_cost": "{R}",
    "type_line": "Instant",
    "oracle_text": "Lightning Bolt deals 3 damage to any target.",
    "image_uris": {
        "small": "https://example.com/s.jpg",
        "normal": "https://example.com/n.jpg",
        "large": "https://example.com/l.jpg",
    },
    "scryfall_uri": "https://scryfall.com/card/lea/161",
}


@pytest.mark.asyncio
async def test_resolve_uses_cache_when_present(tmp_path: Path):
    client = ScryfallClient(cache_dir=tmp_path)
    cache_path = client._cache_path("fuzzy", "Lightning Bolt")  # noqa: SLF001
    cache_path.write_text(json.dumps(SAMPLE_PAYLOAD), encoding="utf-8")

    info = await client.resolve("Lightning Bolt")

    assert info is not None
    assert info.name == "Lightning Bolt"
    assert info.set_code == "LEA"
    assert info.set_name == "Limited Edition Alpha"
    assert info.image_url == "https://example.com/n.jpg"
    assert info.scryfall_uri == "https://scryfall.com/card/lea/161"


@pytest.mark.asyncio
async def test_resolve_returns_none_for_cached_404(tmp_path: Path):
    client = ScryfallClient(cache_dir=tmp_path)
    cache_path = client._cache_path("fuzzy", "Definitely Not A Card")  # noqa: SLF001
    cache_path.write_text(json.dumps({"__not_found__": True}), encoding="utf-8")

    assert await client.resolve("Definitely Not A Card") is None


@pytest.mark.asyncio
async def test_resolve_handles_double_faced_card_image(tmp_path: Path):
    payload = dict(SAMPLE_PAYLOAD)
    payload.pop("image_uris")
    payload["card_faces"] = [
        {"image_uris": {"normal": "https://example.com/face1.jpg"}},
        {"image_uris": {"normal": "https://example.com/face2.jpg"}},
    ]
    client = ScryfallClient(cache_dir=tmp_path)
    cache_path = client._cache_path("fuzzy", "Delver of Secrets")  # noqa: SLF001
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    info = await client.resolve("Delver of Secrets")
    assert info is not None
    assert info.image_url == "https://example.com/face1.jpg"


@pytest.mark.live
@pytest.mark.asyncio
async def test_scryfall_live_fuzzy_lookup(tmp_path: Path):
    client = ScryfallClient(cache_dir=tmp_path)
    info = await client.resolve("lightning bolt")  # case-insensitive resolve
    assert info is not None
    assert info.name == "Lightning Bolt"
    assert info.oracle_id is not None
    # Cache should now contain it.
    cached_path = client._cache_path("fuzzy", "lightning bolt")  # noqa: SLF001
    assert cached_path.exists()


@pytest.mark.live
@pytest.mark.asyncio
async def test_scryfall_live_unknown_card_caches_negative(tmp_path: Path):
    client = ScryfallClient(cache_dir=tmp_path)
    info = await client.resolve("definitely-not-a-real-card-xyz123")
    assert info is None
    cached = client._cache_path("fuzzy", "definitely-not-a-real-card-xyz123")  # noqa: SLF001
    assert cached.exists()
    import json as _j
    assert _j.loads(cached.read_text())["__not_found__"] is True
