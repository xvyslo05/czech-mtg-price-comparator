# cz-mtg-compare-mcp

A **Model Context Protocol** server that lets Claude (or any other MCP client) compare **Magic: The Gathering** single-card prices across six major Czech online card shops, optionally falling back to Cardmarket. Ask Claude what something costs — it queries every shop in parallel and returns one normalized, price-sorted list.

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
- [Setup](#setup) — `uvx`, `pipx`, plain `pip`, or local clone
- [Verify it's working](#verify-its-working)
- [Optional: enable Cardmarket](#optional-enable-cardmarket)
- [Optional: account features (login + cart)](#optional-account-features-login--cart)
- [Configuration reference](#configuration-reference)
- [Troubleshooting](#troubleshooting)
- [How it works under the hood](#how-it-works-under-the-hood)
- [HTTP API (experimental)](#http-api-experimental)
- [Limitations](#limitations)
- [Development](#development)
- [Repo layout](#repo-layout)

---

## What this is

This is an MCP server. MCP is the protocol Claude Desktop (and other clients) use to call external tools. Once configured, Claude can:

- **Search a single card** across all six Czech shops at once.
- **Optimize a Commander/Standard/Modern decklist** — paste the list in chat, get back either the cheapest cross-shop split (default) or a plan that consolidates into the fewest distinct shops (`strategy="fewest_shops"`, within a 10% price tolerance), plus each shop's solo total.
- **Resolve card names** through Scryfall (canonical name, set/collector#, oracle text, multilingual printed names).
- **Fall back to Cardmarket** for European pricing when CZ shops don't carry a card (optional, requires API credentials).
- **(Optional) Log into a shop and manage your cart** for shops where account features are implemented. Currently: `najada` / `blacklotus` / `tolarie` / `rishada` / `cernyrytir` (full — login + add/view/clear cart) and `untap` (login only). See [Account features](#optional-account-features-login--cart). Credentials may be plain strings *or* `op://...` 1Password references.

It does **not** place orders or send notifications — cart contents must still be checked out manually on each shop's website.

---

## Supported shops

| Shop                 | Mechanism                              | Covered fields                                |
|----------------------|----------------------------------------|------------------------------------------------|
| `tolarie.cz`         | HTML scrape (server-rendered table)    | name, edition, condition, foil, stock, price |
| `najada.cz` / `najada.games` | JSON API (`wizardshop.cz`)     | name, edition, set code, condition, language, foil, stock count, price |
| `blacklotus.cz`      | HTML scrape (Shoptet) + detail-page enrichment | name, edition, condition, foil, stock, price |
| `cernyrytir.cz`      | HTML scrape (windows-1250, POST search) | name, edition, set code, condition, foil, stock, price |
| `rishada.cz`         | HTML scrape (custom-PHP, tabular)      | name, edition, condition, foil (incl. judge / etched), stock, price |
| `untap.cz`           | HTML scrape (Prestashop)               | name, edition, set code, condition + foil from product reference, stock, price |
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
>
> "I'd rather place fewer separate orders — optimize this decklist to use the fewest shops possible, even if it costs a bit more."
>
> "Log into najada and add 4× Lightning Bolt at the cheapest price into my cart." *(requires `CZ_MTG_NAJADA_USER` / `_PASS` env vars; see [Account features](#optional-account-features-login--cart))*
>
> "Show me what's currently in my najada cart."

Claude picks the right tool, calls it, and summarises the result.

---

## Setup

You don't need to clone the repo. Pick one of the install methods below, paste the matching JSON into Claude Desktop's config, restart Claude — done. Method **D** is for users who *do* want to clone (development, running unreleased commits).

### Prerequisites

| Requirement       | How to check                          |
|-------------------|---------------------------------------|
| Claude Desktop    | https://claude.ai/download            |
| Python 3.11+      | `python3 --version`                   |
| One of: `uvx`, `pipx`, plain `pip`, or a local clone | see below |

If your `python3` is 3.10 or older, install a newer one: `brew install python@3.12` on macOS, or [python.org](https://www.python.org/downloads/) on any platform.

### Install method A — `uvx` (recommended)

`uv` is a fast Python package manager. `uvx` runs Python apps in isolated environments and caches them. **No clone, no venv, no manual install** — uvx fetches the package and caches it on first run.

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
      "args": ["cz-mtg-compare-mcp"]
    }
  }
}
```

The first time Claude Desktop starts the server, uvx fetches the package from PyPI and installs it (a few seconds). Subsequent starts are instant.

To **upgrade** later, run `uvx --refresh-package cz-mtg-compare-mcp cz-mtg-compare-mcp` once.

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
pipx install cz-mtg-compare-mcp
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
python3 -m pip install --user cz-mtg-compare-mcp

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

To **upgrade** later: `python3 -m pip install --user --upgrade cz-mtg-compare-mcp`.

### Install method D — from a local clone (development / unreleased changes)

Use this when you've cloned the repo and want to run the server from your working copy — useful for developing on the server, testing PRs, or running an unreleased commit.

```bash
git clone https://github.com/xvyslo05/czech-mtg-price-comparator.git
cd czech-mtg-price-comparator
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows PowerShell
pip install -e .
```

Then point Claude Desktop at the in-venv interpreter:

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

Edits to the source take effect on the next Claude Desktop restart — no rebuild needed thanks to `-e` (editable install).

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
      "command": "/opt/homebrew/bin/uvx",
      "args": ["cz-mtg-compare-mcp"]
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

You should see at least these tools listed:

- `search_card`
- `optimize_decklist`
- `lookup_card`
- `list_shops`
- `shop_account_capabilities`, `shop_login`, `add_to_cart`, `view_cart`, `clear_cart`, `add_to_watchlist` *(account features — only useful for shops where they're supported; see [the matrix below](#optional-account-features-login--cart))*

Then try a real query:

> "Find Lightning Bolt across all Czech card shops, show me the five cheapest in-stock copies."

Claude will call `search_card`, the server will fan out to all six shops in parallel (typically responding in 2–4 seconds), and Claude will summarise the results.

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
      "command": "/opt/homebrew/bin/uvx",
      "args": ["cz-mtg-compare-mcp"],
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

## Optional: account features (login + cart)

In addition to anonymous price comparison, the server can **log into individual shops on your behalf** and manage the contents of your cart there. Useful when Claude has already picked the cheapest split — instead of clicking through every offer URL manually, you can ask Claude to add them straight into the shop's cart, then check out yourself on the shop's website.

This is **opt-in per shop**. The server has no credentials by default and never tries to log in unless you explicitly configure them.

> ⚠️ **Read this before enabling.** You are storing shop passwords in your local Claude Desktop config (or your 1Password vault). The MCP server will use them to make authenticated requests on your behalf. Use a unique password per shop; consider whether each shop's Terms of Service permit automated cart manipulation; and remember that any `search_card` call made after a successful login goes out with your session cookie attached, so the shop can correlate those searches with your account.

### Per-shop capability matrix

| Shop          | Login | Cart (add / view / clear) | Watchlist |
|---------------|-------|---------------------------|-----------|
| `najada`      | ✅    | ✅                        | ❌ (planned) |
| `blacklotus`  | ✅    | ✅                        | ❌        |
| `tolarie`     | ✅    | ✅                        | ❌ (planned) |
| `untap`       | ✅    | ⛔ disabled (each login starts a fresh checkout — items don't persist) | ❌ |
| `cernyrytir`  | ✅    | ✅                        | ❌        |
| `rishada`     | ✅    | ✅                        | ❌        |
| `cardmarket`  | ❌    | ❌                        | ❌        |

Each shop's account flow has to be reverse-engineered separately, so the supported set grows shop-by-shop. The `shop_account_capabilities` MCP tool reports this matrix at runtime including whether credentials are currently configured — ask Claude *"which shops can I log into?"*.

### Configuring credentials

Each shop reads its credentials from two env vars, set in your Claude Desktop config under the server's `"env"` block:

```
CZ_MTG_<SHOP>_USER   # username or email (depending on shop)
CZ_MTG_<SHOP>_PASS   # password
```

Shop ids match those used everywhere else (`najada`, `tolarie`, …). Example using the uvx setup:

```json
{
  "mcpServers": {
    "cz-mtg-compare": {
      "command": "/opt/homebrew/bin/uvx",
      "args": ["cz-mtg-compare-mcp"],
      "env": {
        "CZ_MTG_NAJADA_USER": "alice@example.com",
        "CZ_MTG_NAJADA_PASS": "your-najada-password",
        "CZ_MTG_BLACKLOTUS_USER": "alice@example.com",
        "CZ_MTG_BLACKLOTUS_PASS": "your-blacklotus-password",
        "CZ_MTG_UNTAP_USER": "alice@example.com",
        "CZ_MTG_UNTAP_PASS": "your-untap-password",
        "CZ_MTG_TOLARIE_USER": "alice",
        "CZ_MTG_TOLARIE_PASS": "your-tolarie-password",
        "CZ_MTG_CERNYRYTIR_USER": "alice",
        "CZ_MTG_CERNYRYTIR_PASS": "your-cernyrytir-password",
        "CZ_MTG_RISHADA_USER": "alice",
        "CZ_MTG_RISHADA_PASS": "your-rishada-password"
      }
    }
  }
}
```

If one var of a pair is set and the other isn't, you'll get an explicit `CredentialError` on the first login attempt for that shop — silent partial-config is rejected. Both vars missing means the shop is just treated as "no credentials" (the read-only search adapter keeps working).

### Using 1Password instead of putting passwords in config

Either env var value may be a **1Password secret reference** of the form `op://Vault/Item/Field`. On first use, the server shells out to the [1Password CLI](https://developer.1password.com/docs/cli) (`op`) to resolve it and caches the result in-process — so each `op read` happens at most once per server run.

```json
{
  "env": {
    "CZ_MTG_NAJADA_USER": "op://Personal/Najada/username",
    "CZ_MTG_NAJADA_PASS": "op://Personal/Najada/password"
  }
}
```

Requirements:

1. The [`op` CLI](https://developer.1password.com/docs/cli/get-started) is installed and on the PATH visible to Claude Desktop. (Same `PATH` caveat as `uvx` — Claude Desktop may not inherit your shell PATH; if `op read` resolution fails, the error message will say so.)
2. You're signed into 1Password (`op signin`) **or** have biometric / system-auth integration enabled so non-interactive `op read` calls succeed.
3. Each `op://` reference resolves to a non-empty value (an empty value is rejected with a clear error).

You can mix the two forms — e.g. literal `_USER` and `op://` `_PASS` — freely.

### What Claude can actually do

Once configured, ask Claude things like:

> "Add four copies of Lightning Bolt from najada into my cart — pick the cheapest in-stock copies."
>
> "What's in my najada cart right now?"
>
> "Empty my najada cart, I'm starting over." *(asks for confirmation; `clear_cart` is destructive)*

Under the hood Claude calls:

- `shop_account_capabilities()` — discover what's supported and what's configured
- `shop_login(shop="najada")` — eager-login if needed; otherwise the cart tools log in lazily
- `add_to_cart(shop, shop_ref, count=N)` — `shop_ref` is the per-shop product/article id that appears on every `Offer` returned by `search_card` / `optimize_decklist`. Pass it through verbatim (it's a UUID for najada, a numeric id for tolarie / rishada / cernyrytir). If you don't have a `shop_ref` yet, run `search_card` first. For rishada and cernyrytir, `count` is clamped to the row's available stock by the server, so a request for more than is available silently adds the maximum the shop has.
- `view_cart(shop)` — return the shop's current cart contents
- `clear_cart(shop)` — delete every item from the shop's cart (returns `{"removed_items": N}`)
- `add_to_watchlist(shop, shop_ref)` — only on shops that support it (none today; placeholder for follow-up PRs)

Sessions live in-process for the lifetime of the MCP server. If the cached token ever expires server-side, the next cart call transparently re-logs in and retries once before surfacing an error — you don't need to call `shop_login` manually.

### Known limitations

- **najada watchlist (`own-wantlist-items` / `own-shopping-list-items`) is not wired up yet** — the endpoints exist but require an authenticated session to inspect the schema, and the nested-product shape is non-trivial. Planned for a follow-up PR.
- **untap cart is disabled even though it works.** untap's Prestashop install starts a brand-new checkout on every login. Items added by this MCP server during a Claude Desktop session disappear the moment the user logs in again (whether through this server or in a browser). The cart code is kept around so a future PR can flip `supports_cart` back on if untap migrates to a session-spanning cart, but exposing it today would just confuse users — the cart-add API call returns success but the cards never materialise on the user's account.
- **No checkout.** The server stops at "items are in your cart"; finalizing the order, choosing shipping, paying — all still happens manually on the shop's website. By design.

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
| `CZ_MTG_DISABLED_SHOPS`       | Comma-separated, case-insensitive list of shop IDs to drop at startup (e.g. `blacklotus,untap`) | unset |
| `CZ_MTG_MAX_UNIQUE_CARDS`     | Hard cap on unique cards per `optimize_decklist` call (one HTTP request per unique card per shop). Invalid / non-positive values are ignored | `100` |
| `CZ_MTG_CONSOLIDATE_TOLERANCE_PCT` | How much extra (in %) the `fewest_shops` strategy may pay vs. the cheapest-split total in exchange for consolidating into fewer shops. Integer percent; invalid / non-positive values are ignored | `10` |
| `CZ_MTG_NAJADA_USER` / `CZ_MTG_NAJADA_PASS`         | Najada (`wizardshop.cz`) account credentials for login + cart. Literal or `op://...` 1Password reference. See [Account features](#optional-account-features-login--cart) | unset (login disabled for najada) |
| `CZ_MTG_BLACKLOTUS_USER` / `CZ_MTG_BLACKLOTUS_PASS` | Blacklotus account credentials for login + cart | unset (login disabled for blacklotus) |
| `CZ_MTG_UNTAP_USER` / `CZ_MTG_UNTAP_PASS`           | Untap (Prestashop) account credentials for login + cart | unset (login disabled for untap) |
| `CZ_MTG_TOLARIE_USER` / `CZ_MTG_TOLARIE_PASS`       | Tolarie account credentials for login + cart | unset (login disabled for tolarie) |
| `CZ_MTG_CERNYRYTIR_USER` / `CZ_MTG_CERNYRYTIR_PASS` | Černý rytíř account credentials for login + cart | unset (login disabled for cernyrytir) |
| `CZ_MTG_RISHADA_USER` / `CZ_MTG_RISHADA_PASS`       | Rishada account credentials for login + cart | unset (login disabled for rishada) |
| `CZ_MTG_DATABASE_URL`         | SQLAlchemy URL for the web app's database. Required for the upcoming auth / vault work (issue #9 → B/C). Example: `postgresql+asyncpg://user:pass@host/dbname`. The MCP server doesn't use this | `sqlite+aiosqlite:///:memory:` (dev / tests only) |
| `CZ_MTG_DATABASE_ECHO`        | Log all SQL emitted by the web app's engine (debug) | `false` |
| `CZ_MTG_SESSION_COOKIE_NAME`  | Cookie name carrying the opaque server-side session id           | `cz_session` |
| `CZ_MTG_CSRF_COOKIE_NAME`     | Cookie name carrying the CSRF token (readable by JS by design)   | `cz_csrf` |
| `CZ_MTG_SESSION_TTL_SECONDS`  | Session lifetime in seconds; invalid / non-positive values fall back to the default | `2592000` (30 days) |
| `CZ_MTG_COOKIE_SECURE`        | Whether session/CSRF cookies require HTTPS. Set to `false` for local dev over plain HTTP | `true` |
| `CZ_MTG_COOKIE_SAMESITE`      | SameSite flag for both cookies. One of `lax`, `strict`, `none`   | `lax` |
| `CZ_MTG_COOKIE_DOMAIN`        | Cookie Domain attribute. Leave unset for host-only cookies       | unset |
| `CZ_MTG_EMAIL_VERIFY_TTL_SECONDS` | Email-verification token lifetime in seconds. Invalid / non-positive values fall back to the default | `86400` (24h) |
| `CZ_MTG_PUBLIC_BASE_URL`      | Origin used when building links in outbound mail and Google's OAuth redirect URI | `http://localhost:8080` |
| `CZ_MTG_OAUTH_GOOGLE_CLIENT_ID`     | OAuth 2.0 client id from Google Cloud Console. When unset, `/v1/auth/oauth/google/start` returns `503` instead of crashing | unset (Google sign-in disabled) |
| `CZ_MTG_OAUTH_GOOGLE_CLIENT_SECRET` | OAuth 2.0 client secret matching the client id above | unset |
| `CZ_MTG_OAUTH_GOOGLE_REDIRECT_URI`  | Override the callback URL (must match a redirect URI registered in the console) | `{PUBLIC_BASE_URL}/v1/auth/oauth/google/callback` |

### Disabling individual shops

Three ways, in increasing scope:

1. **Per-call allow-list**: tell Claude which shops you DO want.
   > "Only check tolarie and najada for this card."

   Claude passes `shops=["tolarie", "najada"]` to `search_card` / `optimize_decklist`.

2. **Per-call deny-list** (opt-out): tell Claude which shops you DON'T want.
   > "Search Lightning Bolt everywhere except blacklotus."

   Claude passes `exclude_shops=["blacklotus"]`. Combines with `shops`: the deny-list is applied AFTER the allow-list, so an explicit deny always wins. Excluded shops also disappear from `per_shop_bundles` in the optimizer's output.

3. **Server-wide opt-out** via env var. Add `CZ_MTG_DISABLED_SHOPS` to your Claude Desktop config — comma-separated, case-insensitive — and those shops are dropped from the default adapter list at startup. Useful if you have a standing reason to never query a particular shop (bad past experience, slow responses, etc.):

   ```json
   {
     "mcpServers": {
       "cz-mtg-compare": {
         "command": "/opt/homebrew/bin/uvx",
         "args": ["cz-mtg-compare-mcp"],
         "env": {
           "CZ_MTG_DISABLED_SHOPS": "blacklotus,untap"
         }
       }
     }
   }
   ```

   Unknown shop names in the env var are silently ignored, so a typo can't brick the server.

---

## Troubleshooting

**Tools don't appear in Claude Desktop after restart.**
- Make sure the path in `command` is **absolute**. Claude Desktop usually doesn't inherit your shell's `PATH`, so a bare `uvx` or `cz-mtg-compare-mcp` will fail to launch.
- Open Claude Desktop's developer tools (macOS: `Cmd+Option+I` while focused on the chat) and check the console for MCP server errors.
- Try running the server manually:
  - uvx: `uvx cz-mtg-compare-mcp`
  - pipx / pip: `cz-mtg-compare-mcp`
  - local clone: `.venv/bin/python -m cz_mtg_compare`

  It should hang waiting for stdin input — that's correct behaviour. Press `Ctrl+C` to exit.

**`ModuleNotFoundError: No module named 'cz_mtg_compare'`.**
- The install didn't complete. Re-run your install command (`pipx install ...` or `python3 -m pip install --user ...`) and confirm it finishes without errors.

**uvx hangs or times out the first time Claude Desktop starts the server.**
- First-time installs take a few seconds while uvx fetches the package + its dependencies from PyPI. Subsequent starts are instant. If it consistently fails, run the manual command above from a terminal to see the underlying error.

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
       ┌────────────┬────────────┬──┴──────────┬────────────┬────────────┬────────────┐
       ▼            ▼            ▼             ▼            ▼            ▼            ▼
   tolarie.cz  najada API   blacklotus    cernyrytir    rishada       untap       cardmarket
   (HTML)      (JSON)       (Shoptet      (HTML/cp1250  (custom PHP   (Presta-    (OAuth1,
                            +detail)       POST search)  table)        shop)        opt-in)
       │            │            │             │            │            │            │
       └────────────┴────────────┴─────────────┴────────────┴────────────┴────────────┘
                                              │
                                              ▼
                                   normalized Offer[] sorted by price_czk
```

- A single `search_card` call dispatches to every adapter concurrently, with per-host concurrency capped at 3 and a 10-second timeout per shop.
- Each adapter returns a list of normalized `Offer` objects with the same fields regardless of source.
- Per-shop results are cached in-memory for 10 minutes (LRU eviction not yet, just TTL).
- One shop failing or timing out **never** kills the query — partial results come back, and the failed shop's error is surfaced through `list_shops`.

The decklist optimizer is a thin layer on top: it parses the deck, fans out one `search_card` per unique card (still capped per-host, so 100 cards → 100 sequential-per-host searches but parallel across shops), then computes one of two shopping plans:

- **`cheapest` (default)** — per-card greedy split: pick the cheapest in-stock copy of each card across all shops. Minimizes total CZK; may fragment the order across many shops.
- **`fewest_shops`** — consolidate the order into the smallest number of distinct shops, while staying within 10% of the cheapest-split total (overridable via `CZ_MTG_CONSOLIDATE_TOLERANCE_PCT`). Internally it enumerates every non-empty subset of contributing shops, picks the cheapest in-subset offer per card (falling back to the global cheapest for cards the subset doesn't sell), filters to candidates within budget, and returns the plan with the fewest effective shops, ties broken by total.

Both modes return a `shopping_plan` grouped by shop plus a `cheapest_split_total_czk` baseline; `fewest_shops` also populates `consolidated_total_czk` so callers can show the consolidation premium.

- **Per-shop bundles**: for each shop on its own, sum the cheapest offer per card it has and count cards it's missing. Strategy-independent — always returned.

Each `Offer` includes a `url` you can click through to the shop. Offers from shops that support account features also carry an opaque `shop_ref` (e.g. najada's UUID article id, tolarie's numeric product id) that the cart tools need — but only when produced by a search in the same server session.

### Account features pipeline

The login + cart tools live in `adapters/base.py` (capability flags + `AccountFeatureNotSupported` default impls), `credentials.py` (env-var and 1Password resolution), and each adapter that opts in. The pipeline for an `add_to_cart` call:

1. `server.py` forwards the call to `service.CardCompareService`, which looks up the right adapter by `shop_id`.
2. The adapter checks for an existing auth token / session. If absent, it pulls credentials via `credentials_for(shop_id)` (resolving any `op://` references through the 1Password CLI on first use), hits the shop's login endpoint, and stores the resulting bearer token or session cookie on the adapter instance.
3. It POSTs the cart-add request using the offer's `shop_ref`. On 401 it clears the cached session so the next call triggers a fresh login.
4. The raw shop response (or an error with a clear message) is returned to Claude.

Sessions are per-process; they live as long as the MCP server does and are torn down on restart.

---

## Non-playable cards

Display-only products (Art Series, oversized cards, helper / tip / checklist cards, spindowns) are **excluded by default** because they aren't legal in any constructed Magic format. The filter looks at both card name and edition for any of these markers.

If you specifically want them — e.g. you're price-checking an art print or a collector item — pass `include_non_playable=True` to either `search_card` or `optimize_decklist`. Or just say so in chat:

> "Find me Art Series Lightning Bolt — include non-playable cards."

Claude will pass the flag through automatically.

## HTTP API (experimental)

A FastAPI surface ships alongside the MCP server for the upcoming web-app work tracked in issue #9. **Read-only and unauthenticated for now** — cart and login endpoints will land with the credential vault (workstream C); per-user API keys + rate limiting land with G1/G2. Run anywhere you'd run the MCP server.

```bash
pip install "cz-mtg-compare-mcp[web]"
cz-mtg-compare-web --host 0.0.0.0 --port 8080
# OpenAPI docs at http://localhost:8080/docs
```

Available endpoints (v1):

| Method | Path                       | Notes                                                          |
|--------|----------------------------|----------------------------------------------------------------|
| GET    | `/v1/health`               | Liveness probe                                                 |
| GET    | `/v1/shops`                | Configured shops + last-call status (mirrors `list_shops`)     |
| GET    | `/v1/shops/capabilities`   | Per-shop login/cart/watchlist flags + credential presence      |
| GET    | `/v1/cards/search`         | `?name=&edition=&in_stock_only=&shops=...&exclude_shops=...`   |
| GET    | `/v1/cards/lookup`         | `?name=&exact=` (Scryfall)                                     |
| POST   | `/v1/decklists/optimize`   | JSON body — `{decklist, strategy, shops, exclude_shops, ...}`  |
| GET    | `/v1/auth/csrf`            | Mint or refresh the CSRF token; sets the session + CSRF cookies |
| GET    | `/v1/auth/whoami`          | Returns `{authenticated, user_id}` for the current session     |
| POST   | `/v1/auth/signup`          | `{email, password}` → create account + log in (sets cookies) + send verification email |
| POST   | `/v1/auth/login`           | `{email, password}` → mint a fresh session                     |
| POST   | `/v1/auth/logout`          | Delete the current session, clear cookies (204 always)         |
| POST   | `/v1/auth/verify/request`  | Re-send verification email for the logged-in user (`202`)      |
| POST   | `/v1/auth/verify/confirm`  | `{token}` → mark email verified (CSRF-exempt; token is bearer) |
| GET    | `/v1/auth/oauth/google/start`    | Redirect to Google's consent screen (`503` if not configured) |
| GET    | `/v1/auth/oauth/google/callback` | Google → us. Exchanges code, creates/links user, redirects back |

The MCP server (`cz-mtg-compare-mcp`) and the HTTP server (`cz-mtg-compare-web`) share the same in-process service layer (`cz_mtg_compare.service.CardCompareService`); behaviour is identical across surfaces.

### Database

The web app owns a database — required once auth (B1) and the credential vault (C) start landing. Today's read-only surface doesn't touch it, so a fresh checkout still runs without setup, but you should configure one before stacking later PRs:

```bash
# 1. Point the app at your database
export CZ_MTG_DATABASE_URL="postgresql+asyncpg://user:pass@host/dbname"

# 2. Apply migrations (creates the users table)
alembic upgrade head

# 3. Start the server
cz-mtg-compare-web --host 0.0.0.0 --port 8080
```

Without `CZ_MTG_DATABASE_URL` the engine falls back to in-memory SQLite — fine for poking around, not for anything persistent. The MCP server does not use this DB at all; only the HTTP / web surface does.

### CSRF

State-changing requests (`POST` / `PUT` / `PATCH` / `DELETE`) are gated by a double-submit CSRF check **when the request carries a session cookie**. Unsessioned anonymous requests aren't gated — there's no escalation to defend against. Once login (B1 PR3) is wired up, every authenticated request must:

1. Have called `GET /v1/auth/csrf` at least once (sets the `cz_session` and `cz_csrf` cookies).
2. Mirror the CSRF cookie value into the `X-CSRF-Token` request header.

Server-side the middleware also checks the header against the session row's stored `csrf_token`, so revoking a session immediately kills its CSRF token — not just the cookie pair on whichever browser still has it cached.

### Email / password auth

- Passwords are hashed with **argon2id** (library defaults — time_cost=2, memory_cost=64 MiB, parallelism=1). Plaintext never touches the DB or logs. Hashes are re-upgraded on successful login when argon2's recommended parameters move forward.
- Emails are normalised to lowercase before storage AND lookup, so `Alice@Example.com` and `alice@example.com` can't both register.
- The login endpoint returns a **single** generic 401 for both "unknown email" and "wrong password" — it is not a user-enumeration oracle.
- Sessions are rotated on every successful login (the old session row is left untouched; explicit `POST /v1/auth/logout` revokes it).
- A verification email is sent on signup; the user can re-trigger it via `POST /v1/auth/verify/request`. Tokens are single-use, short-lived (24h default — override with `CZ_MTG_EMAIL_VERIFY_TTL_SECONDS`), and stored as SHA-256 hashes only.
- Email verification is **not** a hard gate yet — accounts are usable right after signup, and `email_verified` just flips when the user clicks the link. Routes that should require verification will get explicit guards in a follow-up PR.

### Mail delivery

The web app sends verification emails through a pluggable `Mailer` (`cz_mtg_compare.web.mailer`). The default is a `LoggingMailer` that writes the URL to the logger at INFO — good for dev, no SMTP setup needed. Production deployments swap in a real implementation by passing it to `create_app(mailer=...)`:

```python
from cz_mtg_compare.web.app import create_app

app = create_app(mailer=MyResendMailer())
```

Set `CZ_MTG_PUBLIC_BASE_URL` to your deployed origin (e.g. `https://card-compare.cz`) so the link in the email points at the right host. Defaults to `http://localhost:8080`.

### Google OAuth

Users can sign in with Google in addition to email + password. The flow is the standard server-side OAuth 2.0 / OpenID Connect dance:

1. Browser → `GET /v1/auth/oauth/google/start` → 302 to Google's consent screen.
2. Google → `GET /v1/auth/oauth/google/callback?code=...&state=...`.
3. Server exchanges the code, validates Google's ID token signature against Google's JWKS, then:
   - **Already linked** (matching `oauth_identities(provider="google", provider_user_id=sub)` row) → log the same user back in.
   - **No link, but `User.email == google.email` AND Google says `email_verified: true`** → attach a new `oauth_identities` row; flip `user.email_verified` if it wasn't already.
   - **No link, email matches but Google says `email_verified: false`** → reject with a specific 400 (the user must log in with password first and link Google from settings). This is the only non-generic auth error in the codebase, on purpose — the alternative (silent reject) is worse UX, and the one bit of information leaked ("an account with this email exists") is the same bit `POST /v1/auth/signup` already returns via 409.
   - **No link, no email match** → create a fresh user (`password_hash=null`, `email_verified` inherited from Google's claim) + identity row.
4. Server rotates the session (drops the anonymous pre-flow row, mints an authenticated one), sets cookies, redirects back to `CZ_MTG_PUBLIC_BASE_URL`.

The `state` parameter is single-use: the server clears it the moment the callback fires, regardless of outcome. A replay (correct state or not) lands on a fresh 400.

#### Google Cloud Console — one-time setup

1. Open [Google Cloud Console](https://console.cloud.google.com/), pick or create a project.
2. **APIs & Services → OAuth consent screen**: User type `External`, app name, your support / developer emails, scopes `openid email profile` (built-in, no review). Add yourself as a test user while the app is in `Testing` mode.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**: type `Web application`. Under **Authorized redirect URIs**, register every environment you'll run from:
   - `http://localhost:8080/v1/auth/oauth/google/callback` (local dev)
   - `https://<staging-host>/v1/auth/oauth/google/callback`
   - `https://<prod-host>/v1/auth/oauth/google/callback`
4. Copy the **Client ID** and **Client secret** into your deployment's env (`CZ_MTG_OAUTH_GOOGLE_CLIENT_ID`, `CZ_MTG_OAUTH_GOOGLE_CLIENT_SECRET`). Set `CZ_MTG_PUBLIC_BASE_URL` to the matching origin.

Caveats:
- `/v1/decklists/optimize` runs inline in the handler today. A 100-card list can fan out to ~600 upstream HTTP requests and take several seconds. Moving to a background job queue is tracked as A4 in issue #9.
- No rate limiting on the auth endpoints yet — that lands with G2.
- GitHub OAuth is **not** wired yet — that's B1 PR6, and most of the schema (`oauth_identities` keyed by `(provider, provider_user_id)`) is reused verbatim.
- No "unlink Google" endpoint yet — that needs the settings page (B2). Workaround for now: delete the row from `oauth_identities` directly.

---

## Limitations

- **Shipping cost isn't modelled explicitly.** The `cheapest` strategy minimizes card prices only and ignores per-shop shipping fees. Use `strategy="fewest_shops"` to consolidate into fewer orders (within 10% of the cheapest-split total by default, configurable via `CZ_MTG_CONSOLIDATE_TOLERANCE_PCT`). Per-shop totals still let you eyeball trade-offs manually.
- **blacklotus condition can occasionally still be `?`** if the product page lacks the gtag variant marker — best-effort only.
- **Cardmarket per-seller offers** require a paid Trader-tier API key, not yet wired up. Free tier surfaces priceGuide aggregates only.
- **Decklist size capped at 100 cards total AND 100 unique cards.** Commander format is the largest legal format. The unique-cards limit is what actually drives the request count (one search per unique card per shop = up to 600 requests at 100/6) and exists to keep a single tool call from spawning runaway traffic. Override via `CZ_MTG_MAX_UNIQUE_CARDS` if you genuinely need a bigger list.
- **No price history.** Each query is a fresh snapshot. Track prices yourself if you need it (or open an issue requesting it).
- **Account features cover six of seven shops, with five of them fully cartable.** Full login + cart: `najada` (Djoser/DRF API), `blacklotus` (Shoptet), `tolarie` (Django + jQuery `getJSON` per-product URLs), `rishada` (custom-PHP form POST — `act=20005` against `/`), `cernyrytir` (custom-PHP form POST — `nakupzbozi=Pridat` + `carovy_kod`). Login only: `untap` — cart works against Prestashop but doesn't persist between logins (see Known limitations). Cardmarket has no per-user cart on the free tier and stays out of scope.
- **No automated checkout.** The cart tools stop at "items are in the cart" — finalizing the order, shipping, and payment still happen manually on the shop's website. This is intentional.

---

## Development

The local-clone setup is documented as [Install method D](#install-method-d--from-a-local-clone-development--unreleased-changes) above — that gets the working copy hooked up to Claude Desktop. To run the test suite, install with the `dev` extra and use `pytest`:

```bash
# from inside the cloned repo with the venv activated
pip install -e ".[dev]"

# Fast deterministic tests (~0.5s, 151+ tests)
pytest

# Live smoke tests against real shops + Scryfall (~40s, 10 tests)
pytest -m live --override-ini="addopts="

# Manual MCP smoke test (server speaks stdio; Ctrl+C to exit)
python -m cz_mtg_compare
```

The shop adapters are tested against checked-in HTML/JSON fixtures in `tests/fixtures/`, so the bulk of the test suite is offline and deterministic. Live smoke tests under `tests/test_live_smoke.py` are opt-in via `-m live`.

### Releasing to PyPI

The project ships a `Publish to PyPI` GitHub Actions workflow at `.github/workflows/publish.yml`. It builds an sdist + wheel, smoke-tests the wheel install + entry point in a fresh venv, then publishes via [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC — no API tokens stored anywhere).

**One-time setup** (maintainer only):

1. Create the project on PyPI by going to https://pypi.org/manage/account/publishing/ and adding a new "pending" Trusted Publisher with:
   - **PyPI Project Name**: `cz-mtg-compare-mcp`
   - **Owner**: `xvyslo05`
   - **Repository name**: `czech-mtg-price-comparator`
   - **Workflow filename**: `publish.yml`
   - **Environment name**: `pypi`
2. In the GitHub repo, go to **Settings → Environments → New environment** and create one named `pypi`. (Optional: add required reviewers as a release-gate.)

**Cutting a release**:

```bash
# Bump the version in pyproject.toml, commit it
sed -i '' 's/version = "0.1.0"/version = "0.1.1"/' pyproject.toml
git add pyproject.toml && git commit -m "Release v0.1.1"

# Tag and push — this triggers the workflow
git tag v0.1.1
git push origin main v0.1.1
```

The workflow runs on the tag push: build → smoke test → publish. Verify on https://pypi.org/project/cz-mtg-compare-mcp/. Users can then `uvx cz-mtg-compare-mcp` immediately.

**Manual publish from a local machine** (fallback if Trusted Publishing isn't set up yet):

```bash
pip install -e ".[dev]"
rm -rf dist && python -m build
python -m twine upload dist/*    # prompts for PyPI API token
```

---

## Repo layout

```
src/cz_mtg_compare/
  server.py            MCP entrypoint; thin FastMCP wrappers around the
                       service layer. Registers search_card / optimize_decklist /
                       lookup_card / list_shops + account-feature tools
                       (shop_account_capabilities / shop_login /
                       add_to_cart / view_cart / clear_cart / add_to_watchlist)
  service.py           Transport-agnostic core (CardCompareService). All tool
                       logic lives here — MCP, FastAPI, future hosted MCP,
                       and tests all forward to this.
  web/                 FastAPI delivery surface (optional `[web]` extra).
    app.py             Route definitions, exception handlers, lifespan, middleware.
    schemas.py         Request bodies (responses reuse core pydantic models).
    main.py            `cz-mtg-compare-web` console-script entry point.
    auth_config.py     Cookie names, session TTL, Secure / SameSite knobs.
    auth_schemas.py    Pydantic request bodies for signup / login / verify.
    email_verification.py Token issue/consume + verification URL builder.
    mailer.py          Mailer protocol + LoggingMailer default.
    middleware.py      SessionLoader + CSRF middlewares.
    oauth_config.py    GoogleOAuthSettings (client id/secret/redirect URI).
    oauth_google.py    GoogleOAuthClient protocol + authlib-backed default.
    passwords.py       argon2id hashing wrapper.
    sessions.py        Session create / load / cookie-attach helpers.
  db/                  Database layer (optional `[web]` extra).
    config.py          DatabaseSettings (reads CZ_MTG_DATABASE_URL).
    engine.py          Async engine + session factory + get_session dep.
    models.py          ORM models (Base, User, ...).
  models.py            Offer / Condition / SearchQuery / ShopId
  aggregator.py        async fan-out + per-shop timeouts + cache + get_adapter()
  optimizer.py         decklist optimization (multi-shop split + per-shop bundles)
  decklist.py          Arena/MTGO text parser; ≤100 cards
  scryfall.py          Scryfall lookup with throttle + disk cache
  credentials.py       env-var resolver with 1Password (`op://...`) support
  http_client.py       shared httpx.AsyncClient
  cache.py             TTL cache
  normalize.py         price / stock / condition / foil helpers
  adapters/
    base.py            ShopAdapter ABC + account capability flags
    tolarie.py         (Django; login implemented, cart pending)
    najada.py          (JSON API on wizardshop.cz; login + cart implemented)
    blacklotus.py      (Shoptet HTML + detail-page enrichment)
    cernyrytir.py      (windows-1250 HTML)
    rishada.py         (custom-PHP tabular HTML)
    untap.py           (Prestashop HTML; condition+foil in product reference)
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
