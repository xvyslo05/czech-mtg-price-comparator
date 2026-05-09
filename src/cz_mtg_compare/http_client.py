from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

USER_AGENT = "cz-mtg-compare-mcp/0.1 (+https://github.com/your-org/cz-mtg-compare-mcp)"
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
PER_HOST_CONCURRENCY = 3

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_host_semaphores: dict[str, asyncio.Semaphore] = {}


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is not None and not _client.is_closed:
        return _client
    async with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
                },
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                http2=True,
            )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _host_sem(host: str) -> asyncio.Semaphore:
    sem = _host_semaphores.get(host)
    if sem is None:
        sem = asyncio.Semaphore(PER_HOST_CONCURRENCY)
        _host_semaphores[host] = sem
    return sem


@asynccontextmanager
async def host_slot(host: str) -> AsyncIterator[None]:
    sem = _host_sem(host)
    async with sem:
        yield
