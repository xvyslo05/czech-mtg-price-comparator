from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cz_mtg_compare import fx


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = responses
        self.calls = 0

    async def get(self, url: str, **kwargs) -> _Response:
        assert url == fx.CNB_URL
        assert kwargs["timeout"] == 5.0
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return _Response(response)


@pytest.fixture(autouse=True)
def isolated_fx_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv("CZ_MTG_FX_CACHE", str(tmp_path))
    for env_var in (*fx.ENV_VARS.values(), "MKM_EUR_TO_CZK"):
        monkeypatch.delenv(env_var, raising=False)
    fx._reset_cache()  # noqa: SLF001
    yield
    fx._reset_cache()  # noqa: SLF001


@pytest.fixture
def cnb_text(load_fixture) -> str:
    return load_fixture("cnb_denni_kurz.txt")


def _install_client(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[str | Exception],
) -> _Client:
    client = _Client(responses)

    async def get_fake_client() -> _Client:
        return client

    monkeypatch.setattr(fx, "get_client", get_fake_client)
    return client


def test_parse_cnb_fixture_and_amount_division(cnb_text: str):
    text = f"{cnb_text.rstrip()}\nMaďarsko|forint|100|HUF|6,101\n"
    rates = fx.parse_cnb(text)

    assert rates["EUR"] == pytest.approx(24.185)
    assert rates["GBP"] == pytest.approx(28.347)
    assert rates["PLN"] == pytest.approx(5.589)
    assert rates["HUF"] == pytest.approx(0.06101)


@pytest.mark.asyncio
async def test_resolution_order_override_env_then_cached_live(
    monkeypatch: pytest.MonkeyPatch,
    cnb_text: str,
):
    client = _install_client(monkeypatch, [cnb_text])
    monkeypatch.setenv("CZ_MTG_EUR_TO_CZK", "26.0")

    assert await fx.rate_to_czk("EUR", override=27.0) == 27.0
    assert await fx.rate_to_czk("EUR") == 26.0
    assert client.calls == 0

    monkeypatch.delenv("CZ_MTG_EUR_TO_CZK")
    assert await fx.rate_to_czk("EUR") == pytest.approx(24.185)
    assert await fx.rate_to_czk("EUR") == pytest.approx(24.185)
    assert client.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["", "nope", "0", "-1", "nan", "inf"])
async def test_invalid_env_values_fall_through_to_cnb(
    monkeypatch: pytest.MonkeyPatch,
    cnb_text: str,
    invalid: str,
):
    client = _install_client(monkeypatch, [cnb_text])
    monkeypatch.setenv("CZ_MTG_EUR_TO_CZK", invalid)

    assert await fx.rate_to_czk("EUR") == pytest.approx(24.185)
    assert client.calls == 1


@pytest.mark.asyncio
async def test_disk_cache_round_trip_and_stale_offline_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cnb_text: str,
):
    first_client = _install_client(monkeypatch, [cnb_text])
    assert await fx.rate_to_czk("GBP") == pytest.approx(28.347)
    assert first_client.calls == 1

    cache_path = tmp_path / "rates.json"
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["rates"]["GBP"] == pytest.approx(28.347)
    payload["fetched_at"] = 0
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    fx._reset_cache()  # noqa: SLF001
    offline_client = _install_client(monkeypatch, [OSError("offline")])
    assert await fx.rate_to_czk("GBP") == pytest.approx(28.347)
    assert offline_client.calls == 1


@pytest.mark.asyncio
async def test_failure_without_cache_uses_static_default(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _install_client(monkeypatch, [OSError("offline")])

    assert await fx.rate_to_czk("PLN") == fx.STATIC_DEFAULTS["PLN"]
    assert client.calls == 1


def test_concurrent_cold_cache_is_safe_across_sequential_event_loops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    fetch_calls = 0

    async def fetch_rates() -> dict[str, float]:
        nonlocal fetch_calls
        fetch_calls += 1
        await asyncio.sleep(0)
        return {"EUR": 30.0, "GBP": 31.0, "PLN": 6.0}

    async def resolve_concurrently() -> list[float]:
        return await asyncio.gather(
            fx.rate_to_czk("EUR"),
            fx.rate_to_czk("EUR"),
        )

    monkeypatch.setattr(fx, "_fetch_cnb", fetch_rates)
    caplog.set_level("WARNING", logger=fx.__name__)

    first = asyncio.run(resolve_concurrently())
    fx._reset_cache()  # noqa: SLF001 - force a cold cache on the next loop
    (tmp_path / "rates.json").unlink()
    second = asyncio.run(resolve_concurrently())

    assert first == second == [30.0, 30.0]
    assert fetch_calls == 2
    assert "FX rate resolution failed" not in caplog.text


@pytest.mark.asyncio
async def test_fetch_cnb_bounds_the_whole_request(
    monkeypatch: pytest.MonkeyPatch,
):
    class HangingClient:
        async def get(self, url: str, **kwargs) -> _Response:
            assert url == fx.CNB_URL
            assert kwargs["timeout"] == 0.01
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def get_hanging_client() -> HangingClient:
        return HangingClient()

    monkeypatch.setattr(fx, "get_client", get_hanging_client)
    monkeypatch.setattr(fx, "REQUEST_TIMEOUT_SECONDS", 0.01)

    assert await asyncio.wait_for(fx._fetch_cnb(), timeout=0.2) is None


@pytest.mark.asyncio
async def test_memory_and_disk_cache_respect_24_hour_ttl(
    monkeypatch: pytest.MonkeyPatch,
    cnb_text: str,
):
    later_text = cnb_text.replace(
        "EMU|euro|1|EUR|24,185",
        "EMU|euro|1|EUR|25,000",
    )
    client = _install_client(monkeypatch, [cnb_text, later_text])
    clock = {"monotonic": 100.0, "wall": 10_000.0}
    monkeypatch.setattr(fx.time, "monotonic", lambda: clock["monotonic"])
    monkeypatch.setattr(fx.time, "time", lambda: clock["wall"])

    assert await fx.rate_to_czk("EUR") == pytest.approx(24.185)
    clock["monotonic"] += fx.CACHE_TTL_SECONDS - 1
    clock["wall"] += fx.CACHE_TTL_SECONDS - 1
    fx._reset_cache()  # noqa: SLF001 - simulate a process restart
    assert await fx.rate_to_czk("EUR") == pytest.approx(24.185)
    assert client.calls == 1

    clock["monotonic"] += 2
    clock["wall"] += 2
    assert await fx.rate_to_czk("EUR") == pytest.approx(25.0)
    assert client.calls == 2


def test_nolive_resolution_uses_env_then_static(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("CZ_MTG_GBP_TO_CZK", "29.25")
    assert fx.rate_to_czk_nolive("GBP") == 29.25
    monkeypatch.setenv("CZ_MTG_GBP_TO_CZK", "invalid")
    assert fx.rate_to_czk_nolive("GBP") == fx.STATIC_DEFAULTS["GBP"]
