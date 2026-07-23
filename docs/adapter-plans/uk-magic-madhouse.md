# Adapter plan: Magic Madhouse (magicmadhouse)

Verdict: **MEDIUM-HIGH** feasibility. Server-rendered BigCommerce (Stencil); product
data ships in the HTML as a BODL (BigCommerce Open Data Layer) JSON island — no
JS/headless needed. Two real frictions dominate: (a) the search endpoint is
**cross-game** (a query for "lightning bolt" returned **zero** MTG cards in the top 16 —
all Pokémon), so MTG must be filtered by brand and often paginated; and (b) the BODL
listing exposes only a product-level "from" price and (usually null) stock — the
**exact per-condition price + real stock qty** require an extra
`POST /remote/v1/product-attributes/{product_id}` per option, whose option ids must be
scraped from the product page first. Plus GBP currency.

All JSON paths / URLs / request bodies below were fetched **live on 2026-07-21** with a
realistic desktop Chrome UA and verified (BODL decoded, product-attributes POST executed
and its response captured verbatim in §4).

---

## 1. Identity

- `shop_id`: `"magicmadhouse"` — add to the `ShopId` `Literal` **and** the `ALL_SHOPS`
  tuple in `src/cz_mtg_compare/models.py` (both must stay in sync).
- `base_url`: `https://magicmadhouse.co.uk` (**apex, not `www.`**). `www.magicmadhouse.co.uk`
  `301`-redirects to the apex (verified: first hop `301`, final URL `magicmadhouse.co.uk`).
  Pin the apex so the `host_slot("magicmadhouse.co.uk")` semaphore is accurate and no
  redirect hop is wasted.
- Platform: **BigCommerce Stencil**. Store hash `b4ioc4fed9` (appears as
  `window.storeHash`; informational — not needed by the adapter). Storefront is
  multi-game: brands seen in live searches include `Magic: The Gathering`, `Pokemon`,
  `Disney Lorcana`, `Yu-Gi-Oh!`, `Games Workshop`, `Funko`. MTG products carry
  `brand.name == "Magic: The Gathering"` — **filter on this** (see §8).
- Register the class in `src/cz_mtg_compare/adapters/__init__.py`: import
  `MagicMadhouseAdapter`, add to `__all__`, and append `MagicMadhouseAdapter()` to the
  `candidates` list in `build_default_adapters()` (unconditionally, like the CZ shops and
  Axion — it needs no credentials).

## 2. Starting template

`src/cz_mtg_compare/adapters/blacklotus.py` — the closest existing pattern: **HTML +
embedded JSON island**, `HTMLParser` from selectolax, `get_client()` + `host_slot()`,
plus a best-effort **`_enrich_offers()` detail-page fan-out** (`enrich_detail` flag) that
follows product URLs with bounded `asyncio.gather`. Magic Madhouse needs the same
enrichment shape, except the "detail" step is (product page → parse option ids →
`product-attributes` POST) rather than a single GET.

Currency handling mirrors `src/cz_mtg_compare/adapters/cardmarket.py`
(`MKM_EUR_TO_CZK` / `DEFAULT_EUR_TO_CZK` / `eur_to_czk` ctor override /
`int(round(price * rate))`) — see §6.

## 3. Search endpoint

```
GET https://magicmadhouse.co.uk/search.php?search_query=<name>
```
- URL-encode the query; spaces as `+` (e.g. `search_query=ragavan+nimble+pilferer`).
- **16 products per page.** Pagination is `&page=N` (verified: `?search_query=sol+ring&page=2`
  returns a different 16 products). There is **no usable server-side brand facet** in the
  rendered HTML (BigCommerce quicksearch facets are JS-driven) — so narrow to MTG
  **client-side** by `brand.name` and paginate a bounded number of pages (see §10 for the
  cross-game crowding risk).

### Where the product data lives — the BODL island

Near the bottom of the page (server-rendered, one occurrence):
```js
var BODL = JSON.parse("{\"breadcrumbs\":[...],\"products\":[],\"search\":{\"products\":[{...}]},...}");
```
The argument is a **JSON string literal whose contents are themselves JSON** — decode
twice. In Python:
```python
import re, json
m = re.search(r'var BODL = JSON\.parse\((".*?")\);', html, re.DOTALL)
data = json.loads(json.loads(m.group(1)))   # outer: JS-string → text; inner: text → obj
```
On a **search page** the products are at **`data["search"]["products"]`** (a list). The
top-level `data["products"]` is `[]` and `data["categoryProducts"]` is absent/`null` on
search pages — those two are only populated on category/PLP pages. (The recon note about
`categoryProducts` applies to category pages, **not** `search.php`; confirmed empty here.)

## 4. Offer extraction

### Step 1 — BODL listing (one request, gives name/set/foil + a "from" price)

Real MTG product object from `data["search"]["products"]`
(`GET /search.php?search_query=ragavan+nimble+pilferer`, verified):
```json
{
  "id": 557625,
  "sku": "ME-SPM-154U",
  "name": "Sun-Spider, Nimble Webber | Marvel's Spider-Man",
  "url": "https://magicmadhouse.co.uk/magic-the-gathering-marvels-spider-man-sun-spider-nimble-webber?searchid=...&search_query=...",
  "brand": { "name": "Magic: The Gathering" },
  "availability": "Usually dispatched in 24-48 hours.",
  "has_options": true,
  "stock_level": null,
  "low_stock_level": null,
  "custom_fields": [
    { "id": ..., "name": "Language",     "value": "English" },
    { "id": ..., "name": "Creature Type","value": "Spider, Human, Hero" },
    { "id": ..., "name": "Single Cards", "value": "Singles (English)" },
    { "id": ..., "name": "Rarity",       "value": "Uncommon" },
    { "id": ..., "name": "Card Type",    "value": "Legendary, Creature" },
    { "id": ..., "name": "Colour",       "value": "Multicoloured" },
    { "id": ..., "name": "Magic Set",    "value": "Marvel's Spider-Man" }
  ],
  "price": { "with_tax": { "formatted": "£0.40", "value": 0.4, "currency": "GBP" }, "tax_label": "VAT" }
}
```
Notes from the live data:
- **The listing price is a single `price.with_tax.value`, NOT a `price_range` min/max.**
  (The recon's `price_range` assumption did not hold — there is only one value.) For
  `has_options: true` products this value is the **base/"from" price** (cheapest option);
  for `has_options: false` products it is the exact price.
- **Foil is a separate product**, not an option. The foil twin of the above is a distinct
  object: `id: 558094`, `sku: "ME-SPM-B54U"`, `name: "...(Foil) | Marvel's Spider-Man"`,
  `custom_fields["Single Cards"] == "Foils (English)"`. Confirmed again on FF Ragavan
  (`ME-FCA-A43M` foil / `ME-FCA-043M` non-foil) and SLD Sol Ring (`ME-SLD-A2539R` foil /
  `ME-SLD-02539R` non-foil). **SKU encodes foil**: the number segment is `A`-prefixed for
  foil vs `0`/digit-prefixed for non-foil.
- `stock_level` is frequently `null` for `has_options: true` products (stock lives per
  variant); occasionally an int (e.g. `3`, `0`) for fixed-variant products.
- `name` carries a ` | <Set Name>` suffix and may include a `(Foil)` / `[Alt-art]`
  parenthetical; non-breaking spaces (`\xa0`) appear — `unescape` + collapse whitespace.

### Step 2 — exact per-condition price + real stock (the crux: product-attributes POST)

`has_options: true` products expose a **Condition** option on the product page. To get the
exact price and real stock for a condition you must (a) discover the option ids from the
product page, then (b) POST them to the BigCommerce Stencil attributes endpoint.

**2a. Discover option ids** — `GET {product url}` (strip the `?searchid=...&search_query=...`
tracking params). Parse the condition option group (verified markup):
```html
<div class="form-options form-options--condition hide">
  <input class="form-radio quality--nm" type="radio"
         id="attribute_rectangle__264334_442966"
         name="attribute[264334]" value="442966" data-sid="nm" required>
  <label class="form-option quality quality--nm"
         for="attribute_rectangle__264334_442966"
         data-product-attribute-value="442966"
         title="Mint / Near Mint" data-sid="nm">
    <span class="form-option-variant">NM</span>
  </label>
</div>
```
- Attribute (option) id: from `name="attribute[264334]"` → **`264334`** (per-product).
- Each radio → `value` (the option-value id, e.g. **`442966`**) + `data-sid` (e.g. `nm`,
  and generally `ex`/`gd`/`lp`/`pl`/`hp`) + label `title` ("Mint / Near Mint").
- The product form's `<input name="product_id" value="557625">` confirms the product id
  (matches the BODL `id`). **These ids are per-product** — never hardcode them.
- On the sampled card only **NM** was present (shallow ladder — see §10); the mechanism
  generalizes to whatever radios appear.

**2b. Resolve price + stock** — `POST /remote/v1/product-attributes/{product_id}`, form-encoded:
```
POST https://magicmadhouse.co.uk/remote/v1/product-attributes/557625
Headers: X-Requested-With: XMLHttpRequest
         Content-Type: application/x-www-form-urlencoded
         Accept: application/json
         Referer: <product url>
Body:    action=add&product_id=557625&qty%5B%5D=1&attribute%5B264334%5D=442966
         (i.e. action=add & product_id=<id> & qty[]=1 & attribute[<optid>]=<valueid>)
```
**Verbatim response** (`HTTP 200`, `application/json`, verified live):
```json
{"data":{
  "sku":"ESPM-3154U","base":false,"image":null,
  "available_variant_values":[442966],"in_stock_attributes":[442966],
  "selected_attributes":{"264334":442966},
  "stock":null,"available_to_sell":64,"available_on_hand":64,"available_for_backorder":0,
  "instock":true,"purchasable":true,"purchasing_message":null,
  "variantId":441522,"v3_variant_id":998931,
  "price":{"with_tax":{"formatted":"£0.40","value":0.4,"currency":"GBP"},"tax_label":"VAT"}
}}
```
Read from `data`:
- **exact price** → `data.price.with_tax.value` (GBP; VAT-inclusive)
- **real stock qty** → `data.available_to_sell` (int, `64` here) — note `data.stock` is
  `null`; use `available_to_sell` (fall back to `available_on_hand`).
- **in stock?** → `data.instock` / `data.purchasable` (bool)
- variant sku → `data.sku`; variant ids → `data.variantId` (v2) / `data.v3_variant_id`
  (v3) — needed for a future cart call (§9).

This call is **httpx-friendly** (plain form POST, no JS, no auth, no CSRF token needed —
succeeded with just the realistic UA + `X-Requested-With`).

**Tiering (to bound request amplification — see §10).** Emit offers in two modes:
- **MVP / default:** BODL-only. One offer per product using the listing "from" price,
  `stock_qty` from `stock_level` (fallback per §5), `condition = UNKNOWN` (or the single
  ladder value if it can be read cheaply). 1 request per page. Cheap; price is "from" and
  condition is coarse.
- **Enriched (flag `enrich_variants`, bounded to top-K matches):** for `has_options: true`
  products, fetch product page → parse option ids → POST product-attributes **per
  condition** → emit one offer per (product, condition) with exact price + real stock.
  Bounded fan-out under `host_slot`, exactly like blacklotus `_enrich_offers`.

**Fallback if the product-attributes call ever fails** (403/format change): fall back to
the BODL `price.with_tax.value` (the "from" price) with `condition = UNKNOWN` and
`stock_qty` from `stock_level`/availability text. This keeps the adapter degrading
gracefully instead of dropping the shop.

## 5. Field mapping (→ `Offer`)

| Offer field | Source | Notes |
|-------------|--------|-------|
| `shop` | literal `"magicmadhouse"` | |
| `card_name` | BODL `name`, split on `" | "` (take the left part), strip a trailing `(Foil)` / `(Reverse Holo)` / `[...]` parenthetical, `unescape` + collapse `\xa0`/whitespace | e.g. `"Sun-Spider, Nimble Webber \| Marvel's Spider-Man"` → `"Sun-Spider, Nimble Webber"`. Apply `query.name` substring check like najada/blacklotus. |
| `edition` | `custom_fields[name=="Magic Set"].value` | e.g. `"Marvel's Spider-Man"`, `"FINAL FANTASY: Through the Ages"`, `"Secret Lair Drops"`. Falls back to the `name` suffix after `" | "`. |
| `set_code` | middle segment of `sku` (`ME-<CODE>-...`) → `SPM` / `FCA` / `SLD`; uppercase | **Magic Madhouse-internal** set code, not guaranteed to equal the WOTC 3-letter code. Cross-check against `Magic Set`; keep even if it diverges. |
| `condition` | **enriched:** product-page option `data-sid` (→ `normalize_condition`); **MVP:** the single ladder value if present else `Condition.UNKNOWN` | See §7. Do not assume NM. |
| `language` | `custom_fields[name=="Language"].value`, mapped `"English" → "EN"` | Other adapters use `"EN"`; map it rather than storing `"English"`. |
| `foil` | `"(Foil)" in name` **or** `custom_fields["Single Cards"]` contains `"Foil"` **or** SKU number segment is `A`-prefixed | Three concordant signals; use name/`Single Cards` primary, SKU as cross-check. Non-foil twins say `"Singles (English)"` / `"Variants - Non-foil (English)"`. |
| `price_czk` | **enriched:** `product-attributes` `data.price.with_tax.value`; **MVP:** BODL `price.with_tax.value` (the "from" price) → `int(round(gbp * gbp_to_czk))` | VAT-inclusive GBP. See §6. |
| `stock_qty` | **enriched:** `data.available_to_sell` (fallback `available_on_hand`); **MVP:** BODL `stock_level` if int, else `1` when `availability` text implies dispatch/in-stock, else `0` | BODL `stock_level` is often `null` for `has_options` products. Honor `query.in_stock_only` by skipping `stock_qty <= 0` (like najada). |
| `url` | BODL `url` with `?searchid=/search_query=` tracking params stripped | Product page. |
| `shop_ref` | BigCommerce **product id** = BODL `id` (str) | The stable id add-to-cart needs (§9). The precise variant needs product id **+** the option combo (`attribute[264334]=442966`) / `variantId`; the plain product id alone can't disambiguate condition — note this for the cart follow-up. |

## 6. Currency / FX

Prices are **GBP, VAT-inclusive** (`price.with_tax`). `Offer.price_czk` is `int`, convert
at read time — mirror the cardmarket precedent exactly:
- Env var **`MM_GBP_TO_CZK`**, read in `__init__` as
  `float(os.environ.get("MM_GBP_TO_CZK", DEFAULT_GBP_TO_CZK))`, with a documented
  `DEFAULT_GBP_TO_CZK` module constant (~`28.0` CZK/GBP as of 2026-07; named constant so
  it is easy to bump). Allow a constructor override `gbp_to_czk: float | None` for
  testability, exactly like `CardmarketAdapter(eur_to_czk=...)`.
- Conversion: `price_czk = int(round(price_gbp * gbp_to_czk))`.
- **Interim, blocked on the pending multi-currency refactor** (same status as
  `MKM_EUR_TO_CZK`). When the shared currency layer lands, `MM_GBP_TO_CZK` collapses into
  it. Say so in a code comment and the README Limitations bullet.
- Note in code that the price includes UK VAT (20%); no attempt to strip it — the buyer
  pays the shop's listed price.

## 7. Condition normalization

BigCommerce Condition option → `Condition` via `normalize_condition()`
(`src/cz_mtg_compare/normalize.py`). The option exposes both a short `data-sid` and a
human `title`; prefer `data-sid` (compact, stable):

| `data-sid` | label `title` (observed / expected) | `normalize_condition` maps to | In `_CONDITION_MAP` today? |
|------------|--------------------------------------|-------------------------------|-----------------------------|
| `nm` | "Mint / Near Mint" | `NM` | yes (`"nm"`) |
| `ex` | "Excellent" | `EX` | yes (`"ex"`) |
| `gd` | "Good" | `GD` | yes (`"gd"`) |
| `lp` | "Lightly Played" | `LP` | yes (`"lp"`) |
| `pl` | "Played" | `PL` | yes (`"pl"`) |
| `hp` | "Heavily Played" / "Poor" | `HP` | yes (`"hp"`) |

`normalize_condition()` lowercases + strips, so both `data-sid` and the mapped title
resolve. Only `nm` was live-observed (shallow ladder on the sampled card); the map already
covers the expected BigCommerce sids — **no `normalize.py` change expected**. If a live
option surfaces an unmapped sid/label, add that one key in the same PR rather than a
per-adapter map; unknown → `Condition.UNKNOWN`.

## 8. Non-playable filtering

- **Brand gate (adapter-side, mandatory):** keep only products with
  `brand.name == "Magic: The Gathering"`. The store is multi-game and search is
  cross-game; without this gate Pokémon/Lorcana/Yu-Gi-Oh/Funko rows leak into MTG results.
  This is a Magic-Madhouse-specific filter, separate from the central non-playable filter.
- **Central non-playable filter** (`filters.filter_playable()` in the aggregator,
  post-merge) still applies on `card_name`/`edition` (Art Series, oversized, spindown,
  etc.). No adapter work needed beyond populating clean `card_name`/`edition` so its
  regexes can see the tokens. Note Magic Madhouse lists Secret Lair / oversized / promo
  products the central filter should catch.

## 9. Account features (login/cart)

**Defer — ship read-only first.** Keep `supports_login/cart/watchlist = False` (ABC
defaults). Feasibility for a follow-up PR: BigCommerce Stencil exposes an
unauthenticated AJAX cart — `POST /remote/v1/cart/add` (or the legacy
`/cart.php?action=add`) with `action=add&product_id=<id>&qty[]=<n>&attribute[<optid>]=<valueid>`
(the same option-combo the product-attributes POST validated), and a cart cookie tracks
state. We already capture the product id in `shop_ref` and can capture `variantId` /
option ids during enrichment. Two blockers for now: (a) the shared stateless `httpx`
client doesn't persist a per-shop cart cookie/session, and (b) the precise variant needs
the option-id combo, not just the product id. Out of scope for the initial adapter; note
as a possible follow-up (same posture as the Axion plan).

## 10. Risks & blockers

- **Cross-game search crowding (biggest risk).** `search.php` ranks by relevance across
  all games. A query for **"lightning bolt" returned 0 MTG cards in the top 16** — all
  Pokémon "Lightning" energy cards. Generic/colliding words ("lightning", "bolt",
  "shock", "counterspell") may push MTG off page 1 entirely. Mitigation: filter by brand
  **and paginate a bounded number of pages** (e.g. up to 3–4 `&page=N`) collecting only
  `Magic: The Gathering` products, stopping when a page yields `<16` products or enough
  MTG matches. Document that very common/ambiguous names may return incomplete MTG results.
- **Exact price/stock ⇒ request amplification.** Precise per-condition price + real stock
  need product-page GET + product-attributes POST **per (product, condition)**. Against
  `PER_HOST_CONCURRENCY = 3` and `PER_SHOP_TIMEOUT_S = 20`, unbounded enrichment will time
  out. Mitigation: the MVP/enriched tiering in §4 — enrich only top-K matches, bounded
  `asyncio.gather` under `host_slot`, like blacklotus `_enrich_offers`.
- **Option-id mapping brittleness.** `attribute[264334]` and value id `442966` are
  per-product, scraped from HTML; a Stencil theme change to the `form-options--condition`
  markup breaks discovery. Parse defensively (regex `name="attribute\[(\d+)\]"` + radio
  `value`/`data-sid`); fall back to BODL "from" price on parse failure (§4).
- **Shallow condition ladder.** Many singles are effectively single-condition (only `nm`
  observed). When there's one option, the BODL "from" price already equals the exact
  price, so the POST mainly buys accurate stock. Don't assume a fixed ladder; parse what's
  present.
- **Foil = separate product.** Foil and non-foil are distinct products/SKUs/URLs, not one
  product with a foil option. No dedup needed, but the SKU `A`-prefix / `(Foil)` name /
  `Single Cards` field are the only foil signals — trust them, don't expect a foil option.
- **Price is VAT-inclusive GBP** (`price.with_tax`) — intended for a CZ buyer paying the
  listed price; no VAT stripping.
- **SKU set code is MM-internal** (`SPM`, `FCA`, `SLD`) and may not equal the WOTC code —
  keep as a best-effort `set_code`, lean on `Magic Set` for the human edition.
- **UA / bot protection.** `WebFetch`/default UAs got **HTTP 403**; a realistic desktop
  Chrome UA got `200` and full HTML (Cloudflare present — `window.__CF`). The shared
  client UA (`cz-mtg-compare-mcp/0.1 ...`) may be 403'd by this host. **Likely need a
  browser-like `User-Agent`** for `magicmadhouse.co.uk` (per-request header override on
  this adapter's calls, since the shared client sets a CZ-oriented UA). Verify on first
  live run; if 403, set a Chrome-style UA header.

## 11. Tests & fixtures

Mirror `tests/test_najada_adapter.py` / `tests/test_blacklotus_adapter.py` against
checked-in fixtures (offline, no network). Structure the adapter so `parse()` (the ABC
test slot) takes the **search HTML** and produces BODL-only offers, and a separate helper
consumes a saved **product-attributes JSON** for the enrichment path.

Capture into `tests/fixtures/` (regenerate with the commands below, run during recon):
- `magicmadhouse_ragavan.html` — `GET /search.php?search_query=ragavan+nimble+pilferer`
  (contains the BODL island with real MTG products across `Marvel's Spider-Man`, `FINAL
  FANTASY: Through the Ages` + non-MTG brands to exercise the brand gate). Curl:
  ```
  curl -sSL -A "<chrome UA>" -o tests/fixtures/magicmadhouse_ragavan.html \
    "https://magicmadhouse.co.uk/search.php?search_query=ragavan+nimble+pilferer"
  ```
- `magicmadhouse_product.html` — `GET` a `has_options:true` product page (e.g. the
  Sun-Spider non-foil, id `557625`) for the option-id parsing test (`attribute[264334]`,
  value `442966`, `data-sid="nm"`).
- `magicmadhouse_product_attributes.json` — the verbatim `data`-wrapped POST response from
  §4 (exact price + `available_to_sell`).

Tests (parallel to najada's four, plus the enrichment/brand specifics):
1. `test_parses_ragavan_bodl` — from the search fixture: ≥1 offer; every offer
   `shop == "magicmadhouse"`; `price_czk > 0`; `condition in Condition`;
   `url.startswith("https://magicmadhouse.co.uk/")`.
2. `test_brand_gate_excludes_non_mtg` — the search fixture includes non-MTG brands
   (Lorcana / Nimble Co. / Osprey); assert none leak through (all offers came from
   `brand.name == "Magic: The Gathering"`).
3. `test_foil_detection` — the FF Ragavan foil (`ME-FCA-A43M`, name has `(Foil)`,
   `Single Cards == "Variants - Foil (English)"`) → `foil is True`; its non-foil twin
   (`ME-FCA-043M`) → `foil is False`.
4. `test_edition_and_set_code` — `edition == "FINAL FANTASY: Through the Ages"` (from
   `Magic Set`) and `set_code == "FCA"` (from SKU middle segment).
5. `test_price_gbp_converted` — construct the adapter with an explicit `gbp_to_czk`
   override; a BODL `price.with_tax.value` of `0.4` yields `int(round(0.4 * rate))`
   (FX-rate-independent; guards the currency conversion).
6. `test_product_attributes_exact_price_and_stock` — feed
   `magicmadhouse_product_attributes.json` to the enrichment helper: exact
   `price_czk == int(round(0.4 * rate))` and `stock_qty == 64` (from `available_to_sell`),
   `condition == Condition.NM`.
7. `test_in_stock_filter` — `in_stock_only=True` drops `stock_qty <= 0` offers.

Add nothing to `conftest.py` — `load_fixture` already reads any file by name.

## 12. Implementation checklist

1. `models.py`: add `"magicmadhouse"` to the `ShopId` `Literal` **and** `ALL_SHOPS`.
2. New `src/cz_mtg_compare/adapters/magicmadhouse.py` (`MagicMadhouseAdapter(ShopAdapter)`):
   - Constants: `BASE = "https://magicmadhouse.co.uk"`, `SEARCH_URL`,
     `PRODUCT_ATTRS_URL = f"{BASE}/remote/v1/product-attributes/{{pid}}"`,
     `DEFAULT_GBP_TO_CZK`, a browser-like `UA` header (see §10), and the BODL regex.
   - `__init__(self, *, enrich_variants: bool = True, max_pages: int = 3,
     gbp_to_czk: float | None = None)` — read `MM_GBP_TO_CZK` + optional override.
   - `search()`: paginate `search.php` (`&page=N`, bounded by `max_pages`), decode the
     BODL island, keep `brand.name == "Magic: The Gathering"`, build MVP offers; if
     `enrich_variants`, fan out (product page → option ids → product-attributes POST) with
     bounded `asyncio.gather` under `host_slot("magicmadhouse.co.uk")`, capped to top-K.
   - `parse(html, query)` (ABC slot) → BODL-only offers (for fixture tests).
   - Helpers: `_decode_bodl(html)`, `_parse_bodl_product(obj)`, `_parse_options(product_html)`,
     `_resolve_variant(pid, optid, valueid) -> (price_gbp, stock, condition)`,
     `_clean_name`, `_foil_from(sku, name, single_cards)`, `_set_code_from_sku`.
3. `adapters/__init__.py`: import `MagicMadhouseAdapter`, add to `__all__`, append to
   `candidates`.
4. Fixtures: save the three payloads from §11 into `tests/fixtures/`.
5. `tests/test_magicmadhouse_adapter.py`: the seven tests above.
6. `README.md` (**required by CLAUDE.md — user-facing change**):
   - **Supported shops** table: add a `magicmadhouse.co.uk` row — mechanism "BigCommerce
     BODL JSON island + `product-attributes` POST"; covered fields "name, edition, set
     code, condition, foil, stock, price (GBP→CZK)".
   - **Configuration reference** table: add `MM_GBP_TO_CZK` (default `28.0`, interim
     per-shop FX until the multi-currency refactor).
   - Intro / **What this is**: keep the shop count/geography accurate (a second UK shop
     alongside Axion / the CZ shops).
   - **Limitations**: (a) GBP converted at a static configurable rate pending
     multi-currency; (b) exact price/stock require an extra call, so enrichment is bounded
     — very common/ambiguous card names may return incomplete MTG results due to
     cross-game search crowding; (c) condition ladder is often shallow (frequently NM-only).
   - **How it works under the hood**: describe the BigCommerce BODL-island +
     `product-attributes` two-step as a new adapter shape.
7. Verify: `ruff` / `mypy` (or state if not configured) + `pytest tests/test_magicmadhouse_adapter.py`.
8. Confirm the README diff is non-empty before opening the PR (CLAUDE.md gate).

## 13. Effort estimate

**~1.5–2 days.** Medium-high — meaningfully more than Axion (which was clean single-shape
JSON). The extra work here: (1) HTML + double-decoded BODL-island parsing; (2) the
cross-game brand gate + bounded pagination; (3) the two-step exact-price path (product
page → per-product option-id discovery → `product-attributes` POST) with bounded fan-out
and a graceful "from"-price fallback; (4) GBP FX (well-precedented by cardmarket). Nothing
requires JS/headless or auth, and the crux `product-attributes` call is proven working
with a plain httpx form POST. Template `blacklotus.py` (HTML + embedded JSON + enrich
fan-out) is a close structural match. The main schedule risk is the UA/Cloudflare 403
(§10) — a header tweak, but confirm early.
