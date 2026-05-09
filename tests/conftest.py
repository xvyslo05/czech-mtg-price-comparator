from __future__ import annotations

from pathlib import Path

import pytest

from cz_mtg_compare import http_client as _http_client

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def load_fixture(fixtures_dir: Path):
    def _load(name: str) -> str:
        return (fixtures_dir / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture(autouse=True)
async def _reset_http_client():
    """The shared httpx.AsyncClient is bound to its creation event loop.
    pytest-asyncio creates a fresh loop per test, so we must release the client
    between tests to avoid 'Event loop is closed' errors.
    """
    yield
    try:
        await _http_client.close_client()
    except Exception:
        _http_client._client = None  # noqa: SLF001 — best effort reset
