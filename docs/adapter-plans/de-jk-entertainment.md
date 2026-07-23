# Adapter plan: JK Entertainment (jkentertainment)

> Recon status: **verified live** on 2026-07-21 against `https://www.jk-entertainment.de`
> (browser UA *and* the project's default bot UA both return HTTP 200 with the full
> embedded dataLayer — no blocking, no JS execution required). Verdict: **HIGH**.

## 1. Identity

| Field | Value |
|-------|-------|
| `shop_id` | `jkentertainment` |
| Display name | JK Entertainment |
| Country | Germany (DE) |
| `base_url` | `https://www.jk-entertainment.de` (note: `.de`, **not** `.biz`) |
| Currency | **EUR** (needs FX conversion — see §6) |
| Platform | **Shopware 6** (server-rendered) + a "Maxia Listing Variants" plugin |
| Rendering | Server-rendered HTML carrying an inline GA4 `dataLayer` JSON block — fully parseable with httpx + selectolax, no headless browser |
| Catalog scope | **Multi-TCG** shop (MTG, Yu-Gi-Oh, Pokémon, …). The search endpoint is global; MTG is one game among several — see §8 for game filtering |

## 2. Starting template

Copy **`adapters/najada.py`** for the overall shape (a `parse()` that consumes a
machine-readable payload; `search()` fetches then delegates) and lift the
embedded-JSON + DOM-selector idiom from **`adapters/blacklotus.py`** (regex-extract a
JSON blob out of `<script>`, then use selectolax for the bits the JSON doesn't carry).

What changes vs najada:
- Source is **HTML with one embedded JSON blob** (`onEventDataLayer`), not a REST endpoint. `search()` does a single `GET`, `parse()` runs a regex to pull the JSON string, `json.loads` it, then reads `ecommerce.items[]`.
- URL + `shop_ref` are **not** in the JSON — they come from the DOM (`a.product-name` hrefs), zipped to the JSON items by list index (see §4).
- Prices are **EUR floats** → multiply by an FX rate (see §6), unlike najada's native `*_czk` fields.

## 3. Search endpoint

- **Method / URL:** `GET https://www.jk-entertainment.de/search?search=<term>`
- **Param:** `search` — the raw query, standard `application/x-www-form-urlencoded` quoting (space → `+` or `%20`, both work). Build with `urllib.parse.urlencode({"search": query.name})`.
- **Headers:** none special required. Verified that the shared client's default bot UA (`cz-mtg-compare-mcp/0.1`) and its `Accept-Language: cs-CZ` both return a full page. No cookie/CSRF needed for read-only search.
- **Host slot:** `host_slot("jk-entertainment.de")`.
- **Example (Lightning Bolt):** `https://www.jk-entertainment.de/search?search=Lightning+Bolt`
- **Pagination:** Shopware default is **24 items/page** (verified: "Sol Ring" returned 24 items on page 1). Additional pages use the `p` query param (`?search=<term>&p=2`); Shopware's AJAX incremental loader points at `dataUrl: /widgets/search`. **MVP fetches page 1 only** (24 results is ample for a single-card lookup and matches najada's single-request behaviour); deeper pagination is a documented follow-up.

## 4. Offer extraction

**Primary source — inline GA4 dataLayer.** The page contains exactly one block:

```js
var onEventDataLayer = JSON.parse('{"event":"view_item_list","ecommerce":{"item_list_id":"search","items":[ {…}, {…} ]}}');
```

- **JS variable:** `onEventDataLayer`
- **Extraction:** regex-capture the single-quoted string argument of `JSON.parse('…')`, then decode the JS string escapes and parse. In Python: `m = re.search(r"var onEventDataLayer = JSON\.parse\('(.*?)'\);", html, re.DOTALL)`, then `data = json.loads(m.group(1).encode().decode("unicode_escape"))`. Guard on `data.get("event") == "view_item_list"`.
- **JSON path to product list:** `data["ecommerce"]["items"]` (a list).
- **String fields are HTML-entity-encoded** (e.g. `&#039;`, `&amp;`) — run `html.unescape()` on `item_name` / `item_variant` before use.

**Real observed item (verified, "Lightning Bolt"):**
```json
{
  "item_id": "6061#GD#GE#nFO#nFI",
  "item_name": "Lightning Bolt - Fourth Edition",
  "price": 1.63,
  "item_variant": "R;German;Instant;Nonfoil;Fourth Edition;Good;1;Common",
  "item_category": "MTG#Singles",
  "item_brand": "",
  "item_list_name": "Search"
}
```

**`item_id` is the reliable, positional key** — `#`-split into 5 fields:
`<productId>#<CONDITION>#<LANGUAGE>#<FOIL>#<FIRSTEDITION>`
e.g. `6061#GD#GE#nFO#nFI` → productId `6061`, cond `GD`, lang `GE`, `nFO` (non-foil), `nFI` (not 1st-ed, a Yu-Gi-Oh-only concept, always `nFI` for MTG).

⚠️ **Do NOT parse `item_variant` positionally.** Its field order is **not stable** across
items (observed `R;German;Instant;Nonfoil;Fourth Edition;Good;…` vs
`R;Instant;Nonfoil;English;Near Mint;…`). Use `item_id` for condition/language/foil and
`item_name` for name/edition. `item_variant` is at most a fallback keyword source.

**Secondary source — DOM (for URL + shop_ref, which the JSON lacks).**
Each result is a `div.card.product-box.box-minimal` containing `a.product-name[href]`.
The href is the detail URL, e.g.
`https://www.jk-entertainment.de/detail/4dcc055acc3a486780f46440d12cdb55` — a 32-hex
Shopware product UUID (the numeric `productId` from `item_id` is **not** in the URL, so the
URL must come from the DOM). The same UUID appears in the add-to-cart form as
`name="lineItems[<uuid>][id]" value="<uuid>"`.
Collect `tree.css("a.product-name")` in document order and **zip by index** with the JSON
`items` (both are in listing order; item objects also carry `"index": 0,1,…`). If the two
lists differ in length, fall back to `url = <search-page URL>` (as najada does) and leave
`shop_ref = None`.

Verified counts line up: "Lightning Bolt" → 2 JSON items and 2 `a.product-name` anchors
("2 Produkt" shown on page).

## 5. Field mapping (→ Offer)

| Offer field | Source | Notes |
|-------------|--------|-------|
| `card_name` | `item_name.rsplit(" - ", 1)[0]`, then `.strip()` | JK pads a trailing space ("Lightning Bolt - Fourth Edition "); collapse whitespace |
| `edition` | `item_name.rsplit(" - ", 1)[1]` if present, else `item_category.split("#")[0]` if it isn't the generic `"MTG"` token, else `None` | `rsplit` handles the rare card-name-contains-hyphen case |
| `set_code` | **unavailable** | dataLayer has no 3-letter code → `None` (degrade; matches tolarie/blacklotus) |
| `condition` | `item_id` field[1] via `normalize_condition()` | `NM/EX/GD/…` codes (see §7) |
| `language` | `item_id` field[2], mapped `EN→English, GE→German, FR→French, IT→Italian, SP→Spanish, PT→Portuguese, JP→Japanese, RU→Russian, KR→Korean, CN→Chinese`; else raw code | Informational only |
| `foil` | `item_id` field[3] == `"FO"` → `True`; `"nFO"` → `False` | Live sample was all `nFO`; `FO`/`nFO` naming confirmed by recon MTG example |
| `price_czk` | `round(float(item["price"]) * eur_to_czk)` | `item["price"]` is a clean EUR float (1.63); no string parsing needed. FX per §6 |
| `stock_qty` | **not exposed in listing** → degrade to `1` for every listed offer | JK lists only purchasable items; no per-listing count found. `in_stock_only` then keeps all results. Document as limitation |
| `url` | DOM `a.product-name` href at matching index; else search-page URL | 32-hex `/detail/<uuid>` |
| `shop_ref` | the `<uuid>` from the detail href / `lineItems[<uuid>]` input | Kept for a future Shopware cart; `None` if index-zip fails |

Apply the same in-adapter guards najada/untap use: skip if `query.name.lower()` not in
`card_name.lower()`; apply `edition` filter against `edition`; if `query.in_stock_only` drop
`stock_qty <= 0` (a no-op given the degrade, but keep it for symmetry).

## 6. Currency / FX

- **Currency: EUR.** `Offer.price_czk` is an `int` CZK, so prices must be converted.
- **Interim approach (mirrors the only precedent, cardmarket's `MKM_EUR_TO_CZK`):** add a
  per-shop env var **`JK_EUR_TO_CZK`** (default `24.5`, same default as MKM). Resolve it in
  `__init__` exactly like `CardmarketAdapter`:
  `self._eur_to_czk = eur_to_czk if eur_to_czk is not None else float(os.environ.get("JK_EUR_TO_CZK", 24.5))`.
  `price_czk = int(round(price_eur * self._eur_to_czk))`.
- **Dependency / flag:** this is a **stopgap**. A general multi-currency refactor is pending
  (store native `price` + `currency` on `Offer`, convert centrally). When that lands,
  `JK_EUR_TO_CZK` and `MKM_EUR_TO_CZK` should both be retired in favour of the shared
  mechanism. Call this out in the PR description so the two per-shop FX vars are migrated
  together.

## 7. Condition normalization

JK's condition codes are the 2-letter tokens in `item_id` field[1]. Verified live: `NM`,
`EX`, `GD` (and `NN` = "Near Mint Neuware", a Yu-Gi-Oh-only sealed grade). The remaining
standard Shopware-TCG grades (`MT`, `LP`, `PL`, `PO`) weren't hit by the sampled staples but
follow the same scheme. `normalize_condition()` lowercases its input, so add these keys to
`_CONDITION_MAP` in `normalize.py` (the rest already resolve):

```python
"mt": Condition.NM,   # Mint            (new)
"po": Condition.HP,   # Poor            (new)
"nn": Condition.NM,   # Near Mint Neuware (YGO sealed; harmless for MTG) (new)
# already present and correct: "nm"→NM, "ex"→EX, "gd"→GD, "lp"→LP, "pl"→PL, "mp"→PL, "hp"→HP
```

Unknown/unmapped codes fall through to `Condition.UNKNOWN` (safe). The condition **words**
in `item_variant` (`Near Mint`, `Excellent`, `Good`, …) are all already in the map, so if a
future fallback ever reads them it will Just Work.

## 8. Non-playable filtering

- **Reuse `filters.py` as-is** — the aggregator already calls `filter_playable()` on the
  merged results, so Art Series / oversized / tip cards are dropped centrally. No adapter
  change needed there.
- **Game (TCG) filtering is the JK-specific concern.** The search is multi-TCG. There is
  **no single reliable "this is MTG" field**: `item_category` is `MTG#Singles` for some MTG
  cards but a bare set name for others (`Modern Horizons 2#Singles`, `Kaladesh#null`,
  `Archenemy Nicol Bolas#Singles`), and Yu-Gi-Oh uses `YGO Einzelkarten`, `Speed Duel: …`,
  `Hidden Arsenal 3#null`, etc.
  - **Primary guard (already standard in every adapter):** require `query.name` as a
    substring of `card_name`. This alone eliminates the observed cross-TCG noise — searching
    "Sol Ring" returned Yu-Gi-Oh "…Millennium Ring" cards, none of which contain "sol ring",
    so they're dropped for free.
  - **Optional secondary guard (recommended, low-maintenance):** drop items whose
    `item_category` matches a small non-MTG denylist regex, e.g.
    `(?i)\b(YGO|Yu-?Gi-?Oh|Pok[eé]mon|One Piece|Digimon|Lorcana|Flesh\s*and\s*Blood|Speed Duel|Weiss|Vanguard)\b`.
    Cheap insurance against an identically-named card in another game.

## 9. Account features (login / cart)

- **Defer. Read-only search is the MVP.** Set `supports_login = supports_cart =
  supports_watchlist = False`; inherit the `AccountFeatureNotSupported` defaults from
  `ShopAdapter`.
- **Feasibility note for a follow-up:** Shopware has a standard, well-understood cart flow —
  `POST /checkout/line-item/add` with `lineItems[<uuid>][id]=<uuid>`,
  `lineItems[<uuid>][quantity]=N`, `lineItems[<uuid>][type]=product`, plus the Shopware
  `_csrf_token` (rendered inline, same grep-it-out pattern as untap's `static_token`) and a
  session cookie from login. `shop_ref` already captures the `<uuid>`, so wiring cart later
  is tractable — but out of scope for this PR.

## 10. Risks & blockers (ranked)

1. **Sibling-variant completeness (medium).** The `view_item_list` dataLayer lists **one
   representative offer per product-box** (its default variant), not every condition/language.
   Live proof: a box's default was `1,63 €` (GD/German) while the DOM also showed
   `Varianten ab 1,36 €` — a cheaper variant that is **not** in the dataLayer. Those sibling
   variants load via a JS-gated AJAX call (`POST /maxia-variants/product`, config in
   `window.maxiaListingVariants`). With no JS we capture the default only. **Acceptable for
   an MVP price comparator** (one offer per card/set), but must be documented, and is the
   natural next enhancement (replicate the AJAX call, à la blacklotus detail enrichment).
2. **Multi-TCG false positives (low).** Handled by name-substring + optional denylist (§8);
   residual risk only for a non-MTG card sharing an MTG card's exact name.
3. **Interim FX drift (low).** Hard-coded `JK_EUR_TO_CZK` rate goes stale; same known
   trade-off as cardmarket. Superseded by the pending multi-currency refactor.
4. **`item_variant` order instability (mitigated).** Real and confirmed, but avoided entirely
   by sourcing condition/lang/foil from `item_id`.
5. **Template drift (low).** A Shopware theme change could rename `a.product-name` or drop
   the dataLayer. Regex + selector are isolated and fixture-tested, so breakage is loud and
   local. No anti-bot blocking observed.

## 11. Tests & fixtures

Capture fixtures with the browser-or-bot UA (both verified) and save the **raw HTML** under
`tests/fixtures/`:

| Fixture file | Capture query | Covers |
|--------------|---------------|--------|
| `jkentertainment_lightning_bolt.html` | `GET /search?search=Lightning+Bolt` | Happy path: 2 MTG offers, condition codes `GD`/`NM`, languages `GE`/`EN`, edition split from `item_name`, EUR→CZK conversion, URL/shop_ref from DOM |
| `jkentertainment_counterspell.html` | `GET /search?search=Counterspell` | Larger multi-offer result (8 items) — name filter + multiple editions |
| `jkentertainment_sol_ring.html` | `GET /search?search=Sol+Ring` | **Multi-TCG page** (24 items incl. Yu-Gi-Oh "…Ring" cards) — asserts name-substring + game denylist drop the non-MTG entries |

Add **`tests/test_jkentertainment_adapter.py`**, mirroring `tests/test_untap_adapter.py`
(uses the `load_fixture` fixture from `conftest.py`; `parse()` is called directly, no
network). Cases:
- `test_parses_lightning_bolt_fixture` — offers non-empty; each `shop == "jkentertainment"`, `"lightning bolt" in card_name.lower()`, `price_czk > 0`, `url.startswith("https://www.jk-entertainment.de/")`.
- `test_condition_and_foil_from_item_id` — GD→`Condition.GD`, NM→`Condition.NM`; `foil is False` for `nFO`.
- `test_edition_split_from_item_name` — "Lightning Bolt - Fourth Edition" → `edition == "Fourth Edition"`, name has no `" - "`.
- `test_eur_to_czk_conversion` — construct `JkEntertainmentAdapter(eur_to_czk=25.0)`; assert `price_czk == round(1.63 * 25.0)` for the known item.
- `test_language_mapping` — `GE→"German"`, `EN→"English"`.
- `test_multi_tcg_filtered_out` (Sol Ring fixture) — no returned offer is a Yu-Gi-Oh card (name-substring + denylist); all returned names contain "sol ring".
- `test_edition_filter` — `SearchQuery(name="Lightning Bolt", edition="Fourth")` keeps only Fourth Edition.
- `test_shop_ref_is_uuid` — `shop_ref` is a 32-hex string matching the detail URL.

Also register the new shop in the parametrized suites already covering all adapters:
`test_adapter_robustness.py`, `test_adapters_http.py`, `test_shop_optout.py`,
`test_aggregator.py` (grep for `ALL_SHOPS` / the adapter list and add `jkentertainment`).

## 12. Implementation checklist (ordered)

1. **`models.py`** — add `"jkentertainment"` to the `ShopId` `Literal` **and** to `ALL_SHOPS`.
2. **`normalize.py`** — add `"mt"`, `"po"`, `"nn"` keys to `_CONDITION_MAP` (§7).
3. **`adapters/jkentertainment.py`** — new `JkEntertainmentAdapter(ShopAdapter)`:
   `shop_id="jkentertainment"`, `base_url="https://www.jk-entertainment.de"`, all capability
   flags `False`; `__init__(self, *, eur_to_czk: float | None = None)` resolving
   `JK_EUR_TO_CZK`; `search()` (single GET via `get_client()` + `host_slot`), `parse()`
   (regex → `json.loads` → `ecommerce.items`, zip with `a.product-name` hrefs), field mapping
   per §5, game denylist per §8.
4. **`adapters/__init__.py`** — import `JkEntertainmentAdapter`, add to `__all__`, append to
   the `candidates` list in `build_default_adapters()` (unconditionally — no credentials
   needed, unlike cardmarket).
5. **`aggregator.py` registry** — no change needed (it iterates `build_default_adapters()`);
   confirm `get_adapter("jkentertainment")` resolves.
6. **Fixtures** — add the three HTML files in §11.
7. **Tests** — add `test_jkentertainment_adapter.py`; extend the all-adapter parametrized
   suites (§11).
8. **README.md** (required by CLAUDE.md — user-facing change):
   - **Supported shops** table: new row —
     `` | `jk-entertainment.de` | HTML scrape (Shopware GA4 dataLayer) | name, edition, condition, language, foil, price (EUR→CZK); one offer per listing (cheapest variant) | ``.
   - **Configuration reference** table: new row —
     `` | `JK_EUR_TO_CZK` | EUR → CZK conversion rate for JK Entertainment prices | `24.5` | ``.
   - **What this is** / intro: bump "six major Czech shops" phrasing to acknowledge a German
     shop is now included (or add a bullet: "Include German shop **JK Entertainment** for EU
     pricing on cards Czech shops don't carry").
   - **Limitations**: add bullets — (a) "JK Entertainment prices are EUR, converted with a
     fixed `JK_EUR_TO_CZK` rate (interim, pending multi-currency support)"; (b) "JK
     Entertainment returns one offer per listing — the default/cheapest variant; other
     conditions/languages of the same card exist behind a JS-only variant selector and aren't
     captured"; (c) "JK Entertainment is multi-TCG; non-MTG cards are filtered by name match +
     a game denylist".
   - **How it works under the hood**: add `jkentertainment` to the adapter row/diagram
     (line ~577) and to the **Repo layout** adapter list (line ~854).
9. **Config env vars** — `JK_EUR_TO_CZK` is the only new one (documented in step 8). No
   credentials.
10. **Verify** — run the fast test suite; confirm `list_shops` includes `jkentertainment`;
    confirm the README diff is non-empty (CLAUDE.md rule).

## 13. Effort estimate

**Effort: M (Medium).** One new adapter closely following the najada+blacklotus template,
three fixtures, one focused test module + parametrized-suite registrations, a mechanical FX
wire-up cloned from cardmarket, and the README edits. No new dependencies, no auth, no JS.
The only non-trivial logic is the dataLayer regex/JSON decode and the DOM↔JSON index-zip,
both fully specified above with verified real data.

**Plan confidence: High.** Search endpoint, JSON shape, `item_id` structure, condition
codes, DOM selectors, pagination, prices, currency, and UA behaviour were all verified
against the live site. The one genuinely open item — sibling-variant completeness — is
understood, bounded, and consciously deferred (documented as a limitation, not a blocker).
