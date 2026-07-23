# Adapter plan: Magic Store (magicstore)

> Verdict: **MEDIUM**. Effort: **~0.5–1 day** for the read-only adapter (account
> features deferred). Top risk (B2B price gating) is **RESOLVED** — consumer
> prices, stock, cart ids and product URLs are all visible to anonymous
> requests. The real headache is **Italian-localized card names**, not pricing.

All facts below were verified against live HTML fetched 2026-07-21 with a
realistic desktop UA:

```
GET https://www.magicstore.it/ricerca.php?q=lightning+bolt&id_cat=9
→ 200, UTF-8, ~148 KB, 30 result rows, prices present, no login.
```

---

## 1. Identity

- `shop_id`: **`magicstore`**
- Shop: **Magic Store** — Italian hobby/TCG retailer (est. 1999), online + physical.
- **Domain correction:** the originally-listed **`magistore.it` is DEAD (NXDOMAIN / dead DNS).**
  The real shop is **`magicstore.it`** — canonical host **`https://www.magicstore.it`**.
  Every URL in this plan uses `www.magicstore.it`.
- Platform: **custom PHP on Apache.** Session cookie **`ms_sid`** (confirmed set on
  first GET). `.php` routing: `ricerca.php` (search), `carrello/update.php`
  (cart add), `areaclienti/` (customer login area), `carte_singole.php`,
  `ritiro_carte.php` (buyback). B2B/distributor portal lives on a **separate
  domain** `msdistribuzione.it/areaclienti` — irrelevant to us.
- Rendering: **fully server-rendered PHP**, content inline. No JS / headless needed.
- **Encoding: UTF-8** (`<meta charset=utf-8>`). Unlike cernyrytir (windows-1250),
  **no `resp.encoding` override is required.** `&euro;` / `&gt;` entities are
  HTML-escaped; use `html.unescape`.
- Currency: **EUR.**

## 2. Starting template

Clone **`src/cz_mtg_compare/adapters/rishada.py`** — it is the closest fit:
- GET-based search (`client.get(url)`), single results page, selectolax parse.
- `_parse(html, url, query)` split out so `parse()` can drive fixtures.
- Per-row cart-id captured into `shop_ref`; NM default condition.

Borrow the **EUR→CZK constructor pattern** from
`src/cz_mtg_compare/adapters/cardmarket.py` (`self._eur_to_czk`, env override,
`int(round(price_eur * rate))`).

Do **NOT** copy rishada's `wanted not in offer.card_name.lower()` name filter —
see §4 / §10 (Italian names).

## 3. Search endpoint

```
GET https://www.magicstore.it/ricerca.php?q=<query>&id_cat=9
```
- `q` — url-encoded search string (spaces → `+`). The server does its own
  fuzzy/alias matching (an English query `lightning bolt` returns Italian rows
  like `FULMINE`).
- **`id_cat=9` = Magic single cards** (`carte_singole_magic`). **REQUIRED.**
  Verified: without `id_cat`, results are polluted with other games
  (a no-cat `lightning bolt` search returned 44 Magic + 17 Yu-Gi-Oh rows).
  Category map observed in the home nav: `9`=Magic, `10`=Yu-Gi-Oh, `12`=Lorcana,
  `13`=Pokémon/SWU singles.
- **Pagination:** footer renders
  `<div class="pagination"><div class="results">Prodotti da 1 a 30 su 30 (1 Pagina)</div></div>`.
  Page size ≈ **30**. The `lightning bolt` query fit in one page. The
  multi-page query param (likely `&pag=` / `&p=`) is **UNCONFIRMED** — no
  >30-result sample was captured. MVP: parse page 1 only (sufficient for
  single-card lookups); the `.results` text ("… su N …") tells you when N>30 so
  a follow-up can add paging.
- Encoding UTF-8, 200 anonymous, `~148 KB`.

Build the URL with `urllib.parse.urlencode({"q": query.name, "id_cat": 9})`.

## 4. Offer extraction

**Prices are visible anonymously — no B2B gate.** Parse the results HTML
directly (no detail-page follow-up needed): name, set, price, stock qty, cart
id and product URL are all in the results row.

Row container: **`div.s_item.clearfix`** (30 per page). Meaningful content sits
in the inner **`div.grid_10.omega`**. Real snippet (whitespace-collapsed):

```html
<div class="s_item clearfix">
  <div class="grid_2 alpha">
    <a class="s_thumb" href="https://www.magicstore.it/-/fulmine-15619">
      <img src="…/ms_img_default_w60.gif" title="FULMINE" alt="FULMINE"></a>
  </div>
  <div class="grid_10 omega">
    <h3><a href="https://www.magicstore.it/-/fulmine-15619">FULMINE</a></h3>
    <p class="subcat-rel"><span>
      <a href="…/__/carte_singole_magic/">Magic</a> &gt;
      <a href="…/_/magic-the-gathering-avatar-the-last-airbender-eternal/">Magic: The Gathering | Avatar: The Last Airbender: Eternal</a>
    </span><br></p>
    <p>M - nr 32&nbsp; &nbsp;<img src="…/colori_magic/color_R.gif" alt="R"></p>
    <p class="s_price s_price"><span class="s_currency s_before">€</span>4.00 </p>
    <div style="text-align:center;float:right;padding-right:7px">
      <img src="…/semaforo_1.gif" alt="Disponibilità limitata"> (1)</div>
    <p></p>
    Qta: <input type="text" class="box-qta" id="product_buy_quantity_152269" value="1" size="3">
    <a class="s_button_add_to_cart" href="https://www.magicstore.it/carrello/update.php?id=152269"
       rel="152269"><span class="s_icon_16"><span class="s_icon"></span>Compra</span></a>
  </div>
</div>
```

Per-row selectors:
- **name**: `h3 a` text (fallback `a.s_thumb img[title]`).
- **set/edition**: `p.subcat-rel` → **second** `<a>` text (first `<a>` is always
  the game "Magic").
- **rarity/collector** (NOT condition): the bare `<p>` after `subcat-rel`, shape
  `"<RARITY> - nr <N>"` (e.g. `M - nr 32`). Ignore for the Offer, or keep
  collector number for future use. See §7.
- **price**: `p.s_price` — take the text after the `span.s_currency`
  (`€` + `4.00`). EUR decimal. **Present only for in-stock rows.**
- **stock**: `img[src*="semaforo"]` — `semaforo_2`=Disponibile,
  `semaforo_1`=Disponibilità limitata, `semaforo_0`=Esaurito(out). Exact qty is
  the `(N)` in the enclosing `div[style*="float:right"]` text.
- **cart id / shop_ref**: `a.s_button_add_to_cart` → `rel` attr (`"152269"`) or
  the `id=` in its `carrello/update.php?id=…` href. Also mirrored in
  `input.box-qta` id `product_buy_quantity_152269`. **Anonymously present.**
  NB: this is **distinct** from the wishlist `id_prod` seen in
  `areaclienti/wishlist.php?id_prod=…` — use the cart id.
- **product URL**: `h3 a` href (absolute, e.g. `…/-/fulmine-15619`).
- **foil**: substring `"foil"` in the `h3` name — e.g. `FULMINE - FOIL`,
  `LIGHTNING BOLT (JUDGE FOIL)` (7/30 rows in the sample).

**Out-of-stock rows** (`semaforo_0` / Esaurito, 5/30 in the sample) have **no
`p.s_price` and no `a.s_button_add_to_cart`** — instead they render a
`<span class="prezzo_vendita">Valutazione: € X &gt;</span>` **buyback CTA**.
**Do NOT parse `Valutazione` as a sale price.** Rule: if `p.s_price` is missing,
treat the row as stock 0 / no offer and drop it when `in_stock_only`.

## 5. Field mapping (→ `Offer`)

| Offer field  | Source | Notes |
|--------------|--------|-------|
| `shop`       | literal `"magicstore"` | |
| `card_name`  | `h3 a` text | **Italian-localized** (`FULMINE`). Do NOT English-filter. |
| `edition`    | `p.subcat-rel` 2nd `<a>` text | Italian set name (e.g. `I Segreti di Strixhaven`). |
| `set_code`   | `None` | No 3-letter code exposed in results. |
| `condition`  | `Condition.NM` (default) | No condition axis in listings (single grade). See §7. |
| `language`   | `None` | Not exposed per-row (mostly Italian stock; unverified). |
| `foil`       | `"foil" in h3 name` | |
| `price_czk`  | `p.s_price` € value × FX, `int(round(...))` | EUR→CZK, see §6. |
| `stock_qty`  | `(N)` from `div[style*=float:right]`; 0 if no `s_price` | |
| `url`        | `h3 a` href | absolute. |
| `shop_ref`   | `a.s_button_add_to_cart` `rel` / `?id=` | cart product id; anonymously present. |

## 6. Currency / FX

Shop is **EUR**; `Offer.price_czk` is `int`. Follow the **cardmarket precedent**
(`MKM_EUR_TO_CZK`, default `24.5`) as an **interim per-shop FX env var** pending
the multi-currency refactor:

- Add env var **`CZ_MTG_MAGICSTORE_EUR_TO_CZK`** (default `24.5`).
- Constructor: `eur_to_czk: float | None = None`, resolved as
  `float(os.environ.get("CZ_MTG_MAGICSTORE_EUR_TO_CZK", 24.5))` when not passed.
- Conversion: `price_czk = int(round(price_eur * self._eur_to_czk))`.
- Document the flag in README **Configuration reference** (see §12) exactly like
  the `MKM_EUR_TO_CZK` row.

## 7. Condition normalization

**There is no condition axis.** The `"M - nr 32"` token is **rarity** (Italian:
`C`=Comune, `NC`=Non Comune, `R`=Rara, `M`=Mitica, plus `N`), followed by the
collector number — **not** a Magic condition. Verified across the sample:
`{C, NC, R, M, N}`. The detail page (`…/-/fulmine-15619`) exposes no
condition/grade either (apparent "stato"/"condizion" hits were false positives
from set names like "Devastatori").

→ **Default every offer to `Condition.NM`** (matches rishada/cernyrytir single-
grade behaviour; better than `UNKNOWN` for a physical shop selling graded
singles). Do **not** run the rarity token through `normalize.normalize_condition`.
Note the assumption in a code comment.

## 8. Non-playable filtering

Rely on the **existing global `filter_playable`** in `aggregator.py` — no
per-adapter filtering. Caveat: `filters._NON_PLAYABLE_PATTERNS` are **English**
(`art series`, `oversized`, `spindown`, …); Magic Store names are Italian, so
Italian-named display products would slip through. Scoping to `id_cat=9`
(singles) already excludes most non-playables. Log as a known limitation; do not
expand the shared filter in this PR (surgical scope).

## 9. Account features (login / cart)

- Session cookie **`ms_sid`**. Login is the "Login Privati" flow at
  `areaclienti/` (`jslogin`, private-customer, on `magicstore.it` — not the
  `msdistribuzione.it` B2B portal).
- Cart add is a **GET** to `carrello/update.php?id=<cart_id>` (the "Compra"
  link), with the desired qty in `box-qta`. `shop_ref` (the cart id) is already
  captured during search, so `add_to_cart` is wireable later.
- **DEFER account features to a follow-up PR.** Ship the read-only adapter with
  `supports_login = supports_cart = supports_watchlist = False`. Credentials
  would use the standard `CZ_MTG_MAGICSTORE_USER` / `_PASS` convention
  (`credentials.py`) when implemented. Login form fields are unverified.

## 10. Risks & blockers

1. **B2B price gating — RESOLVED (top risk cleared).** Consumer prices, stock,
   cart ids and product URLs are all rendered to anonymous requests
   (`p.s_price` = `€4.00`, etc., with no `ms_sid` login). The B2B/reseller
   pricing lives on a separate domain (`msdistribuzione.it`) and does not gate
   the public `magicstore.it` listings.
2. **Italian-localized card names (biggest real issue).** Results return
   `FULMINE`, not `Lightning Bolt`. Consequences:
   - The English `query.name` substring filter used by other adapters would drop
     every row → **omit client-side name filtering**; trust the server's search.
   - `card_name` on returned offers is Italian, so the **decklist optimizer**
     (which matches offers to requested cards by English name) will NOT associate
     Magic Store offers with English decklist entries. Offers show up in raw
     `search`/`compare` but effectively drop out of the optimizer/decklist path.
     **Flag prominently in README limitations.** Future mitigation: map
     Italian↔English via Scryfall printed names (`scryfall.py` already present).
3. **No `set_code`; Italian set names.** `edition` filtering only matches the
   localized set string, so an English edition filter (`"Strixhaven"`) won't
   match `"I Segreti di Strixhaven"`. Document.
4. **Bespoke selectors** (`div.s_item`, `p.s_price`, `semaforo_*`) — brittle to a
   template redesign. Fixture tests mitigate/detect regressions.
5. **Pagination param unconfirmed** for >30 results (MVP = page 1 only).
6. Encoding is UTF-8 (no windows-1250 trap) — low risk.

## 11. Tests & fixtures

- Save the fetched search HTML as
  **`tests/fixtures/magicstore_lightning_bolt.html`** (the live
  `?q=lightning+bolt&id_cat=9` response; it already contains in-stock, foil,
  and out-of-stock/`Esaurito` rows — good coverage in one file).
- Add **`tests/test_magicstore_adapter.py`**, mirroring
  `tests/test_rishada_adapter.py` and using the `load_fixture` conftest fixture.
  Drive `adapter.parse(html, SearchQuery(...))` (no network). Assertions:
  - parses ≥1 offer; every `o.shop == "magicstore"`, `o.url.startswith("https://www.magicstore.it/")`.
  - **foil**: the `LIGHTNING BOLT (JUDGE FOIL)` / `FULMINE - FOIL` row has `foil is True`.
  - **in_stock filter**: `in_stock_only=True` drops `semaforo_0`/no-`s_price`
    rows; all remaining `stock_qty > 0`.
  - **EUR→CZK**: construct `MagicStoreAdapter(eur_to_czk=25.0)`; assert a known
    `€4.00` row maps to `price_czk == 100`.
  - **shop_ref**: at least one in-stock offer has a numeric `shop_ref` (cart id).
  - **Valutazione not priced**: an `Esaurito` card is not returned as a priced
    in-stock offer.
- Register nothing extra in conftest (the shared `load_fixture` already covers it).

## 12. Implementation checklist

1. `models.py`: add `"magicstore"` to the `ShopId` `Literal` **and** to `ALL_SHOPS`.
2. Create `src/cz_mtg_compare/adapters/magicstore.py` — `MagicStoreAdapter`
   (template: `rishada.py`; FX from `cardmarket.py`). GET search, selectolax
   parse, NM default, `shop_ref` from cart id, **no name filter**,
   `supports_* = False`.
3. `src/cz_mtg_compare/adapters/__init__.py`: import `MagicStoreAdapter`, add to
   `__all__`, append to `build_default_adapters()` candidates. (This is the
   registry the aggregator's `get_adapter()` iterates.)
4. FX env var `CZ_MTG_MAGICSTORE_EUR_TO_CZK` (default 24.5) in the constructor.
5. Fixture + `tests/test_magicstore_adapter.py` (§11).
6. **README.md (mandatory — CLAUDE.md rule; user-facing change):**
   - **Supported shops** table: new `magicstore.it` row (Italy, HTML-scraped,
     EUR→CZK, Italian names caveat).
   - **Configuration reference** table: `CZ_MTG_MAGICSTORE_EUR_TO_CZK` row
     (mirror the `MKM_EUR_TO_CZK` row; default `24.5`).
   - **Known limitations**: Italian-localized names → weak decklist/optimizer
     matching; edition filtering matches Italian set names; no condition axis
     (all NM); interim fixed FX rate.
   - Per-shop capability matrix: `magicstore` row with ❌/❌/❌ (read-only).
7. Run `pytest`, `ruff check`, and the type checker; confirm README diff is
   non-empty before opening the PR.

## 13. Effort estimate

**MEDIUM — ~0.5–1 developer-day** for the read-only adapter + tests + README.
Parsing is genuinely easy (clean semantic CSS classes, UTF-8, anonymous prices).
The incremental cost over a "vanilla" adapter is: (a) EUR→CZK plumbing, (b) the
deliberate *removal* of English name-filtering plus documenting the Italian-name
optimizer limitation, (c) the out-of-stock `Valutazione` guard. Account features
(login + cart) are a **separate follow-up PR**, roughly another 0.5 day once the
`ms_sid` login form is reverse-engineered.
