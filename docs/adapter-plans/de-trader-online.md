# Adapter plan: Trader-Online (traderonline)

> Recon verified live on 2026-07-21 against `trader-online.de` (search "Sol Ring",
> 48 hits, 543 KB HTML; one single-card detail page, 331 KB). Raw HTML captured at
> `…/scratchpad/traderonline_search.html` and `traderonline_detail.html`. All selectors
> below are quoted from that live markup, not assumed from a stock OXID theme.

## 1. Identity

| | |
|---|---|
| `shop_id` | `traderonline` |
| Display name | Trader-Online |
| Country / currency | Germany / **EUR** |
| `base_url` | `https://www.trader-online.de` (301-redirects to `https://trader-online.de`; the shared client has `follow_redirects=True`, so either works — use the canonical `https://trader-online.de` to skip the hop) |
| Platform | OXID eShop (PHP), bespoke "trader"/"divi" front-end theme (NOT stock Flow/Wave) |
| Rendering | Fully server-rendered HTML. No JSON-LD, no JS needed. httpx + selectolax only. |
| Account features | **Deferred** (read-only v1) — see §9 |
| `host_slot` key | `"trader-online.de"` |

Register in `models.py` (`ShopId` Literal **and** `ALL_SHOPS` tuple) and in
`adapters/__init__.py` (`build_default_adapters()` candidates + `__all__`).

## 2. Starting template

**`tolarie.py`** is the closest structural match (tile/box loop, `strip_card_suffixes`,
per-tile `shop_ref` from a data attribute, name/edition/in-stock filtering), with the
**EUR→CZK** conversion lifted from **`cardmarket.py`** (`eur_to_czk` ctor arg + env var,
`price_czk = int(round(eur * fx))`). Copy tolarie's `search`/`parse`/`_parse` skeleton and
its query-side filters (`wanted`, `edition_filter`, `in_stock_only`); replace the row
parser with a card-tile parser. Do **not** copy tolarie's login/cart methods (v1 is
read-only). Read-only means `supports_login = supports_cart = supports_watchlist = False`
(unlike tolarie/najada/blacklotus which set them True).

## 3. Search endpoint

```
GET https://trader-online.de/index.php?lang=1&cl=search&searchparam=<term>&_artperpage=100
```

- `lang=1` = English UI (verified: labels render "Rarity/Edition/Condition/Ready for
  shipping"). This is the **UI** language only; card-printing language is separate (§5).
- `searchparam` — url-encoded query term. `urllib.parse.urlencode({"searchparam": query.name})`.
- **Pagination:** OXID standard `pgNr` (0-indexed) + `_artperpage` (default **50**).
  "Sol Ring" returned all 48 hits on `pgNr=0`; the `<ul class="d-none pagination">` was
  empty/hidden. Canonical/alt-view links in the page expose the param shape:
  `…&ldtype=grid&_artperpage=50&pgNr=0`.
  **Strategy:** request `_artperpage=100` in a single GET — covers virtually every
  single-name search. Add a `pgNr` loop only if a real query is ever seen to exceed 100
  hits (document as a follow-up, don't build speculatively).
- **UA:** the shared client's default UA is a bot string
  (`cz-mtg-compare-mcp/0.1 …`). A curl with a browser UA got HTTP 200; the bot UA is
  untested against this shop. Pass a realistic browser `User-Agent` header on the request
  (`Mozilla/5.0 … Chrome/124 Safari/537.36`) to be safe — German commercial shops
  commonly filter bots. `Accept-Language` is irrelevant here (language is set by `lang=1`).

## 4. Offer extraction

**Where variants live — DECIDED: parse the results page only. Do NOT follow detail pages.**

Each OXID article is a single, fully-specified printing. The detail page shows a fixed
attribute table (`Product number / Color / Manufacturer / Condition / Rarity / Edition /
Weight`) with **no `<select>` variant dropdowns** (verified: zero `<select>` on the detail
page). Different conditions/foils/languages are therefore **separate articles = separate
search tiles**, so one tile → one `Offer`. Following details would be pure request
amplification (up to 100 GETs/search) for the single field the tile omits (condition), and
is rejected. Condition is defaulted instead (§7).

**Results container:** there is no named wrapper; select the tiles directly.
**Per-tile selector:** `div.card.product-card` (48 present for "Sol Ring"; class string is
exactly `card product-card col-sm`). Iterate `tree.css("div.product-card")`.

**Product-type filter (critical):** the search mixes in accessories, sealed, playmats
**and buylist ("we-buy") entries**. Keep a tile only if its detail href contains
`"/magic-the-gathering/single-cards/"`. This yields 23 real sell offers for "Sol Ring" and
cleanly drops:
- accessories, e.g. `/en/accessories/gamegenic/playmats/…`
- **buylist**, e.g. `/en/card-purchase/sell-magic-single-cards/sol-ring-1-13.html` — these
  are prices the shop **pays you**, NOT sell prices; including them would inject bogus
  cheap "offers". Belt-and-braces: also reject any href containing `"/card-purchase/"`.

Verbatim single-card tile (Sol Ring, trimmed):

```html
<div class="card product-card col-sm">
  <div class="product-img-wrapper">
    <img ... src="https://trader-online.de/out/pictures/generated/product/1/350_220_75/sol-ring_SOC-EN0128.webp"
         alt="Sol Ring " class="product-img">
    <a href="https://trader-online.de/en/magic-the-gathering/single-cards/english/secrets-of-strixhaven-commander/sol-ring.html?listtype=search&amp;searchparam=Sol%20Ring"
       class="stretched-link" aria-label="Details"></a>
    <div class="right">
      <a aria-label="Add to wish list" class="btn"
         href="…index.php?lang=1&cl=account&anid=de441a707d9b4785bb922831a7faa361&sourcecl=search…"> … </a>
    </div>
  </div>
  <div class="card-body">
    <button type="button" class="btn btn-highlight divi-add-to-cart"
            data-id="de441a707d9b4785bb922831a7faa361" onclick="diviAjaxCart.addToCart(this)">To cart</button>
    <div class="h5 card-title">Sol Ring</div>
    <ul class="list-unstyled attributes small">
      <li><strong>Rarity:</strong><span>Uncommon</span></li>
      <li><strong>Edition:</strong><span>Secrets of Strixhaven: Commander</span></li>
    </ul>
    <div class="text-nowrap grid-price mt-1">
      <span class="price-pre">2</span>
      <span class="price-decimal">,99 €*</span>
    </div>
    <span class="stockFlag"><i class="fa-solid fa-stop stock-flag-icon text-success"></i> Ready for shipping</span>
  </div>
</div>
```

Sale tiles wrap the same `.grid-price` but add a nested struck old price —
`<span class="oldPrice">Statt <s>24,99 €</s></span>` — inside `.grid-price`. **Never read
the whole `.grid-price` text**; select `.price-pre` + `.price-decimal` only (verified: a
`-20 %` tile gives pre=`19`, decimal=`,99 €*` → 19.99, ignoring the struck 24,99).

## 5. Field mapping (→ `Offer`)

| Offer field | Source on tile | Notes |
|---|---|---|
| `card_name` | `.card-title` text; fall back to `img.product-img[alt]` when the title ends with `…` (long titles are truncated, e.g. playmats — singles like "Sol Ring" are not). Run through `strip_card_suffixes` to peel any `(foil)`/condition suffix. | Apply the `query.name` substring filter (tolarie pattern). |
| `edition` | `ul.attributes li` where `<strong>` == `Edition:` → child `<span>` text. | e.g. "Secrets of Strixhaven: Commander". German printings may read "… (englisch)/(deutsch)". |
| `set_code` | Regex on `img.product-img[src]` filename / product-number: `([A-Z0-9]{2,4})-([A-Z]{2})\d+` from `sol-ring_SOC-EN0128.webp` → `SOC`. | Uppercased. `None` when the code is absent (some tiles use a flat slug like `sol-ring-1-32.html` with no code). |
| `condition` | **Not on tile.** Default `Condition.NM`. | Detail page shows "Near Mint - Mint"; see §7. |
| `language` | Group 2 of the same code regex (`EN`/`DE`/…). | Store as `"EN"`/`"DE"`. Do NOT use `lang=1` (that's UI, not printing). URL segment (`…/single-cards/english/…`) is a weaker fallback — not always present (flat slugs omit it). |
| `foil` | `strip_card_suffixes` + case-insensitive `"foil"` in raw title/edition (rishada pattern). | No foil tiles in the Sol Ring sample; see risk §10.3. |
| `price` (→ `price_czk`) | `.grid-price .price-pre` (integer euros) + digits of `.grid-price .price-decimal` (`",99 €*"` → `99`). Compose `float(f"{pre}.{cents}")` → EUR, then `int(round(eur * fx))`. | Skip tile if pre/decimal missing. Avoid `.oldPrice`/`<s>`. |
| `stock_qty` | `span.stockFlag` text. "Ready for shipping" (green `i.stock-flag-icon.text-success`) → in stock. | No numeric qty on tile → use `1` when in stock, `0` otherwise (matches cardmarket's "sellers exist → 1"). Treat "not available"/`text-danger`/missing flag as `0`. Apply `in_stock_only`. |
| `url` | `a.stretched-link[href]` (absolute). Optionally strip the `?listtype=search&searchparam=…` query for a clean canonical URL. | |
| `shop_ref` | `button.divi-add-to-cart[data-id]` (32-hex OXID article id, `anid`). Fallback: the `anid=` param of the wish-list link `a[href*="cl=account"][href*="anid="]`. | Captured now so §9 cart work needs no re-plumbing. `None` if absent. |

`rarity` (`<strong>Rarity:</strong>`) is not an `Offer` field; read it only to help token
detection if desired (`Token (Spielstein)`).

## 6. Currency / FX

Shop prices are EUR; `Offer.price_czk` is `int`. Follow the cardmarket precedent exactly
(interim per-shop FX until the multi-currency refactor lands):

- Ctor arg `eur_to_czk: float | None = None`.
- Env var **`CZ_MTG_TRADERONLINE_EUR_TO_CZK`**, default `24.5` (reuse cardmarket's
  `DEFAULT_EUR_TO_CZK` constant value; a shared default is fine).
- `price_czk = int(round(price_eur * self._eur_to_czk))`.
- Add the env var to the README **Configuration reference** table and note the static-rate
  caveat in **Limitations** (same wording as `MKM_EUR_TO_CZK`).

## 7. Condition normalization

Condition is **not rendered on search tiles**; only the detail page carries it, as an EN
label like **"Near Mint - Mint"** (German UI would show "Zustand"). Since v1 does not fetch
detail pages, **default every offer to `Condition.NM`** — Trader-Online is a dedicated
singles retailer stocking near-mint copies, so NM is the correct modal value and matches
how tolarie/rishada default. Document this as a known limitation (§10.1).

Add an OXID label map (in-adapter, or extend `normalize._CONDITION_MAP`) so it is ready if
detail parsing is ever added:

| OXID label (EN / DE) | `Condition` |
|---|---|
| Near Mint - Mint, Near Mint, Mint | `NM` |
| Excellent | `EX` |
| Good / Gut | `GD` |
| Played / Gespielt | `PL` |
| Poor | `HP` |

Minimum change today: add `"near mint - mint": Condition.NM` to `_CONDITION_MAP`.

## 8. Non-playable filtering

- The `/magic-the-gathering/single-cards/` URL gate (§4) already removes accessories,
  sealed, playmats and the `/card-purchase/` buylist — the main adapter-level filtering.
- The aggregator applies `filters.filter_playable` globally (Art Series / oversized /
  spindown etc.) — no change needed; nothing Trader-Online-specific to add there.
- **Tokens:** rarity `Token (Spielstein)` appears as single-cards. Peers don't strip
  tokens and a token's name usually won't match a real card-name query anyway, so leave
  them to the existing filter. Note it; don't build a new rule speculatively.

## 9. Account features (login / cart)

**Defer — ship read-only v1.** Feasibility (documented for a future PR):

- OXID login: `POST index.php?cl=login&fnc=login` with `lgn_usr` / `lgn_pwd`; session via
  `sid` cookie.
- Add-to-cart: the tile button calls `diviAjaxCart.addToCart(this)` reading
  `data-id` (= `anid`). Underlying OXID route is
  `index.php?cl=basket&fnc=tobasket&aid=<anid>&am=<n>` but requires an OXID session token
  (`stoken`) — a CSRF-style value that must be scraped from a live page/form first. That
  extra token handshake is the reason to defer.

Set `supports_login/cart/watchlist = False`. We already capture `shop_ref` (the `anid`)
during search, so a later cart PR only adds the login + `stoken` plumbing, not a re-parse.

## 10. Risks & blockers

1. **Condition is detail-only** → all offers default to NM. If the shop ever lists a
   non-NM copy, we mislabel it. Fetching per-tile detail to fix this is rejected (up to
   100 extra GETs/search). *Medium.*
2. **Buylist contamination** — `/card-purchase/…` "we-buy" rows appear in the same search
   results. Without the URL gate they'd surface as bogus ultra-cheap offers. High impact,
   but fully mitigated by the `/magic-the-gathering/single-cards/` filter. *Handled.*
3. **Foil detection is textual only** — no foil flag/badge on the tile; relies on "foil"
   in title/edition. Foils may be mislabeled non-foil (no foil tile in the sample to
   confirm the naming). *Medium.*
4. **Bespoke theme selectors** — `.product-card`, `.price-pre`, `.price-decimal`,
   `.stockFlag`, `.divi-add-to-cart[data-id]` are custom "trader"/"divi" classes, not stock
   OXID; a theme update can break them. Same brittleness class as every HTML-tile adapter.
   *Medium.*
5. **Split + sale price markup** — current price is `.price-pre` + `.price-decimal`; the
   struck `.oldPrice`/`<s>` is nested inside `.grid-price`. Reading the wrong node yields
   the pre-discount price. *Low once §4/§5 followed.*
6. **DE/EN dual paths** — `lang=1` controls UI only; printing language comes from the
   product code (EN/DE). German printings are separate articles. *Low.*
7. **Static FX** — `CZ_MTG_TRADERONLINE_EUR_TO_CZK` drifts from spot. Known, same as
   cardmarket. *Low.*
8. **Bot UA / pagination** — default client UA untested against the shop (use a browser
   UA); >100-hit queries would need a `pgNr` loop (unlikely per single card). *Low.*

## 11. Tests & fixtures

Fixture-based, mirroring `tests/test_tolarie_adapter.py` (uses `load_fixture` +
`SearchQuery`, calls `adapter.parse(html, query)`).

Fixtures (save under `tests/fixtures/`):
- `traderonline_sol_ring.html` — the captured 543 KB search page (already in scratchpad).
  It exercises single-cards **and** the accessory + buylist tiles the filter must drop, and
  a `-20 %` sale tile for the old-price path.
- Optionally `traderonline_lightning_bolt.html` for peer-naming consistency — capture only
  if a foil single is needed to test §5 foil detection.

`tests/test_traderonline_adapter.py`:
- **parses fixture** → non-empty; every offer `shop == "traderonline"`, `card_name` matches
  query (case-insensitive), `price_czk > 0`, `url.startswith("https://trader-online.de")`,
  `condition in Condition`.
- **buylist excluded** → no `offer.url` contains `/card-purchase/`.
- **accessories excluded** → every `offer.url` contains `/magic-the-gathering/single-cards/`.
- **shop_ref captured** → at least one offer has a 32-hex `shop_ref`.
- **set_code / language** → some offer has `set_code == "SOC"` and `language == "EN"`.
- **FX** → construct `TraderOnlineAdapter(eur_to_czk=24.5)`; the 2,99 € "Sol Ring
  (Strixhaven)" tile → `price_czk == round(2.99 * 24.5) == 73`.
- **in_stock filter** → `in_stock_only=True` count ≤ `False` count; all in-stock `stock_qty > 0`.
- **edition filter** → `edition="Strixhaven"` returns only Strixhaven offers.
- **robustness** → add to `test_adapter_robustness.py`: empty / garbage HTML → `[]`, no raise.

## 12. Implementation checklist

- [ ] `models.py`: add `"traderonline"` to `ShopId` Literal **and** `ALL_SHOPS`.
- [ ] `adapters/traderonline.py`: `TraderOnlineAdapter(ShopAdapter)` with `shop_id`,
      `base_url`, `supports_* = False`, `__init__(eur_to_czk=None)` reading
      `CZ_MTG_TRADERONLINE_EUR_TO_CZK`, `_search_url`, `search`, `parse`, `_parse`,
      `_parse_card`, `_parse_price_eur`. Browser UA header on the GET.
- [ ] `adapters/__init__.py`: import, add to `__all__`, append `TraderOnlineAdapter()` to
      `build_default_adapters()` candidates.
- [ ] `normalize.py`: add `"near mint - mint": Condition.NM` (and the §7 OXID labels).
- [ ] `tests/`: `test_traderonline_adapter.py` + fixture(s); robustness case.
- [ ] **README.md** (mandatory per CLAUDE.md — user-facing new shop): **Supported shops**
      (Trader-Online, DE, EUR, read-only), **Configuration reference**
      (`CZ_MTG_TRADERONLINE_EUR_TO_CZK`), per-shop **capability matrix** (login/cart = no),
      **Limitations** (condition defaults to NM; static EUR→CZK rate).
- [ ] Verify: `ruff check`, `mypy`, `pytest` all green.

## 13. Effort estimate

**Verdict: MEDIUM-HIGH** (confirmed feasible — pure server-rendered HTML, no JS, no login
in v1). Slightly above a plain tile adapter because of four wrinkles: split/sale price
markup, mandatory buylist URL filtering, EUR→CZK FX, and set-code/language parsing from the
image/product code.

**~4–6 hours**, ~250–350 LOC incl. tests: ~120 LOC adapter, ~120 LOC tests, plus the
fixture, model/registry wiring, and README. No account-feature work (deferred).
