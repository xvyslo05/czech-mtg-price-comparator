# Adapter plan: Axion Now (axionnow)

Verdict: **HIGH** feasibility. Clean, unauthenticated Shopify JSON. No JS/headless
needed. Only real friction is (a) GBP currency and (b) per-variant stock only being
exposed as a binary `available` flag via the AJAX `.js` endpoint, not the `.json` one.

All JSON paths / URLs below were fetched live on 2026-07-21 with a realistic desktop
UA and verified.

---

## 1. Identity

- `shop_id`: `"axionnow"` — add to the `ShopId` `Literal` **and** `ALL_SHOPS` tuple in
  `src/cz_mtg_compare/models.py` (both lists must stay in sync).
- `base_url`: `https://axionnow.com` (apex, **not** `www.`).
  - `www.axionnow.com` serves `suggest.json` and `/products/{handle}.json` fine, but
    `/products/{handle}.js` **301-redirects `www` → apex**. Pinning the apex host avoids
    a redirect hop and keeps the `host_slot("axionnow.com")` semaphore accurate. Apex
    returns `200` directly on all three endpoints (verified).
- Platform: Shopify storefront + "Card Companion" singles app (vendor field is
  sometimes `"Axion Now"`, sometimes `"Magic: The Gathering"` — do not filter on vendor;
  see §8).
- Register class in `src/cz_mtg_compare/adapters/__init__.py`: import
  `AxionNowAdapter`, add to `__all__`, and append `AxionNowAdapter()` to the
  `candidates` list in `build_default_adapters()` (unconditionally, like the CZ shops —
  it needs no credentials).

## 2. Starting template

`src/cz_mtg_compare/adapters/najada.py` — the closest existing pattern (JSON API, uses
`get_client()` + `host_slot()`, re-uses the `parse()` ABC slot to parse a saved JSON
payload string in tests). Copy its `search()` / `parse()` / `_parse_payload()` shape.

Key structural difference from najada: Axion needs **two HTTP round-trips** —
`suggest.json` (name → product handles) then one `/products/{handle}.js` per handle
(handle → priced+available variants). najada gets everything from one endpoint, so the
class needs an extra fan-out step (bounded concurrency, see §3 / §10).

Currency handling mirrors `src/cz_mtg_compare/adapters/cardmarket.py`
(`MKM_EUR_TO_CZK` / `DEFAULT_EUR_TO_CZK` / `int(round(price * rate))`) — see §6.

## 3. Search endpoint

Two stages. **Do not** bulk-crawl `products.json` (catalog is 2800+ pages at 250/page;
Shopify rate-limits it).

### Stage A — name → handles (Shopify predictive search)

```
GET https://axionnow.com/search/suggest.json
    ?q=<card name>
    &resources[type]=product
    &resources[limit]=10
```
(URL-encode the brackets: `resources%5Btype%5D=product&resources%5Blimit%5D=10`.)

Response shape (verified):
```
resources.results.products[]  → each has:
    handle           e.g. "mtg-singles-clb-lightningbolt-401"
    title            e.g. "Lightning Bolt (401) - CLB"   (set code as " - CLB" suffix)
    price/price_min/price_max   decimal strings, GBP, e.g. "1.14" / "1.78"
    tags[]           e.g. ["boosterfun","CLB","Common","gid://shopify/Collection/...",
                           "Instant","Magic","Phil Stone","Singles"]
    type             "Singles"
    available        product-level bool (true if ANY variant available)
    url, vendor, featured_image
    variants         [] — ALWAYS EMPTY here; variant data requires Stage B
```

Filter the product list before Stage B: keep only products whose `type == "Singles"`
**and** whose `tags` contain `"Magic"` (Axion also sells other TCGs / sealed).
Apply the `query.name` substring check against the `product-description-name` /
title as najada does (`wanted not in name.lower()` → skip). Optionally honor
`query.edition` against the title set-code suffix / tags here to prune Stage-B fetches.

### Stage B — handle → variants (Shopify AJAX product)

```
GET https://axionnow.com/products/{handle}.js
```
Use `.js` (AJAX API), **not** `.json`: only `.js` exposes per-variant `available`.
Fan out over the surviving handles with bounded concurrency
(`asyncio.gather` under `host_slot("axionnow.com")`; cap the handle count, e.g. first
~8 matches, to respect the per-host semaphore of 3 and the 20 s per-shop timeout).

## 4. Offer extraction

From `/products/{handle}.js` (verified live):

Product-level keys: `id, title, handle, tags` (**comma-joined string** here, unlike the
`suggest.json` array), `available, price, price_min, price_max, variants[], options[], url`.

Each `variants[]` entry (real snippet):
```json
{
  "id": 47592596996413,
  "title": "Non-Foil / Near Mint",
  "sku": "cc-clb-lightningbolt-401-nm-nf",
  "price": 120,                      // integer PENCE (120 = £1.20)  ← note: minor units
  "available": false,                // binary stock signal
  "inventory_quantity": null,        // storefront hides exact qty → always null
  "option1": "Non-Foil",             // Card Finish  → foil flag
  "option2": "Near Mint"             // Condition    → Condition enum
}
```
Other variants on the same product: `"Foil / Near Mint"` sku `...-nm-f` price `178`
`available:true`; `"Non-Foil / Excellent"` sku `...-e-nf` price `114` `available:false`.

Note the **price unit differs by endpoint**: `.js` prices are **integer pence**
(`120`); `.json`/`suggest.json` prices are **decimal-string pounds** (`"1.20"`). Since
the adapter reads variants from `.js`, treat `price` as pence → `£ = price / 100`.

Parse **condition + foil from `option1`/`option2`** (cleanest — structured fields), not
from the title string or SKU. Fall back to splitting `title` on `" / "` only if options
are absent. SKU (`cc-<set>-<card>-<cond>-<foil>`, e.g. `-nm-nf`, `-e-nf`, `-nm-f`) is a
secondary cross-check, not the primary source.

The condition ladder is **fuller than the recon feared** — `Near Mint` **and**
`Excellent` both observed on a single product, so do not hardcode NM-only.

## 5. Field mapping (→ `Offer`)

| Offer field | Source | Notes |
|-------------|--------|-------|
| `shop` | literal `"axionnow"` | |
| `card_name` | `suggest.json` `body` → `product-description-name` div, or title with `(nnn) - SET` suffix stripped | Prefer the clean name from suggest.json's `product-description-name`; else regex-strip ` (\d+) - [A-Z0-9]+$` off `title`. |
| `edition` | `suggest.json` `body` → `product-description-set-name` anchor text (e.g. `"Commander Legends: Battle for Baldur's Gate"`) | Full set name only in suggest.json body HTML; `.js` has only the code in tags/title. Capture edition in Stage A and pass it into Stage B. |
| `set_code` | title suffix after `" - "` (e.g. `CLB`), cross-checked against uppercase set-code token in `tags` and the `mtg-singles-<code>-` handle segment | Uppercase it. |
| `condition` | variant `option2` → `normalize_condition()` | See §7. |
| `language` | `None` | Not exposed; Axion is a UK/EN shop — leave `None` (do **not** invent `"EN"`). |
| `foil` | variant `option1` == `"Foil"` (case-insensitive) | `"Non-Foil"` → False. |
| `price_czk` | variant `price` (pence) → `£ = price/100` → `int(round(£ * gbp_to_czk))` | See §6. |
| `stock_qty` | variant `available` → `1` if true else `0` | Binary only; `inventory_quantity` is always `null`. Mirrors cardmarket's `stock_qty=1` convention. When `query.in_stock_only`, drop variants with `available` false (skip when `stock_qty <= 0`, exactly like najada). |
| `url` | `https://axionnow.com/products/{handle}` (optionally `?variant={variant_id}`) | Product page; strip suggest.json's `_pos/_psq/_ss` tracking params. |
| `shop_ref` | `str(variant["id"])` | Shopify variant id (e.g. `"47592596996413"`) — the opaque id an AJAX add-to-cart would need (§9). |

## 6. Currency / FX

Prices are **GBP**. `Offer.price_czk` is `int`, so convert at read time.

- Interim (matches cardmarket precedent exactly): env var **`AXION_GBP_TO_CZK`**, read in
  `__init__` as `float(os.environ.get("AXION_GBP_TO_CZK", DEFAULT_GBP_TO_CZK))`, with a
  documented `DEFAULT_GBP_TO_CZK` constant (pick a current-ish rate, ~28.0 CZK/GBP as of
  2026-07; keep it a named constant so it is easy to bump). Allow a constructor override
  (`gbp_to_czk: float | None`) as cardmarket does for testability.
- Conversion: `price_gbp = variant_price_pence / 100`; `price_czk = int(round(price_gbp
  * gbp_to_czk))`.
- **Multi-currency dependency**: this per-shop FX env var is explicitly interim, blocked
  on the pending multi-currency refactor (same status as `MKM_EUR_TO_CZK`). When that
  lands, `AXION_GBP_TO_CZK` should collapse into the shared currency layer. Note this in
  the code comment and the README Limitations bullet so it is not mistaken for permanent.

## 7. Condition normalization

Card Companion `option2` strings → `Condition` enum via `normalize_condition()`
(`src/cz_mtg_compare/normalize.py`). Coverage check against the existing `_CONDITION_MAP`:

| Axion `option2` | Maps to | In `_CONDITION_MAP` today? |
|-----------------|---------|-----------------------------|
| `Near Mint` | `NM` | yes (`"near mint"`) |
| `Excellent` | `EX` | yes (`"excellent"`) |
| `Good` | `GD` | yes (`"good"`) |
| `Lightly Played` | `LP` | yes (`"lightly played"`) |
| `Played` | `PL` | yes (`"played"`) |
| `Poor` / `Heavily Played` | `HP` | yes (`"poor"` / `"heavily played"`) |

`normalize_condition()` lowercases + strips, so `"Near Mint"` matches. The map already
covers the Card Companion ladder — **no change to `normalize.py` expected**. If a live
variant surfaces a string not in the map (e.g. `"Damaged"`), add that one key in the
same PR rather than inventing a per-adapter map. Unknown → `Condition.UNKNOWN`.

## 8. Non-playable filtering

Handled centrally by `filters.filter_playable()` in the aggregator (post-merge), keyed
on `card_name` / `edition` regexes (Art Series, oversized, spindown, etc.). No
adapter-side work needed. Just make sure `card_name`/`edition` are populated with the
real card + set names (§5) so the central filter can see tokens like "Art Series".
Additionally skip non-singles at Stage A (`type != "Singles"` or no `"Magic"` tag) so
sealed product / other TCGs never enter the pipeline.

## 9. Account features (login/cart)

Defer — ship read-only first. Keep `supports_login/cart/watchlist = False` (ABC
defaults). Shopify does expose an unauthenticated AJAX cart (`POST /cart/add.js` with
`{"items":[{"id": <variant_id>, "quantity": n}]}`, `GET /cart.js`, `POST
/cart/clear.js`), and we already capture the variant id in `shop_ref`, so a cart-only
implementation (no login) is feasible in a **follow-up PR** — but cart state lives in a
Shopify session cookie, which the shared stateless `httpx` client doesn't persist per
shop today. Out of scope for the initial adapter; note as a possible follow-up.

## 10. Risks & blockers

- **Stock is binary only.** `.js` gives `available` true/false; `inventory_quantity` is
  `null` (storefront hides it). `stock_qty` can only ever be `0` or `1`. Acceptable
  (cardmarket already does `stock_qty=1`), but document it in README Limitations.
- **Rate limits.** `suggest.json` and `.js` are lightweight and fine. `products.json`
  bulk crawl is the danger (Shopify throttles it); the two-stage design avoids it. The
  Stage-B fan-out must stay bounded — cap handles fetched per search (~8) and rely on the
  `PER_HOST_CONCURRENCY = 3` semaphore + `PER_SHOP_TIMEOUT_S = 20` ceiling.
- **N+1 latency.** One `.js` fetch per matching handle. Cap the handle count and gather
  concurrently; worst case is bounded by the 20 s per-shop timeout (aggregator drops the
  shop's results on timeout without failing the whole search).
- **Condition-ladder depth** unknown per product (some cards may only have NM). Parse
  whatever `option2` values appear; don't assume a fixed ladder.
- **www → apex 301** on `.js` (see §1) — pin apex.
- **Two price units** (pence in `.js` vs pound-strings elsewhere) — a real footgun; the
  adapter reads `.js`, so treat `price` as pence. Guard with a test.
- **UA sensitivity.** Requests succeeded with a realistic desktop UA. The shared client
  UA (`cz-mtg-compare-mcp/0.1 ...`) may be treated differently by Shopify/Cloudflare; if
  live calls 403, the fix is a browser-like UA (raise separately if observed — endpoints
  were reachable during recon).

## 11. Tests & fixtures

Mirror `tests/test_najada_adapter.py` against checked-in fixtures (offline, no network).

Capture two fixtures into `tests/fixtures/` (already fetched live, reusable):
- `axionnow_lightning_bolt_suggest.json` — output of
  `GET /search/suggest.json?q=Lightning Bolt&resources[type]=product&resources[limit]=10`.
- `axionnow_lightning_bolt_product.js.json` — output of
  `GET /products/mtg-singles-clb-lightningbolt-401.js` (save the `.js` body as a `.json`
  file; it is valid JSON). This one has the priced+available+condition-ladder variants.

Because the adapter is two-stage, structure `parse()`/helpers so tests can drive each
stage from a fixture string without HTTP:
- Give the adapter a `_parse_suggest(payload_str, query) -> list[handle+edition]` and a
  `_parse_product(js_payload_str, card_name, edition, set_code, query) -> list[Offer]`,
  and have `parse()` (the ABC test slot) route to `_parse_product` (the offer-producing
  stage) so existing fixture-test ergonomics hold.

Tests (parallel to najada's four):
1. `test_parses_lightning_bolt_variants` — from the product fixture: ≥1 offer;
   `shop == "axionnow"`; `"lightning bolt" in card_name.lower()`; `price_czk > 0`;
   `condition in Condition`; `url.startswith("https://axionnow.com/products/")`.
2. `test_in_stock_filter` — `in_stock_only=True` yields a subset with all
   `stock_qty > 0` (the fixture has both `available:true` and `available:false` variants,
   so the counts must differ).
3. `test_foil_and_condition_parsed` — asserts at least one `foil` offer (`Foil / Near
   Mint`) and at least one non-NM condition (`Excellent` → `Condition.EX`) come through.
4. `test_set_code_from_title` — `set_code == "CLB"` resolved from the `- CLB` suffix.
5. `test_price_is_pence_gbp_converted` — a variant `price:120` (pence) with a fixed
   injected `gbp_to_czk` yields `round(1.20 * rate)` (guards the pence-vs-pounds trap).
   Construct the adapter with an explicit `gbp_to_czk` override so the test is
   FX-rate-independent.
6. `test_suggest_maps_name_to_handles` — `_parse_suggest` on the suggest fixture returns
   the CLB handle and filters out non-`Singles` / non-`Magic` entries.

Add nothing to `conftest.py` — `load_fixture` already reads any file by name.

## 12. Implementation checklist

1. `models.py`: add `"axionnow"` to `ShopId` `Literal` and `ALL_SHOPS`.
2. New `src/cz_mtg_compare/adapters/axionnow.py` (`AxionNowAdapter(ShopAdapter)`):
   constants (`SHOP_BASE = "https://axionnow.com"`, `SUGGEST_ENDPOINT`,
   `DEFAULT_GBP_TO_CZK`); `__init__` reads `AXION_GBP_TO_CZK` + optional override;
   `search()` → Stage A `suggest.json`, filter to Magic singles, fan out Stage B `.js`
   with bounded `asyncio.gather` under `host_slot("axionnow.com")`; `_parse_suggest`,
   `_parse_product`, `parse()` (ABC slot → `_parse_product`); helpers to strip the
   `(nnn) - SET` title suffix and to parse foil/condition from `option1`/`option2`.
3. `adapters/__init__.py`: import, add to `__all__`, append to `candidates`.
4. Fixtures: save the two payloads captured during recon into `tests/fixtures/`.
5. `tests/test_axionnow_adapter.py`: the six tests above.
6. `README.md` (**required by CLAUDE.md — user-facing change**):
   - **Supported shops** table: add `axionnow.com` row — mechanism "Shopify JSON
     (`suggest.json` + per-product `.js`)", covered fields "name, edition, set code,
     condition, foil, stock (binary), price (GBP→CZK)".
   - **Configuration reference** table: add `AXION_GBP_TO_CZK` (default ~28.0, interim
     per-shop FX until multi-currency lands).
   - Intro / **What this is**: bump "six major Czech shops" wording to include a UK shop
     (Axion Now is UK, not CZ — reword so the count/geography stays accurate).
   - **Limitations**: note (a) Axion stock is binary (in/out, no exact qty), (b) GBP
     converted at a static configurable rate pending the multi-currency refactor,
     (c) no language field (EN assumed, left blank).
   - **How it works under the hood**: mention the two-stage Shopify lookup
     (suggest → per-handle `.js`) as a new adapter shape.
7. Verify: `ruff`/`mypy` (or state if not configured) + `pytest tests/test_axionnow_adapter.py`.
8. Confirm the README diff is non-empty before opening the PR (CLAUDE.md gate).

## 13. Effort estimate

**~0.5–1 day.** Low-to-medium. The Shopify JSON is clean and fully mapped above;
najada is a near-drop-in template. The only genuinely new work vs. existing adapters is
the two-stage fan-out (suggest → per-handle `.js`) and the GBP/pence conversion — both
small and well-precedented (cardmarket FX, najada JSON parsing). Fixtures are already
captured. No auth, no HTML scraping, no headless browser.
