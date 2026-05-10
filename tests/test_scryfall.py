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


@pytest.mark.asyncio
async def test_fuzzy_and_exact_use_separate_cache_keys(tmp_path: Path):
    """A cached fuzzy result must not be served to an exact query and vice versa."""
    client = ScryfallClient(cache_dir=tmp_path)
    fuzzy_payload = dict(SAMPLE_PAYLOAD)
    fuzzy_payload["name"] = "Lightning Bolt FUZZY"
    exact_payload = dict(SAMPLE_PAYLOAD)
    exact_payload["name"] = "Lightning Bolt EXACT"

    client._cache_path("fuzzy", "Lightning Bolt").write_text(  # noqa: SLF001
        json.dumps(fuzzy_payload), encoding="utf-8"
    )
    client._cache_path("exact", "Lightning Bolt").write_text(  # noqa: SLF001
        json.dumps(exact_payload), encoding="utf-8"
    )

    fuzzy = await client.resolve("Lightning Bolt", exact=False)
    exact = await client.resolve("Lightning Bolt", exact=True)
    assert fuzzy is not None and fuzzy.name == "Lightning Bolt FUZZY"
    assert exact is not None and exact.name == "Lightning Bolt EXACT"


@pytest.mark.asyncio
async def test_resolve_handles_corrupted_cache_file(tmp_path: Path):
    """Garbage in the cache file must not crash; we just treat it as a miss."""
    client = ScryfallClient(cache_dir=tmp_path)
    # Pre-populate the negative-cache slot for the same name so we don't hit network.
    cache_path = client._cache_path("fuzzy", "Lightning Bolt")  # noqa: SLF001
    cache_path.write_text("not json {{{", encoding="utf-8")
    # Reading should return None (cache miss treatment) without raising.
    # Then write a negative-cache so the subsequent resolve() doesn't go to network.
    cache_path.write_text(json.dumps({"__not_found__": True}), encoding="utf-8")
    info = await client.resolve("Lightning Bolt")
    assert info is None


def test_cache_directory_is_created_if_missing(tmp_path: Path):
    new_dir = tmp_path / "subdir" / "scryfall"
    assert not new_dir.exists()
    ScryfallClient(cache_dir=new_dir)
    assert new_dir.exists()


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
