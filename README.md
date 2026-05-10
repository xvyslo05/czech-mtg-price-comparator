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
- [Setup](#setup) — three install options: `uvx`, `pipx`, plain `pip`
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
| `cardmarket.com`     | OAuth1 API (opt-in, **untested live**) | aggregate priceGuide (TREND/AVG/LOW + foil), EUR→CZK — see [Cardmarket section](#optional-enable-cardmarket) |

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

## Setup

You don't need to clone the repo. Pick one of the install methods below, paste the matching JSON into Claude Desktop's config, restart Claude — done.

### Prerequisites

| Requirement       | How to check                          |
|-------------------|---------------------------------------|
| Claude Desktop    | https://claude.ai/download            |
| Python 3.11+      | `python3 --version`                   |
| One of: `uvx`, `pipx`, or plain `pip` | see below |

If your `python3` is 3.10 or older, install a newer one: `brew install python@3.12` on macOS, or [python.org](https://www.python.org/downloads/) on any platform.

### Install method A — `uvx` (recommended)

`uv` is a fast Python package manager. `uvx` runs Python apps in isolated environments and caches them. **No clone, no venv, no manual install** — uvx fetches the package from GitHub on first run and caches it.

**1. Install uv** (one-liner):

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**2. Find uvx's absolute path** — Claude Desktop doesn't always inherit your shell's `PATH`, so you must give it the full path:

```bash
# macOS / Linux
which uvx
# → /Users/you/.local/bin/uvx   (or /opt/homebrew/bin/uvx)

# Windows PowerShell
(Get-Command uvx).Source
```

**3. Add this to Claude Desktop's config** (see the [config-file location table](#step-add-to-claude-desktops-config-file) below):

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "/ABSOLUTE/PATH/TO/uvx",
      "args": [
        "--from",
        "git+https://github.com/xvyslo05/czech-mtg-price-comparator.git",
        "cz-mtg-compare-mcp"
      ]
    }
  }
}
```

The first time Claude Desktop starts the server, uvx fetches and installs the package (~10–20 seconds). Subsequent starts are instant.

To **upgrade** later, run `uvx --refresh-package cz-mtg-compare-mcp --from git+https://github.com/xvyslo05/czech-mtg-price-comparator.git cz-mtg-compare-mcp` once.

### Install method B — `pipx`

`pipx` installs Python apps in isolated venvs and exposes their console scripts on your `PATH`.

**1. Install pipx** (https://pipx.pypa.io/stable/installation/):

```bash
# macOS
brew install pipx && pipx ensurepath

# Linux
python3 -m pip install --user pipx && python3 -m pipx ensurepath

# Windows
python -m pip install --user pipx
python -m pipx ensurepath
```

Reopen your terminal so `PATH` updates take effect.

**2. Install the server**:

```bash
pipx install git+https://github.com/xvyslo05/czech-mtg-price-comparator.git
```

**3. Find the absolute path of the installed binary**:

```bash
# macOS / Linux
which cz-mtg-compare-mcp
# → /Users/you/.local/bin/cz-mtg-compare-mcp

# Windows PowerShell
(Get-Command cz-mtg-compare-mcp).Source
```

**4. Add this to Claude Desktop's config**:

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "/ABSOLUTE/PATH/TO/cz-mtg-compare-mcp",
      "args": []
    }
  }
}
```

To **upgrade** later: `pipx upgrade cz-mtg-compare-mcp`.

### Install method C — plain `pip`

If you don't want extra tooling, install with system pip and point Claude at the script directly.

```bash
python3 -m pip install --user git+https://github.com/xvyslo05/czech-mtg-price-comparator.git

# Find the script
python3 -c "import sysconfig; print(sysconfig.get_path('scripts'))"
# → /Users/you/Library/Python/3.12/bin     (macOS)
# → /home/you/.local/bin                   (Linux)
# → C:\Users\you\AppData\Roaming\Python\Python312\Scripts   (Windows)
```

The full path to the script is `<that-directory>/cz-mtg-compare-mcp`. Then add to Claude Desktop's config:

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "/ABSOLUTE/PATH/TO/cz-mtg-compare-mcp",
      "args": []
    }
  }
}
```

To **upgrade** later: `python3 -m pip install --user --upgrade git+https://github.com/xvyslo05/czech-mtg-price-comparator.git`.

### Step: add to Claude Desktop's config file

The config file lives at:

| OS       | Path                                                          |
|----------|---------------------------------------------------------------|
| macOS    | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows  | `%APPDATA%\Claude\claude_desktop_config.json`                 |
| Linux    | `~/.config/Claude/claude_desktop_config.json`                 |

If the file doesn't exist yet, create it. If you already have other MCP servers configured, **don't replace the whole file** — add the `"cz-mtg-compare"` entry alongside the existing ones inside the same `"mcpServers"` object.

A complete macOS example using uvx:

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "/Users/robin/.local/bin/uvx",
      "args": [
        "--from",
        "git+https://github.com/xvyslo05/czech-mtg-price-comparator.git",
        "cz-mtg-compare-mcp"
      ]
    }
  }
}
```

### Step: restart Claude Desktop

Fully quit Claude Desktop (don't just close the window — use **Cmd+Q** on macOS, or right-click the tray icon → Quit on Windows) and reopen it.

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

Cardmarket would give you EU-wide pricing as a fallback for cards Czech shops don't carry. The adapter is implemented and ships with the server, but it's **off by default** and only activates if you provide the four `MKM_*` OAuth1 credentials as environment variables.

> ⚠️ **Untested in production.** As of the last update to this repo, **Cardmarket is not accepting new API access requests** — they've paused signups for the Dedicated App / Personal tier. The adapter was built against their published API spec (request signing verified against the OAuth1 reference, response shape verified against their `/products/find` schema), and unit tests cover the full request/response cycle, but it has not been live-tested end-to-end. If you have an existing Cardmarket API key from before signups were paused, the steps below should work — please open an issue if anything misbehaves.

### 1. Get a Cardmarket dedicated app token (if signups have reopened)

1. Go to https://www.cardmarket.com/en/Magic/Account/API
2. Apply for a **Dedicated App** (Personal/Free tier is enough for read-only price aggregates).
3. After approval, you'll see four values: `App Token`, `App Secret`, `Access Token`, `Access Token Secret`.

### 2. Add them to your Claude Desktop config

Add an `"env"` block alongside `"command"` and `"args"` in your existing entry. With uvx, that looks like:

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "/Users/you/.local/bin/uvx",
      "args": [
        "--from",
        "git+https://github.com/xvyslo05/czech-mtg-price-comparator.git",
        "cz-mtg-compare-mcp"
      ],
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

`MKM_EUR_TO_CZK` is optional (default `24.5`). Restart Claude Desktop and Cardmarket offers should start showing up alongside the Czech shops; verify with `list_shops`.

### What the adapter does and doesn't cover

- **Covered**: `/products/find?search=<name>&idGame=1` with OAuth1 HMAC-SHA1 signing, `priceGuide` parsing (TREND / AVG / LOW with LOW fallback), foil variants surfaced as separate offers, EUR→CZK conversion, `set_code` / `edition` filtering, max-results truncation, missing-key tolerance.
- **Not covered**: per-seller article listings (`/articles/{idProduct}` — requires a paid Trader-tier API key, ~€20/year). The Free/Personal tier only exposes aggregate priceGuide data, so cardmarket offers come back without specific condition / language / seller info — `condition` is `UNKNOWN` and `stock_qty` defaults to 1 (priceGuide implies sellers exist but doesn't quantify them).
- **Behaviour without credentials**: if any of the four `MKM_*` env vars is missing or empty, the adapter is silently dropped from the default adapter list — no startup errors, no failed auth requests, just no `cardmarket` entries in `list_shops`.

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
- Make sure the path in `command` is **absolute**. Claude Desktop usually doesn't inherit your shell's `PATH`, so a bare `uvx` or `cz-mtg-compare-mcp` will fail to launch.
- Open Claude Desktop's developer tools (macOS: `Cmd+Option+I` while focused on the chat) and check the console for MCP server errors.
- Try running the server manually:
  - uvx: `uvx --from git+https://github.com/xvyslo05/czech-mtg-price-comparator.git cz-mtg-compare-mcp`
  - pipx / pip: `cz-mtg-compare-mcp`

  It should hang waiting for stdin input — that's correct behaviour. Press `Ctrl+C` to exit.

**`ModuleNotFoundError: No module named 'cz_mtg_compare'`.**
- The install didn't complete. Re-run your install command (`pipx install ...` or `python3 -m pip install --user ...`) and confirm it finishes without errors.

**uvx hangs or times out the first time Claude Desktop starts the server.**
- First-time installs through git can take 10–30 seconds while uvx fetches dependencies. Subsequent starts are instant. If it consistently fails, run the manual command above from a terminal to see the underlying error.

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

## Non-playable cards

Display-only products (Art Series, oversized cards, helper / tip / checklist cards, spindowns) are **excluded by default** because they aren't legal in any constructed Magic format. The filter looks at both card name and edition for any of these markers.

If you specifically want them — e.g. you're price-checking an art print or a collector item — pass `include_non_playable=True` to either `search_card` or `optimize_decklist`. Or just say so in chat:

> "Find me Art Series Lightning Bolt — include non-playable cards."

Claude will pass the flag through automatically.

## Limitations

- **No shipping cost optimization.** The multi-shop split picks the cheapest *card* prices, ignoring that buying from four shops means four shipping fees. Per-shop totals let you see the trade-off, but the optimizer doesn't pick for you.
- **blacklotus condition can occasionally still be `?`** if the product page lacks the gtag variant marker — best-effort only.
- **Cardmarket per-seller offers** require a paid Trader-tier API key, not yet wired up. Free tier surfaces priceGuide aggregates only.
- **Decklist size capped at 100 cards.** Commander format is the largest legal format.
- **No price history.** Each query is a fresh snapshot. Track prices yourself if you need it (or open an issue requesting it).

---

## Development

If you want to hack on the server or run the test suite, do a clone-and-editable-install:

```bash
git clone https://github.com/xvyslo05/czech-mtg-price-comparator.git
cd czech-mtg-price-comparator
python3 -m venv .venv
source .venv/bin/activate           # macOS/Linux; .venv\Scripts\activate on Windows
pip install -e ".[dev]"

# Fast deterministic tests (~0.2s)
pytest

# Live smoke tests against real shops + Scryfall (~5s)
pytest -m live --override-ini="addopts="

# Manual MCP smoke test (server speaks stdio)
python -m cz_mtg_compare
```

The shop adapters are tested against checked-in HTML/JSON fixtures in `tests/fixtures/`, so the bulk of the test suite is offline and deterministic. Live smoke tests under `tests/test_live_smoke.py` are opt-in via `-m live`.

To point Claude Desktop at your local working copy instead of the published GitHub version, use the `.venv/bin/python -m cz_mtg_compare` command form:

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "/absolute/path/to/repo/.venv/bin/python",
      "args": ["-m", "cz_mtg_compare"]
    }
  }
}
```

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
