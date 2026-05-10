# cz-mtg-compare-mcp

MCP server that searches **Magic: The Gathering** single-card prices across Czech online card shops (with optional Cardmarket fallback) and returns one normalized, comparable offer list.

Supported shops:

| Shop                | Source        | Notes                                    |
|---------------------|---------------|------------------------------------------|
| `cernyrytir.cz`     | HTML scrape   | windows-1250 page, name search via POST  |
| `najada.cz` / `najada.games` | JSON API (`wizardshop.cz`) | rich variants: condition, language, foil, stock counts |
| `blacklotus.cz`     | HTML scrape (Shoptet) | listing → detail-page enrichment for missing condition / edition |
| `tolarie.cz`        | HTML scrape   | server-rendered table, condition + foil as inline icons |
| `cardmarket.com`    | OAuth1 API    | enabled only if `MKM_*` env vars are set; aggregate priceGuide (TREND/LOW/AVG) converted EUR→CZK |

## What you get

Four MCP tools:

- `search_card(name, edition=None, in_stock_only=True, shops=None)` — fans out to all shops in parallel; returns a flat, **price-sorted** list of offers.
- `optimize_decklist(decklist, in_stock_only=True)` — paste an Arena/MTGO decklist (≤100 cards, Commander limit), get the cheapest multi-shop split + per-shop bundle totals.
- `lookup_card(name, exact=False)` — Scryfall canonical resolution (set, oracle text, image URL, multilingual names). Disk-cached.
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

### Decklist optimizer

Input is the standard Arena/MTGO export (set codes, collector numbers, and `*F*` foil markers are ignored — we only canonicalize on name):

```
4 Lightning Bolt
4 Counterspell
2 Sol Ring (CMR) 263
1 Lightning Bolt (M11) 149 *F*

Sideboard
1 Negate

Commander
1 Atraxa, Praetors' Voice
```

Sections (`Deck`, `Sideboard`, `Maybeboard`, `Commander`) are recognised; comments (`//`, `#`) and blank lines are ignored.

Output bundles two views:

- `picks[]` — the **cheapest in-stock copy of each card across all shops** (the multi-shop split).
- `per_shop_bundles[]` — for each shop on its own: cards covered, cards missing, single-shop CZK total. Sorted best-to-worst.

The split usually beats any single shop, but per-shop totals are useful when you want to minimise shipping by buying everything from one place.

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
pytest                          # 54 fixture-based tests, ~0.2s
pytest -m live --override-ini="addopts="   # 7 live smoke tests against real shops + Scryfall, ~5s
```

## Cardmarket setup (optional)

Cardmarket is **only enabled when its credentials are present** in the environment; otherwise it's silently dropped. Get OAuth1 dedicated-app tokens at https://www.cardmarket.com/en/Magic/Account/API:

```bash
export MKM_APP_TOKEN=...
export MKM_APP_SECRET=...
export MKM_ACCESS_TOKEN=...
export MKM_ACCESS_TOKEN_SECRET=...
# Optional EUR→CZK conversion rate (default 24.5)
export MKM_EUR_TO_CZK=24.7
```

The Free/Personal tier provides priceGuide-level aggregates (TREND/LOW/AVG, plus foil variants); per-seller article listings need a paid tier and are not yet wired up.

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
>
> "Tady je můj Commander deck — jakou kombinaci nakoupit nejlevněji?"

Claude calls `search_card` / `optimize_decklist` and ranks the returned data.

## Disabling a shop

Pass an explicit `shops=[…]` list when calling the tool, or pop the unwanted adapter from `build_default_adapters()` in `src/cz_mtg_compare/adapters/__init__.py`.

## Notes on scraping etiquette

- Calls go out with an identifiable `User-Agent` and per-host concurrency cap of 3.
- Results are cached for 10 minutes to avoid hammering shops on repeated queries.
- Scryfall calls are throttled (≤10 rps) and disk-cached at `~/.cache/cz-mtg-compare/scryfall/` (override via `CZ_MTG_SCRYFALL_CACHE`).
- The server only reads public listing pages; it never touches cart, login, or admin endpoints.
- This is intended for **personal price-comparison use**. If a shop owner asks you to stop, please respect that.

## Repo layout

```
src/cz_mtg_compare/
  server.py            MCP entrypoint (stdio); registers search_card / optimize_decklist /
                       lookup_card / list_shops tools
  models.py            Offer / Condition / SearchQuery / ShopId
  aggregator.py        async fan-out + per-shop timeouts + cache
  optimizer.py         decklist optimization (multi-shop split + per-shop bundles)
  decklist.py          Arena/MTGO text parser; ≤100 cards
  scryfall.py          Scryfall lookup with throttle + disk cache
  http_client.py       shared httpx.AsyncClient
  cache.py             TTL cache
  normalize.py         price / stock / condition / foil helpers
  adapters/
    base.py
    tolarie.py
    najada.py          (JSON API)
    blacklotus.py      (Shoptet HTML + detail-page enrichment)
    cernyrytir.py      (windows-1250 HTML)
    cardmarket.py      (OAuth1 API; opt-in via env vars)
tests/
  fixtures/            saved real-world responses
  test_*_adapter.py    deterministic adapter tests
  test_aggregator.py   fan-out + partial-failure
  test_optimizer.py    decklist split + per-shop bundles
  test_decklist_parser.py
  test_scryfall.py
  test_live_smoke.py   opt-in live tests
```
