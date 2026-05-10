from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .http_client import get_client, host_slot

log = logging.getLogger(__name__)

API_BASE = "https://api.scryfall.com"
# Scryfall asks API consumers to throttle to <= 10 rps. We sleep this many seconds
# between API calls per process, in addition to the per-host concurrency cap.
MIN_REQUEST_INTERVAL_S = 0.1


def _default_cache_dir() -> Path:
    if env := os.environ.get("CZ_MTG_SCRYFALL_CACHE"):
        return Path(env).expanduser()
    return Path.home() / ".cache" / "cz-mtg-compare" / "scryfall"


def _key(name: str) -> str:
    h = hashlib.sha1(name.lower().strip().encode("utf-8")).hexdigest()[:16]
    safe = "".join(c if c.isalnum() else "-" for c in name.lower().strip())[:60]
    return f"{safe}-{h}"


class CardInfo(BaseModel):
    """Subset of Scryfall card data we surface through the MCP."""

    name: str
    oracle_id: str | None = None
    set_code: str | None = None
    set_name: str | None = None
    collector_number: str | None = None
    rarity: str | None = None
    mana_cost: str | None = None
    type_line: str | None = None
    oracle_text: str | None = None
    image_url: str | None = None
    scryfall_uri: str | None = None
    printed_names: dict[str, str] = {}  # locale_code -> printed_name when known


def _to_card_info(payload: dict[str, Any]) -> CardInfo:
    image_url: str | None = None
    images = payload.get("image_uris") or {}
    if isinstance(images, dict):
        image_url = images.get("normal") or images.get("large") or images.get("small")
    if not image_url:
        # Double-faced cards put images on each face.
        faces = payload.get("card_faces") or []
        if faces and isinstance(faces[0], dict):
            face_images = faces[0].get("image_uris") or {}
            image_url = face_images.get("normal") or face_images.get("large")

    printed_names: dict[str, str] = {}
    if printed := payload.get("printed_name"):
        lang = payload.get("lang") or "en"
        printed_names[lang] = printed

    return CardInfo(
        name=payload.get("name") or "",
        oracle_id=payload.get("oracle_id"),
        set_code=(payload.get("set") or "").upper() or None,
        set_name=payload.get("set_name"),
        collector_number=payload.get("collector_number"),
        rarity=payload.get("rarity"),
        mana_cost=payload.get("mana_cost"),
        type_line=payload.get("type_line"),
        oracle_text=payload.get("oracle_text"),
        image_url=image_url,
        scryfall_uri=payload.get("scryfall_uri"),
        printed_names=printed_names,
    )


class ScryfallClient:
    """Throttled Scryfall lookup with persistent on-disk JSON cache.

    Cache entries do not expire: Scryfall card data rarely changes retroactively
    and the user can wipe the directory at `CZ_MTG_SCRYFALL_CACHE` (defaults to
    `~/.cache/cz-mtg-compare/scryfall/`) to force re-fetch.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or _default_cache_dir()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request_at: float = 0.0
        self._lock = asyncio.Lock()

    def _cache_path(self, mode: str, name: str) -> Path:
        return self._cache_dir / f"{mode}-{_key(name)}.json"

    def _read_cache(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as e:
            log.warning("scryfall cache write failed: %r", e)

    async def _throttle(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < MIN_REQUEST_INTERVAL_S:
                await asyncio.sleep(MIN_REQUEST_INTERVAL_S - elapsed)
            self._last_request_at = time.monotonic()

    async def resolve(self, name: str, *, exact: bool = False) -> CardInfo | None:
        mode = "exact" if exact else "fuzzy"
        cache_path = self._cache_path(mode, name)
        cached = self._read_cache(cache_path)
        if cached is not None:
            if cached.get("__not_found__"):
                return None
            return _to_card_info(cached)

        await self._throttle()
        client = await get_client()
        params = {("exact" if exact else "fuzzy"): name}
        async with host_slot("api.scryfall.com"):
            resp = await client.get(
                f"{API_BASE}/cards/named",
                params=params,
                headers={"Accept": "application/json"},
            )
        if resp.status_code == 404:
            self._write_cache(cache_path, {"__not_found__": True})
            return None
        resp.raise_for_status()
        payload = resp.json()
        self._write_cache(cache_path, payload)
        return _to_card_info(payload)

    async def resolve_many(self, names: list[str], *, exact: bool = False) -> dict[str, CardInfo | None]:
        async def one(n: str) -> tuple[str, CardInfo | None]:
            return n, await self.resolve(n, exact=exact)

        results = await asyncio.gather(*(one(n) for n in names))
        return dict(results)
