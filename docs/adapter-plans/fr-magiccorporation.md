# Adapter plan: MagicCorporation (magiccorporation)

Verdict: **HIGH** feasibility. Fully server-rendered, single-request search, all
prices + variants + stock present on the results page. Effort: **Small–Medium
(~4–6 h incl. tests + README)**.

Recon was performed live (realistic Chrome UA) against
`boutique.magiccorporation.com`; every selector, URL, and attribute below was
verified against saved HTML (`recherche?q=Sol+Ring`, 167 KB, and
`/carte/6365-anneau-solaire`, 128 KB). The site is a modern **Laravel + Tailwind**
app (the JS `_token` is Laravel CSRF, classes are Tailwind), but it is *fully
server-rendered* — httpx + selectolax is sufficient, no JS/headless needed. The
task brief called it "custom PHP"; it is Laravel PHP, which does not change the
scraping approach.

---

## 1. Identity

| Field | Value |
|-------|-------|
| `shop_id` | `magiccorporation` |
| `base_url` | `https://boutique.magiccorporation.com` |
| host (for `host_slot(...)`) | `boutique.magiccorporation.com` |
| Country / currency | France / **EUR** |
| Platform | Laravel + Tailwind, server-rendered (no-JS friendly) |
| New class | `MagicCorporationAdapter` in `src/cz_mtg_compare/adapters/magiccorporation.py` |

Model changes (`src/cz_mtg_compare/models.py`): add `"magiccorporation"` to both
the `ShopId` `Literal` (lines 9–17) and the `ALL_SHOPS` tuple (lines 18–26).

Registry changes (`src/cz_mtg_compare/adapters/__init__.py`): import
`MagicCorporationAdapter`, add to `__all__`, and append an instance to the
`candidates` list in `build_default_adapters()`. No credentials gate needed (v1
is read-only, always enabled; still honours `CZ_MTG_DISABLED_SHOPS`).

## 2. Starting template

**Primary template: `adapters/rishada.py`** (custom server-rendered shop, one
`search()` GET, then a synchronous `_parse(html, url, query)` with a fixture-only
`parse()` override). Mirror its structure:

- `search()` → `get_client()` + `async with host_slot("boutique.magiccorporation.com")` → `resp.raise_for_status()` → `self._parse(...)`.
- `parse(html, query)` (public, for fixture tests) → delegates to `_parse`.
- Per-query filtering (name substring, edition, `in_stock_only`) done in `_parse`, exactly like rishada lines 80–106.

**Secondary references:**
- `adapters/cardmarket.py` (lines 100–117, 174) for the **EUR→CZK** constructor
  pattern (`eur_to_czk: float | None = None` + env fallback, `int(round(eur * fx))`).
- `adapters/blacklotus.py` `_enrich_offers` (lines 68–91) — **only** as the model
  for the *deferred future* per-`/carte/{id}` fetch that would add used copies;
  v1 does not fetch detail pages.

## 3. Search endpoint

`GET /recherche?q=<name>` — form `method=get`, field `name="q"`. Build with
`urllib.parse.urlencode({"q": query.name})`.

**Fetch strategy — single GET, no detail fetch.** The results page carries
everything the Offer needs: per-variant price, per-variant stock (`data-max`),
language/foil, product id, edition, rarity, and the detail URL. Verified: the
`/carte/{id}` detail page does **not** use the `data-max` variant `<select>` at
all (0 occurrences), so following detail links buys us nothing for *new* stock.

**Per-host request count: 1 GET per search** in v1. This is the cheapest possible
adapter and sits comfortably under `PER_HOST_CONCURRENCY = 3` and the 20 s
per-shop timeout.

**Pagination (bound it).** Results are paginated 25 rows/page. The singles
section header reads `Cartes à l'unité(85)`; page links use
`?q=<name>&cartes=2#cartes`, `&cartes=3`, … plus a `Suivant ›` link. **v1 fetches
page 1 only** (1 request). If richer coverage is wanted later, cap at ~2–3 pages
(`cartes=2`, `cartes=3`) to stay within the request budget — document the cap.
There is also a `Produits(0)` (sealed) section on the same page; ignore it, parse
only the singles table.

## 4. Offer extraction

**One `<section class="scroll-mt-24">` → one `<table class="w-full text-sm">` →
one `<tbody class="divide-y divide-white/5">`** with 25 `<tr>` product rows.
`<thead>` columns are: `Carte | N° | Édition | Rareté | Prix | (blank) | (blank) |
Foil | Ajouter au panier`.

**Critical coverage fact:** a per-row add-to-cart `<form class="js-cart-row">` is
rendered **only for rows that have in-stock NEW copies**. On the Sol Ring page,
25 rows → **only 2** carry a `form.js-cart-row` (the rest are used-only
"exemplaire unique" or out of stock). So iterating the cart forms is the cleanest
way to enumerate purchasable new offers, and each form's variant `<select>` is
the authoritative source of price + stock.

**Robust selectors** (anchor on stable `js-*` hooks and `name=` attributes, NOT
on Tailwind visual classes like `hover:bg-white/5` which contain `/` and `:` and
are brittle to escape):

- Rows/offers: `tree.css('form.js-cart-row')` — one form per new-stock product.
- Within each form:
  - `input[name="id"]` → product id (e.g. `6365`) = the `/carte/{id}` id.
  - `input[name="type"]` → `carte` (sanity check).
  - `input[name="_token"]` → 40-char Laravel CSRF (needed only for future cart).
  - `select[name="variant"] option` → **one Offer per option**.
- To read name + edition + detail URL, walk up from the form to its enclosing
  `<tr>` (`node.parent` until `tag == "tr"`, cf. rishada `_parse_cart_items`
  lines 372–374) and read within that row.

**Variant `<select>` — the offer expansion (verified verbatim):**

```html
<form method="post" action="https://boutique.magiccorporation.com/panier/ajouter" class="inline-flex items-center gap-1.5 js-cart-row">
    <input type="hidden" name="_token" value="2tbQIPi1uLIAFdthAvOoyIRe0VNWv0VsjwCAFiZg" autocomplete="off">
    <input type="hidden" name="type" value="carte">
    <input type="hidden" name="id" value="6365">
    <select name="variant" class="js-variant ...">
        <option value="vo" data-max="5">VO · 25,00 €</option>
        <option value="vf" data-max="1">VF · 25,00 €</option>
    </select>
    <select name="qty" class="js-qty ...">...</select>
    <button ...>+</button>
</form>
```

Observed option `value`s: `vo`, `vf`, `vo_foil`, `vf_foil`. Option text forms:
`"VO · 25,00 €"`, `"VF · 25,00 €"`, `"VO Foil · 29,90 €"`. Only *in-stock*
variants are emitted as options (no `data-max="0"` observed) — so every option is
a real, buyable offer. **Emit one `Offer` per `<option>`.**

**Name + edition within the row (verified):**

```html
<a href="https://boutique.magiccorporation.com/carte/6365-anneau-solaire" class="min-w-0">
    <span class="block text-white ... font-medium truncate ...">Anneau solaire</span>
    <span class="block text-xs text-gray-500 truncate">Sol Ring</span>
</a>
...
<a href="https://boutique.magiccorporation.com/cartes/5-revised" title="Revised" ...>
    ... <span class="truncate max-w-[12rem]">Revised</span>
</a>
```

- Bold span (`span.block.text-white`) = **localized (French) name** — here
  `Anneau solaire`.
- Gray subtitle (`span.block.text-xs.text-gray-500`) = **English canonical name**
  — here `Sol Ring`. It is **present only when the French name differs**; for
  English-only-named cards (Alpha/Beta/Unlimited "Sol Ring") the bold span
  already holds the English name and the subtitle is absent.
- Edition: `a[href*="/cartes/"]` (plural `cartes` = edition list; singular
  `carte` = card detail) → `title` attribute or inner `span` text → `Revised`.
- Detail URL: `a[href*="/carte/"]` (singular) → `/carte/6365-anneau-solaire`.

## 5. Field mapping (→ Offer)

| `Offer` field | Source | Notes |
|---------------|--------|-------|
| `shop` | constant `"magiccorporation"` | |
| `card_name` | gray subtitle `span.text-gray-500` if present, else bold `span.text-white` text | prefer the **English canonical** name; unescape + collapse whitespace |
| `edition` | `a[href*="/cartes/"]` `title` (fallback inner span) | e.g. `Revised`; `None` if absent |
| `set_code` | *optional* — parse from the row's `svgs.scryfall.io/sets/<code>.svg` img (`3ed`) | nice-to-have; may omit in v1 |
| `condition` | constant `Condition.NM` | **no per-offer condition axis** for new stock — see §7 (detail page confirms new VO/VF copies are labelled "Near Mint") |
| `language` | from option `value`: `startswith("vo")` → `"EN"`, else `"FR"` | VO = *version originale* (English); VF = *version française* (French) |
| `foil` | option `value.endswith("_foil")` | `vo_foil` / `vf_foil` → `True` |
| `price_czk` | parse EUR from option text (after `·`), `int(round(eur * self._eur_to_czk))` | e.g. `"VO Foil · 29,90 €"` → `29.90` → CZK; comma decimal, strip `€`/spaces/label |
| `stock_qty` | `int(option["data-max"])` | per-variant stock, verified |
| `url` | row's `a[href*="/carte/"]` href | `/carte/{id}-{slug}` |
| `shop_ref` | `f"{id}:{value}"` e.g. `"6365:vo_foil"` | product id alone is ambiguous (multiple buyable variants per product); encode id **and** variant so a future `add_to_cart` can POST the right `variant`. Document the format. |
| `fetched_at` | model default | |

**EUR price parsing** (no existing helper — `normalize.parse_price_czk` only
matches `Kč`/`CZK`). Add a small local helper in the adapter:

```python
_EUR_RE = re.compile(r"(\d[\d\s .]*,\d{2})\s*€")
def _parse_price_eur(text: str) -> float | None:
    m = _EUR_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(" ", "").replace(".", "").replace(",", ".")
    return float(raw)
```

## 6. Currency / FX

Shop is EUR; `Offer.price_czk` is `int`. Follow the **cardmarket precedent**
(`DEFAULT_EUR_TO_CZK = 24.5`, `MKM_EUR_TO_CZK` env, `int(round(eur * fx))`):

- Constructor: `def __init__(self, *, eur_to_czk: float | None = None) -> None:`
  with `self._eur_to_czk = eur_to_czk if eur_to_czk is not None else
  float(os.environ.get("CZ_MTG_MAGICCORPORATION_EUR_TO_CZK", DEFAULT_EUR_TO_CZK))`,
  `DEFAULT_EUR_TO_CZK = 24.5` (same default as cardmarket).
- Passing `eur_to_czk` explicitly in tests makes `price_czk` deterministic.

**Pending multi-currency refactor — flag it.** There are now **two** EUR shops
(cardmarket via `MKM_EUR_TO_CZK`, magiccorporation via
`CZ_MTG_MAGICCORPORATION_EUR_TO_CZK`) each carrying a private FX rate and
`DEFAULT_EUR_TO_CZK` constant. This duplication should be centralized into a
single shared currency module (one env var / live-rate source consumed by all
non-CZK adapters, and possibly a currency field on `Offer`). Leave a clear
`# TODO: multi-currency — see docs/adapter-plans/fr-magiccorporation.md §6`
comment near the FX constant so the debt is discoverable. Do **not** do the
refactor in this PR — just introduce the per-shop var and flag it.

## 7. Condition normalization

New stock has **no per-offer condition axis**: MagicCorporation differentiates
new copies by **language × foil only** (VO / VF / VO_foil / VF_foil). The
`/carte/{id}` detail page confirms these new copies are labelled **"Near Mint"**
(verified: the detail variant table renders rows like
`VO · Near Mint · 25,00 €`). Therefore set `condition = Condition.NM` for every
select-derived offer. Document this in a code comment (mirroring rishada's
"displays Near Mint by default", rishada line 136).

`normalize.normalize_condition` already maps `"near mint"`, `"excellent"`,
`"played"`, etc. — it will be needed **only if** the deferred used-copies feature
(§10) is built, where per-copy conditions (Excellent / Played / Near Mint) do
appear.

## 8. Non-playable filtering

Handled centrally: `aggregator.search()` calls `filters.filter_playable()`
(unless `include_non_playable`). No adapter-side work required. Because we set
`card_name` to the **English canonical** name where available, the existing
English regexes (`art series`, `oversized`, `spindown`, …) in `filters.py` match
correctly. Minor residual risk: for cards whose *only* name is a French display
string, or French edition labels, an English-only pattern could miss — note this,
but do not expand `filters.py` in this PR (out of scope; central concern).

## 9. Account features (login / cart)

**Defer to a follow-up PR.** v1 sets `supports_login = supports_cart =
supports_watchlist = False` (ABC defaults; the base methods raise
`AccountFeatureNotSupported`).

Feasibility is good and the endpoints are already visible (record them for later;
the brief's `monpanier.php` guess is superseded by the real Laravel routes):

- Login page: `GET /connexion`; there is an English variant via `/lang/en`.
- Cart: `GET /panier`; **add-to-cart** `POST /panier/ajouter` with form fields
  `_token` (Laravel CSRF, per-page), `type=carte`, `id=<product id>`,
  `variant=<vo|vf|vo_foil|vf_foil>`, `qty=<n>`.
- The CSRF `_token` is rendered in every `js-cart-row` form (and typically a
  `<meta name="csrf-token">`), so it can be scraped from the search page — which
  is exactly why `shop_ref` encodes `id:variant` (§5): a future `add_to_cart`
  needs the variant, and must fetch a fresh `_token`.

This maps cleanly onto the rishada/blacklotus login+cart pattern
(`_auth_lock`, `_ensure_auth`, `credentials_for("magiccorporation")`,
`CZ_MTG_MAGICCORPORATION_USER` / `_PASS`) when it is built.

## 10. Risks & blockers

1. **Variant-in-`<select>` expansion (handled).** One product row → N offers
   (one per `<option>`). Straightforward; the option text carries price and
   `data-max` carries stock. Low risk.
2. **Low new-stock coverage for vintage cards.** The cart form / variant select
   is rendered **only for in-stock NEW copies**. On the Sol Ring page, 25 rows
   yielded only **2** new-stock rows; the rest were used "exemplaires uniques"
   (per-copy, real conditions on `/carte/{id}`) or OOS. So for older / Reserved-
   List cards MagicCorporation may return **few or zero** offers from this
   adapter. This is a genuine coverage limitation — document it in the README
   Limitations section.
3. **Used copies deferred → possible N detail fetches.** Surfacing used
   "exemplaire unique" copies would require following `/carte/{id}` per result row
   (modelled on `blacklotus._enrich_offers`), i.e. **up to 25 extra GETs per
   search page** against one host. That inflates request count well beyond v1's
   single GET and pressures `PER_HOST_CONCURRENCY = 3` + the 20 s timeout.
   **v1 does not do this.** If added later, cap the number of enriched rows.
4. **Tailwind class brittleness.** Visual classes (`hover:bg-white/5`,
   `bg-amber-500/15`) are unstable and awkward to escape in CSS selectors. Anchor
   on `form.js-cart-row`, `select[name="variant"]`, `input[name="id"]`,
   `a[href*="/carte/"]`, `a[href*="/cartes/"]` instead. Low risk if followed.
5. **Anti-bot / UA.** Live fetches with a realistic Chrome UA returned HTTP 200
   at full size; the shared `http_client` UA is generic. If blocking appears,
   set a realistic browser `User-Agent` on the request. Low risk currently.

## 11. Tests & fixtures

Follow `tests/test_rishada_adapter.py` (fixture + `adapter.parse(html, query)`,
`load_fixture` from `conftest.py`).

- **Fixture:** save the recon HTML as
  `tests/fixtures/magiccorporation_sol_ring.html` (already captured during recon:
  the `recherche?q=Sol+Ring` page — contains a `vo`/`vf` product **and** a
  `vo_foil` product). Recommended: capture a second fixture for a modern staple
  (e.g. a recent Commander reprint) to also exercise a **`vf_foil`** option and
  richer multi-variant rows — save as
  `tests/fixtures/magiccorporation_<card>.html`.
- **Adapter under test:** instantiate with a fixed rate,
  `MagicCorporationAdapter(eur_to_czk=25.0)`, so `price_czk` is deterministic.
- **Test cases:**
  - parses ≥1 offer; every offer `shop == "magiccorporation"`,
    `url` starts with `https://boutique.magiccorporation.com/carte/`, `price_czk > 0`.
  - **language mapping:** at least one `language == "EN"` (VO) and one
    `language == "FR"` (VF).
  - **foil detection:** the `vo_foil` option yields an offer with `foil is True`
    and a higher `price_czk` than its non-foil sibling.
  - **stock from `data-max`:** an offer has `stock_qty == 5` (the `vo · data-max=5`
    Sol Ring row).
  - **default condition:** all offers `condition == Condition.NM`.
  - **`in_stock_only` filter:** with `in_stock_only=True`, all `stock_qty > 0`
    (trivially true since options are in-stock; keep the test for regression).
  - **`shop_ref` format:** matches `^\d+:(vo|vf|vo_foil|vf_foil)$`.
  - **edition + name:** `card_name` includes "Sol Ring"; `edition` set includes
    `Revised`.
- Add `magiccorporation` to any parametrized cross-adapter tests
  (`test_adapter_robustness.py`, `test_account_features_more_shops.py`,
  `test_aggregator*`) if they enumerate all shops.

## 12. Implementation checklist

- [ ] `models.py`: add `"magiccorporation"` to `ShopId` Literal and `ALL_SHOPS`.
- [ ] `adapters/magiccorporation.py`: new `MagicCorporationAdapter`
      (rishada-shaped `search` / `parse` / `_parse`; EUR→CZK ctor like
      cardmarket; local `_parse_price_eur`; `js-cart-row` → per-`option` Offer
      expansion; VO/VF→EN/FR; foil from `_foil`; `stock_qty` from `data-max`;
      `condition=NM`; `shop_ref = "{id}:{variant}"`; `supports_* = False`).
- [ ] `# TODO: multi-currency` comment near `DEFAULT_EUR_TO_CZK` (§6).
- [ ] `adapters/__init__.py`: import, add to `__all__`, append to `candidates`.
- [ ] `tests/fixtures/magiccorporation_sol_ring.html` (+ optional 2nd fixture).
- [ ] `tests/test_magiccorporation_adapter.py` (§11 cases).
- [ ] Extend any all-shops parametrized tests to include the new shop.
- [ ] **README.md** (required by CLAUDE.md — user-facing change):
  - Supported shops table (~line 54): add
    `| boutique.magiccorporation.com | HTML scrape (Laravel, server-rendered) | name, edition, language (VO/VF), foil, stock, price; EUR→CZK |`.
  - Configuration reference table (~line 466): add
    `| CZ_MTG_MAGICCORPORATION_EUR_TO_CZK | EUR → CZK conversion rate for MagicCorporation prices | 24.5 |`.
  - "What this is" / "Supported shops": note it is a **French** EUR shop.
  - Limitations: note only **new** (VO/VF ± foil) stock is surfaced; used
    "exemplaires uniques" (per-copy conditions) are **not** covered in v1.
  - Account-feature capability matrix (~line 364): add a `magiccorporation` row
    with ❌/❌/❌.
  - "How it works under the hood" adapter diagram (~line 577): add the shop.
- [ ] Run `ruff`/lint + `mypy` (or state if not configured) and `pytest`.

## 13. Effort estimate

**Small–Medium — roughly 4–6 hours.** The adapter itself is one of the simplest
in the repo (single GET, server-rendered, everything on the results page). The
only non-trivial bits are the EUR→CZK plumbing (copy cardmarket), the
`<select>`/`data-max` variant expansion (mechanical), and the README updates. No
login/cart, no detail fetch, no encoding quirks (UTF-8), no pagination in v1.
Risk is low; the main judgement call is the documented used-copies coverage gap.
