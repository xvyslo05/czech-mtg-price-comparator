from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import weakref
from pathlib import Path
from typing import Any

from .http_client import get_client, host_slot

log = logging.getLogger(__name__)

STATIC_DEFAULTS = {"EUR": 24.5, "GBP": 28.5, "PLN": 5.8}
ENV_VARS = {
    "EUR": "CZ_MTG_EUR_TO_CZK",
    "GBP": "CZ_MTG_GBP_TO_CZK",
    "PLN": "CZ_MTG_PLN_TO_CZK",
}
CNB_URL = (
    "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/"
    "kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt"
)
CACHE_TTL_SECONDS = 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 5.0

_CACHE_FILENAME = "rates.json"
_memory_rates: dict[str, float] | None = None
_memory_cached_at = 0.0
_cache_locks: weakref.WeakValueDictionary[
    asyncio.AbstractEventLoop, asyncio.Lock
] = weakref.WeakValueDictionary()


def _cache_lock_for_running_loop() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _cache_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _cache_locks[loop] = lock
    return lock


def parse_cnb(text: str) -> dict[str, float]:
    """Parse CNB's pipe-delimited daily exchange-rate sheet."""
    rates: dict[str, float] = {}
    for line in text.splitlines()[2:]:
        columns = line.split("|")
        if len(columns) < 5:
            continue
        code = columns[3].strip().upper()
        try:
            amount = float(columns[2].strip().replace(",", "."))
            quoted_rate = float(columns[4].strip().replace(",", "."))
        except (TypeError, ValueError):
            continue
        if (
            not code
            or not math.isfinite(amount)
            or not math.isfinite(quoted_rate)
            or amount <= 0
            or quoted_rate <= 0
        ):
            continue
        rates[code] = quoted_rate / amount
    return rates


def rate_from_env(name: str) -> float | None:
    """Return a valid positive rate from an environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _valid_rate(value: Any) -> float | None:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def _cache_dir() -> Path:
    if configured := os.environ.get("CZ_MTG_FX_CACHE"):
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "cz-mtg-compare" / "fx"


def _cache_path() -> Path:
    return _cache_dir() / _CACHE_FILENAME


def _normalise_rates(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    rates: dict[str, float] = {}
    for raw_code, raw_rate in value.items():
        rate = _valid_rate(raw_rate)
        if rate is not None:
            rates[str(raw_code).upper()] = rate
    return rates


def _read_disk_cache() -> tuple[dict[str, float], float] | None:
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
        rates = _normalise_rates(payload.get("rates"))
        fetched_at = float(payload.get("fetched_at", 0.0))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not rates or not math.isfinite(fetched_at):
        return None
    return rates, fetched_at


def _write_disk_cache(rates: dict[str, float]) -> None:
    try:
        cache_dir = _cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": time.time(), "rates": rates}
        _cache_path().write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        log.warning("FX cache write failed: %r", exc)


def _memory_is_fresh() -> bool:
    return (
        _memory_rates is not None
        and time.monotonic() - _memory_cached_at < CACHE_TTL_SECONDS
    )


def _remember(
    rates: dict[str, float],
    *,
    age_seconds: float = 0.0,
) -> dict[str, float]:
    global _memory_cached_at, _memory_rates
    _memory_rates = rates
    _memory_cached_at = time.monotonic() - max(0.0, age_seconds)
    return rates


async def _fetch_cnb() -> dict[str, float] | None:
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            client = await get_client()
            async with host_slot("www.cnb.cz"):
                response = await client.get(
                    CNB_URL,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            response.raise_for_status()
            rates = parse_cnb(response.text)
            if not all(currency in rates for currency in STATIC_DEFAULTS):
                return None
            return rates
    except Exception as exc:  # noqa: BLE001 - FX must never fail a shop search
        log.warning("CNB FX fetch failed; using cached/default rates: %r", exc)
        return None


async def _cached_live_rates() -> dict[str, float]:
    if _memory_is_fresh():
        return _memory_rates or {}

    async with _cache_lock_for_running_loop():
        if _memory_is_fresh():
            return _memory_rates or {}

        disk_entry = _read_disk_cache()
        if disk_entry is not None:
            disk_rates, fetched_at = disk_entry
            disk_age = max(0.0, time.time() - fetched_at)
            if disk_age < CACHE_TTL_SECONDS:
                return _remember(disk_rates, age_seconds=disk_age)
        else:
            disk_rates = {}

        fetched_rates = await _fetch_cnb()
        if fetched_rates is not None:
            _write_disk_cache(fetched_rates)
            return _remember(fetched_rates)

        # A stale real rate remains preferable to a static estimate. Remember
        # failures too, so every adapter search does not retry CNB immediately.
        return _remember(disk_rates)


async def rate_to_czk(
    currency: str,
    *,
    override: float | None = None,
) -> float:
    """Resolve a CZK rate: override → env → cached/live CNB → static.

    This function never raises. Unsupported currencies fall back to 1.0.
    """
    try:
        code = str(currency).strip().upper()
        if (rate := _valid_rate(override)) is not None:
            return rate
        env_var = ENV_VARS.get(code)
        if env_var and (rate := rate_from_env(env_var)) is not None:
            return rate
        rates = await _cached_live_rates()
        if (rate := _valid_rate(rates.get(code))) is not None:
            return rate
        return STATIC_DEFAULTS.get(code, 1.0)
    except Exception as exc:  # noqa: BLE001 - public FX resolution never raises
        log.warning("FX rate resolution failed; using static default: %r", exc)
        return STATIC_DEFAULTS.get(str(currency).strip().upper(), 1.0)


def rate_to_czk_nolive(
    currency: str,
    *,
    override: float | None = None,
) -> float:
    """Resolve a rate without network access, for fixture/parser call paths."""
    try:
        code = str(currency).strip().upper()
        if (rate := _valid_rate(override)) is not None:
            return rate
        env_var = ENV_VARS.get(code)
        if env_var and (rate := rate_from_env(env_var)) is not None:
            return rate
        if _memory_rates and (rate := _valid_rate(_memory_rates.get(code))) is not None:
            return rate
        disk_entry = _read_disk_cache()
        if disk_entry and (rate := _valid_rate(disk_entry[0].get(code))) is not None:
            return rate
        return STATIC_DEFAULTS.get(code, 1.0)
    except Exception as exc:  # noqa: BLE001 - parser paths must never raise
        log.warning("Offline FX rate resolution failed; using static default: %r", exc)
        return STATIC_DEFAULTS.get(str(currency).strip().upper(), 1.0)


def _reset_cache() -> None:
    """Clear process-local state (used by the offline test suite)."""
    global _memory_cached_at, _memory_rates
    _memory_rates = None
    _memory_cached_at = 0.0
