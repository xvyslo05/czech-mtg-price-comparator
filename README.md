# cz-mtg-compare-mcp

MCP server that searches **Magic: The Gathering** single-card prices across Czech online card shops and returns one normalized, comparable offer list.

Supported shops:

| Shop                | Source        | Notes                                    |
|---------------------|---------------|------------------------------------------|
| `cernyrytir.cz`     | HTML scrape   | windows-1250 page, name search via POST  |
| `najada.cz` / `najada.games` | JSON API (`wizardshop.cz`) | rich variants: condition, language, foil, stock counts |
| `blacklotus.cz`     | HTML scrape (Shoptet) | condition + foil from image alt; edition from product description |
| `tolarie.cz`        | HTML scrape   | server-rendered table, condition + foil as inline icons |

Cardmarket is intentionally **not** in v1.

## What you get

Two MCP tools:

- `search_card(name, edition=None, in_stock_only=True, shops=None)` — fans out to all shops in parallel; returns a flat, **price-sorted** list of offers.
- `list_shops()` — last-call status per shop (ok / error / offer count).

Each `Offer` carries:

```jsonc
{
  "shop": "najada",
  "card_name": "Lightning Bolt",
  "edition": "Secret Lair Drop Series: Extra Life",
  "set_code": "SLD",
  "condition": "NM",
  "language": "EN",
  "foil": false,
  "price_czk": 99,
  "stock_qty": 4,
  "url": "https://najada.games/vyhledavani?q=Lightning+Bolt",
  "fetched_at": "2026-05-10T..."
}
```

Per-shop calls are cached in-memory for 10 minutes; a single shop's failure never sinks the whole query.

## Install (local development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the server (stdio):

```bash
python -m cz_mtg_compare
```

Run the test suite:

```bash
pytest                          # 27 fixture-based tests, ~0.1s
pytest -m live --override-ini="addopts="   # live smoke against real shops, ~5s
```

## Plugging into Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "/absolute/path/to/cz-mtg-price-comparator/.venv/bin/python",
      "args": ["-m", "cz_mtg_compare"]
    }
  }
}
```

Restart Claude Desktop. You can then ask things like:

> "Najdi mi nejlevnější Lightning Bolt skladem napříč českými obchody."

Claude will call `search_card` and rank the returned offers.

## Disabling a shop

Pass an explicit `shops=[…]` list when calling the tool, or pop the unwanted adapter from `build_default_adapters()` in `src/cz_mtg_compare/adapters/__init__.py`.

## Notes on scraping etiquette

- Calls go out with an identifiable `User-Agent` and per-host concurrency cap of 3.
- Results are cached for 10 minutes to avoid hammering shops on repeated queries.
- The server only reads public listing pages; it never touches cart, login, or admin endpoints.
- This is intended for **personal price-comparison use**. If a shop owner asks you to stop, please respect that.

## Repo layout

```
src/cz_mtg_compare/
  server.py            MCP entrypoint (stdio)
  models.py            Offer / Condition / SearchQuery
  aggregator.py        async fan-out + per-shop timeouts + cache
  http_client.py       shared httpx.AsyncClient
  cache.py             TTL cache
  normalize.py         price / stock / condition / foil helpers
  adapters/
    base.py
    tolarie.py
    najada.py          (JSON API)
    blacklotus.py      (Shoptet HTML)
    cernyrytir.py      (windows-1250 HTML)
tests/
  fixtures/            saved real-world responses
  test_*_adapter.py    deterministic tests
  test_aggregator.py   fan-out + partial-failure
  test_live_smoke.py   opt-in live tests
```
