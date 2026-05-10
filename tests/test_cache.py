from __future__ import annotations

import asyncio

import pytest

from cz_mtg_compare.cache import TTLCache


def test_get_returns_none_for_missing_key():
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)
    assert cache.get("missing") is None


def test_set_then_get_returns_value():
    cache: TTLCache[str] = TTLCache(ttl_seconds=60)
    cache.set("k", "v")
    assert cache.get("k") == "v"


def test_set_overwrites_previous_value():
    cache: TTLCache[str] = TTLCache(ttl_seconds=60)
    cache.set("k", "v1")
    cache.set("k", "v2")
    assert cache.get("k") == "v2"


def test_clear_empties_cache():
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None


def test_expired_entries_evicted_on_get(monkeypatch):
    """Past-TTL entries must be transparently dropped on access."""
    import cz_mtg_compare.cache as cache_mod

    fake_now = [1000.0]

    def _now() -> float:
        return fake_now[0]

    monkeypatch.setattr(cache_mod.time, "monotonic", _now)

    cache: TTLCache[str] = TTLCache(ttl_seconds=10.0)
    cache.set("k", "v")
    assert cache.get("k") == "v"
    fake_now[0] += 11.0
    assert cache.get("k") is None


@pytest.mark.asyncio
async def test_get_or_compute_caches_result_on_first_call():
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)
    calls = 0

    async def compute() -> int:
        nonlocal calls
        calls += 1
        return 42

    v1 = await cache.get_or_compute("key", compute)
    v2 = await cache.get_or_compute("key", compute)
    assert v1 == v2 == 42
    assert calls == 1


@pytest.mark.asyncio
async def test_get_or_compute_propagates_compute_exceptions():
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)

    async def boom() -> int:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError, match="nope"):
        await cache.get_or_compute("key", boom)
    # And does not cache the failed value.
    assert cache.get("key") is None


@pytest.mark.asyncio
async def test_get_or_compute_separate_keys_run_independently():
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)
    counter = 0

    async def make() -> int:
        nonlocal counter
        counter += 1
        return counter

    await cache.get_or_compute("a", make)
    await cache.get_or_compute("b", make)
    assert counter == 2
    # And both are cached.
    assert await cache.get_or_compute("a", make) in (1, 2)
    assert counter == 2  # no new compute call
