# cz-mtg-compare-mcp

A **Model Context Protocol** server that lets Claude (or any other MCP client) compare **Magic: The Gathering** single-card prices across the four major Czech online card shops, optionally falling back to Cardmarket. Ask Claude what something costs — it queries every shop in parallel and returns one normalized, price-sorted list.

```
You:    Find me the cheapest in-stock Lightning Bolt across the Czech shops.

Claude: (calls search_card)
        → 31 offers found:
          • tolarie:    35 Kč  NM  Battle for Baldur's Gate Extras
          • najada:     49 Kč  NM  Commander Legends: BfBG Extras (Showcase)
          • cernyrytir: 59 Kč  LP  4th Edition (4ED)
          ...
        Cheapest copy is on tolarie.cz at 35 Kč.
```

---

## Table of contents

- [What this is](#what-this-is)
- [Supported shops](#supported-shops)
- [What you can ask Claude](#what-you-can-ask-claude)
- [Setup (5 minutes)](#setup-5-minutes)
- [Verify it's working](#verify-its-working)
- [Optional: enable Cardmarket](#optional-enable-cardmarket)
- [Configuration reference](#configuration-reference)
- [Troubleshooting](#troubleshooting)
- [How it works under the hood](#how-it-works-under-the-hood)
- [Limitations](#limitations)
- [Development](#development)
- [Repo layout](#repo-layout)

---

## What this is

This is an MCP server. MCP is the protocol Claude Desktop (and other clients) use to call external tools. Once configured, Claude can:

- **Search a single card** across all four Czech shops at once.
- **Optimize a Commander/Standard/Modern decklist** — paste the list in chat, get back the cheapest combination of shops to buy from, plus each shop's solo total.
- **Resolve card names** through Scryfall (canonical name, set/collector#, oracle text, multilingual printed names).
- **Fall back to Cardmarket** for European pricing when CZ shops don't carry a card (optional, requires API credentials).

It does **not**: place orders, log in to shops, manage carts, send notifications, or do anything other than read public listings.

---

## Supported shops

| Shop                 | Mechanism                              | Covered fields                                |
|----------------------|----------------------------------------|------------------------------------------------|
| `tolarie.cz`         | HTML scrape (server-rendered table)    | name, edition, condition, foil, stock, price |
| `najada.cz` / `najada.games` | JSON API (`wizardshop.cz`)     | name, edition, set code, condition, language, foil, stock count, price |
| `blacklotus.cz`      | HTML scrape (Shoptet) + detail-page enrichment | name, edition, condition, foil, stock, price |
| `cernyrytir.cz`      | HTML scrape (windows-1250, POST search) | name, edition, set code, condition, foil, stock, price |
| `cardmarket.com`     | OAuth1 API (opt-in)                    | aggregate priceGuide (TREND/AVG/LOW + foil), EUR→CZK |

---

## What you can ask Claude

Once installed, you can talk to Claude in plain Czech or English. Some examples that work well:

> "Najdi mi nejlevnější Lightning Bolt skladem napříč českými obchody."
>
> "How much would this Commander deck cost from each shop separately, and what's the cheapest if I buy across all of them?"  *(then paste the decklist)*
>
> "Show me all foil printings of Sol Ring available right now and where they are."
>
> "Lookup Atraxa, Praetors' Voice on Scryfall and tell me which sets it's printed in."
>
> "Compare prices for the cards in this Pioneer deck — but only from najada and tolarie."

Claude picks the right tool, calls it, and summarises the result.

---

## Setup (5 minutes)

### Prerequisites

| Requirement       | How to check                          |
|-------------------|---------------------------------------|
| Python 3.11+      | `python3 --version`                   |
| Git               | `git --version`                       |
| Claude Desktop    | https://claude.ai/download            |

If `python3` says you have 3.10 or older, install a newer one (e.g. `brew install python@3.12` on macOS, or [python.org](https://www.python.org/downloads/) on any platform).

### 1. Clone the repo

```bash
git clone https://github.com/xvyslo05/czech-mtg-price-comparator.git
cd czech-mtg-price-comparator
```

### 2. Create a virtual environment and install

```bash
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows PowerShell
pip install -e .
```

That installs the project plus its runtime dependencies (`mcp`, `httpx`, `selectolax`, `pydantic`, `tenacity`).

### 3. Find the absolute path of the Python interpreter

You need this path for the Claude Desktop config — it must be the **absolute** path to `python` inside the `.venv` you just created.

```bash
# macOS / Linux
realpath .venv/bin/python
# → /Users/you/projects/czech-mtg-price-comparator/.venv/bin/python

# Windows PowerShell
Resolve-Path .venv\Scripts\python.exe
```

Copy that full path — you'll paste it into the config in the next step.

### 4. Configure Claude Desktop

Open Claude Desktop's config file. The location depends on your OS:

| OS       | Path                                                          |
|----------|---------------------------------------------------------------|
| macOS    | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows  | `%APPDATA%\Claude\claude_desktop_config.json`                 |
| Linux    | `~/.config/Claude/claude_desktop_config.json`                 |

If the file doesn't exist yet, create it. Paste this and replace `<ABSOLUTE_PATH>` with the path you copied:

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "<ABSOLUTE_PATH>",
      "args": ["-m", "cz_mtg_compare"]
    }
  }
}
```

If you already have other MCP servers configured, just add the `"cz-mtg-compare"` entry alongside them inside the existing `"mcpServers"` object — don't replace the whole file.

A complete macOS example:

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "/Users/robin/projects/czech-mtg-price-comparator/.venv/bin/python",
      "args": ["-m", "cz_mtg_compare"]
    }
  }
}
```

### 5. Restart Claude Desktop

Fully quit Claude Desktop (don't just close the window — use **Cmd+Q** on macOS or right-click the tray icon → Quit on Windows) and reopen it.

---

## Verify it's working

Open a new chat in Claude Desktop and ask:

> "What MCP tools do you have available?"

You should see at least these four tools listed:

- `search_card`
- `optimize_decklist`
- `lookup_card`
- `list_shops`

Then try a real query:

> "Find Lightning Bolt across all Czech card shops, show me the five cheapest in-stock copies."

Claude will call `search_card`, the server will fan out to all four shops in parallel (typically responding in 2–4 seconds), and Claude will summarise the results.

If something doesn't work, jump to [Troubleshooting](#troubleshooting).

---

## Optional: enable Cardmarket

Cardmarket gives you EU-wide pricing as a fallback for cards Czech shops don't carry. It's **off by default** and only activates if you provide OAuth1 credentials.

### 1. Get a Cardmarket dedicated app token

1. Go to https://www.cardmarket.com/en/Magic/Account/API
2. Apply for a **Dedicated App** (Personal/Free tier is enough for read-only price aggregates).
3. After approval, you'll see four values: `App Token`, `App Secret`, `Access Token`, `Access Token Secret`.

### 2. Add them to your Claude Desktop config

Update the server entry to pass them as environment variables:

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "cz_mtg_compare"],
      "env": {
        "MKM_APP_TOKEN": "...",
        "MKM_APP_SECRET": "...",
        "MKM_ACCESS_TOKEN": "...",
        "MKM_ACCESS_TOKEN_SECRET": "...",
        "MKM_EUR_TO_CZK": "24.7"
      }
    }
  }
}
```

`MKM_EUR_TO_CZK` is optional (default `24.5`). Restart Claude Desktop and Cardmarket offers will start showing up alongside the Czech shops.

> **Note**: the Free tier only exposes aggregate priceGuide data (TREND / AVG / LOW + foil variants) — not specific seller listings. Per-seller offers require a paid Trader-tier account and are not yet implemented in this server.

---

## Configuration reference

| Environment variable          | Purpose                                            | Default |
|-------------------------------|----------------------------------------------------|---------|
| `MKM_APP_TOKEN`               | Cardmarket OAuth1 app token                        | unset (Cardmarket disabled) |
| `MKM_APP_SECRET`              | Cardmarket OAuth1 app secret                       | unset |
| `MKM_ACCESS_TOKEN`            | Cardmarket OAuth1 access token                     | unset |
| `MKM_ACCESS_TOKEN_SECRET`     | Cardmarket OAuth1 access token secret              | unset |
| `MKM_API_BASE`                | Override Cardmarket API base URL                   | `https://api.cardmarket.com/ws/v2.0/output.json` |
| `MKM_EUR_TO_CZK`              | EUR → CZK conversion rate for Cardmarket prices    | `24.5` |
| `CZ_MTG_SCRYFALL_CACHE`       | Override Scryfall on-disk cache directory          | `~/.cache/cz-mtg-compare/scryfall/` |

### Disabling individual shops

Two ways:

1. **Per-call**: tell Claude which shops to use.
   > "Only check tolarie and najada for this card."

   Claude will pass `shops=["tolarie", "najada"]` to `search_card`.

2. **Globally**: edit `src/cz_mtg_compare/adapters/__init__.py` and remove the unwanted adapter from `build_default_adapters()`.

---

## Troubleshooting

**Tools don't appear in Claude Desktop after restart.**
- Make sure the path in `command` is **absolute** and points at the `python` *inside* `.venv`. A common mistake is using just `python` or a system-wide path.
- Open Claude Desktop's developer tools (macOS: `Cmd+Option+I` while focused on the chat) and check the console for MCP server errors.
- Try running the server manually: `path/to/.venv/bin/python -m cz_mtg_compare`. It should hang waiting for stdin input — that's correct behaviour. Press `Ctrl+C` to exit.

**`ModuleNotFoundError: No module named 'cz_mtg_compare'`.**
- The venv is missing the install. Re-run `pip install -e .` from inside the project directory with the venv activated.

**`Event loop is closed` errors during testing.**
- Already handled by `tests/conftest.py`. If you see it elsewhere, the shared `httpx.AsyncClient` was bound to a now-closed loop — call `cz_mtg_compare.http_client.close_client()` between event-loop boundaries.

**One shop's results are missing or stale.**
- Each shop's last-call status is exposed via the `list_shops` tool. Ask Claude:
  > "Run list_shops and tell me if any shop is failing."
- Results are cached for 10 minutes; results older than that get auto-refreshed.

**Cardmarket returns nothing.**
- Run `list_shops` and check if `cardmarket` is included. If it isn't, the credentials weren't loaded — verify the `env` block in your Claude Desktop config and that you fully restarted Claude Desktop (Cmd+Q, not just close window).

**Search returns offers that don't match the card I asked about.**
- Some Czech shops' search engines are loose with substring matching. If you're querying a card with a common word in its name (e.g. "Lightning"), narrow down with `edition=...`. Ask Claude:
  > "Search for Lightning Bolt, but only from the Strixhaven set."

---

## How it works under the hood

```
                          ┌────────────────────┐
   Claude Desktop  ◄────► │  MCP server (stdio)│
                          │  cz_mtg_compare    │
                          └─────────┬──────────┘
                                    │  fans out in parallel
              ┌─────────────────────┼──────────────────────┐
              ▼          ▼          ▼          ▼           ▼
         tolarie.cz  najada API  blacklotus  cernyrytir   cardmarket
         (HTML)      (JSON)      (HTML+detail) (HTML/cp1250)  (OAuth1)
              │          │          │          │           │
              └──────────┴──────────┴──────────┴───────────┘
                                    │
                                    ▼
                         normalized Offer[] sorted by price_czk
```

- A single `search_card` call dispatches to every adapter concurrently, with per-host concurrency capped at 3 and a 10-second timeout per shop.
- Each adapter returns a list of normalized `Offer` objects with the same fields regardless of source.
- Per-shop results are cached in-memory for 10 minutes (LRU eviction not yet, just TTL).
- One shop failing or timing out **never** kills the query — partial results come back, and the failed shop's error is surfaced through `list_shops`.

The decklist optimizer is a thin layer on top: it parses the deck, fans out one `search_card` per unique card (still capped per-host, so 100 cards → 100 sequential-per-host searches but parallel across shops), then computes:

- **Multi-shop split**: pick the cheapest in-stock copy of each card across all shops.
- **Per-shop bundles**: for each shop on its own, sum the cheapest offer per card it has and count cards it's missing.

Each `Offer` includes a `url` you can click through to the shop.

---

## Limitations

- **No shipping cost optimization.** The multi-shop split picks the cheapest *card* prices, ignoring that buying from four shops means four shipping fees. Per-shop totals let you see the trade-off, but the optimizer doesn't pick for you.
- **blacklotus condition can occasionally still be `?`** if the product page lacks the gtag variant marker — best-effort only.
- **Cardmarket per-seller offers** require a paid Trader-tier API key, not yet wired up. Free tier surfaces priceGuide aggregates only.
- **Decklist size capped at 100 cards.** Commander format is the largest legal format.
- **No price history.** Each query is a fresh snapshot. Track prices yourself if you need it (or open an issue requesting it).

---

## Development

```bash
# Install dev dependencies (pytest etc.)
pip install -e ".[dev]"

# Fast deterministic tests (~0.2s)
pytest

# Live smoke tests against real shops + Scryfall (~5s)
pytest -m live --override-ini="addopts="

# Manual MCP smoke test (server speaks stdio)
python -m cz_mtg_compare
```

The shop adapters are tested against checked-in HTML/JSON fixtures in `tests/fixtures/`, so the bulk of the test suite is offline and deterministic. Live smoke tests under `tests/test_live_smoke.py` are opt-in via `-m live`.

---

## Repo layout

```
src/cz_mtg_compare/
  server.py            MCP entrypoint; registers search_card / optimize_decklist /
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
    base.py            ShopAdapter ABC
    tolarie.py
    najada.py          (JSON API on wizardshop.cz)
    blacklotus.py      (Shoptet HTML + detail-page enrichment)
    cernyrytir.py      (windows-1250 HTML)
    cardmarket.py      (OAuth1 API; opt-in)
tests/
  fixtures/            saved real-world responses
  test_*_adapter.py    deterministic adapter tests
  test_aggregator.py
  test_optimizer.py
  test_decklist_parser.py
  test_scryfall.py
  test_live_smoke.py   opt-in live tests
```

---

*Etiquette*: this server reads only public listing pages, identifies itself with a clear `User-Agent`, caps per-host concurrency at 3, and caches results for 10 minutes to avoid hammering shops. It's intended for personal price-comparison use. If a shop owner asks you to stop, please respect that.
