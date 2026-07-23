# Adapter plan: Bazaar of Magic + Spellenwinkel (`bazaarofmagic`, `spellenwinkel`) — shared adapter

Verdict: **HIGH** for the platform/adapter (Bazaar of Magic is singles-rich with clean
JSON-LD). **Caveat:** live recon shows **Spellenwinkel has no MTG singles catalog** (see
§10), so it will typically return 0 singles offers — the shared class still handles it
correctly and cheaply, so we ship both.

All recon below was captured live on 2026-07-21 with a realistic browser UA
(`Mozilla/5.0 ... Chrome/126 ... Safari/537.36`). Both domains sit behind Cloudflare
(`server: cloudflare`, `x-environment: Hipex/3`) and serve assets from `bazaargames.nl`
(shared "Bazaar Games" platform, Zurb Foundation CSS). Fully server-rendered.

---

## 1. Identity

Two shop ids, two base URLs, **one adapter class** — `BazaarGamesAdapter` in
`src/cz_mtg_compare/adapters/bazaargames.py`.

| shop_id | base_url | Notes |
|---|---|---|
| `bazaarofmagic` | `https://www.bazaarofmagic.eu` | Apex `bazaarofmagic.eu` 301s to `www.`. **Do not use `bazaarofmagic.nl`** — it also 301s here but has a TLS cert mismatch. Target `.eu`. |
| `spellenwinkel` | `https://www.spellenwinkel.nl` | Apex `spellenwinkel.nl` 301s to `www.`. |

- Country **NL**, currency **EUR**, platform **"Bazaar Games"** (custom, Cloudflare-fronted),
  rendering **server-side HTML** (no JS, no headless needed).
- Catalog scope: **Magic singles** only (we parse `div.singles` tiles). Bazaar of Magic
  carries a full singles catalog under `/en-WW/magic/singles`; Spellenwinkel is a
  board-game/sealed/accessories shop with **no** singles subcategory (§10).

**Shared-class + constructor-arg pattern.** `ShopAdapter` declares `shop_id` / `base_url`
as class attributes (`adapters/base.py`). A shared class simply sets them as *instance*
attributes in `__init__`, which shadow the class attributes:

```python
class BazaarGamesAdapter(ShopAdapter):
    supports_login = False
    supports_cart = False
    supports_watchlist = False

    def __init__(self, *, shop_id: ShopId, base_url: str,
                 eur_to_czk: float | None = None, enrich_detail: bool = False) -> None:
        self.shop_id = shop_id
        self.base_url = base_url.rstrip("/")
        self._host = urllib.parse.urlsplit(self.base_url).hostname or ""
        self._eur_to_czk = (
            eur_to_czk if eur_to_czk is not None
            else float(os.environ.get("CZ_MTG_BAZAARGAMES_EUR_TO_CZK", DEFAULT_EUR_TO_CZK))
        )
        self._enrich_detail = enrich_detail
```

Instantiate twice in `build_default_adapters()` (§12). `Aggregator.get_adapter()` is a
plain `shop_id` lookup and needs no change.

---

## 2. Starting template

`adapters/blacklotus.py` — it is the closest existing adapter because it (a) scrapes an
HTML search-results grid into `Offer`s and (b) has an optional best-effort **detail-page
enrichment** pass (`_enrich_offers` / `_apply_detail`, gated by `self._enrich_detail`).

Reuse that shape:
- `search()` → build search URL, GET via `get_client()` inside `async with host_slot(self._host)`, `_parse()` the HTML.
- `parse(html, query)` → thin wrapper over `_parse()` for fixture tests (required by ABC).
- `_parse()` iterates tiles, applies name/edition/in-stock filters, returns offers.
- Optional `_enrich_offers()` → follow `/en-WW/p/<slug>/<id>` and read the JSON-LD Offer
  for authoritative price/availability (default **off** — the search tile already carries
  everything; enrichment is only needed if a tile is ambiguous).

Parametrize by reading `self.base_url` / `self.shop_id` / `self._host` everywhere blacklotus
hardcodes `BASE` / `"blacklotus"` / `"blacklotus.cz"`.

---

## 3. Search endpoint

`GET {base_url}/en-WW/query?name=<term>` on **both** domains (spaces → `+`). Confirmed
identical scheme on `.eu` and `.nl`.

- Apex → `www.` is a **301**; the shared httpx client already sets `follow_redirects=True`
  (`http_client.py`), so pointing `base_url` at `https://www.<host>` avoids the hop entirely.
- Results page: server-rendered. Magic singles render inside
  `div.product-list-visual.landing > div.grid-x... > div.cell > div.singles`.
- Each tile links to the detail page: **`{base_url}/en-WW/p/<slug>/<numeric-id>`**
  e.g. `https://www.bazaarofmagic.eu/en-WW/p/lightning-bolt-magic-2011/4065135`.
- Sealed products / accessories use `div.products` tiles (same inner structure, plus a
  `span.brand`) — we deliberately skip these.

Search-URL builder:
```python
def _search_url(self, query: SearchQuery) -> str:
    return f"{self.base_url}/en-WW/query?{urllib.parse.urlencode({'name': query.name})}"
```

---

## 4. Offer extraction

**Decision: parse the search-results `div.singles` tiles directly.** The tile already
carries name, set, price, product id, and an in/out-of-stock signal — so no per-offer
detail fetch is needed (avoids N requests for a card like Lightning Bolt that returns
4–16 tiles). Detail-page JSON-LD is kept as an *optional* enrichment path behind
`enrich_detail`, mirroring blacklotus.

Tile selector: `tree.css('div.singles')`. Per tile:

| Field | Source |
|---|---|
| detail URL + product id | `div.thumb a[href]` → `/en-WW/p/<slug>/<id>`; id = last path segment |
| product id (redundant) | `div.name a.list.toggle[data-id]` |
| full name+set+foil | link **`title`** attr: `"More information about <Card [(#coll)] [(foil)]> - <Set>"` (the visible `a.header` text and `img[alt]` are the card name **only**, no set) |
| price | `div.price-display span.nowrap` → text `"€ 2,60"` (HTML `&euro; 2,60`, comma decimal) |
| in-stock boolean | button class: `a.button.cta.cart.buy` / `title="Add to shopping basket"` = **in stock**; `a.button.cta.alert` / `title="Keep me informed"` (a "notify me" bell) = **out of stock** |

Real snippet — **Bazaar of Magic** (`/en-WW/query?name=Lightning+Bolt`):
```html
<div class="singles"> <div class="thumb"> <a
  href="https://www.bazaarofmagic.eu/en-WW/p/lightning-bolt-magic-2011/4065135"
  title="More information about Lightning Bolt - Magic 2011">
  <img src="https://www.bazaargames.nl/images/cards/l/m11/lightning_bolt.jpg"
       alt="Lightning Bolt" height="310" width="222" loading="lazy"></a></div>
<div class="name"> <a class="float-right list toggle" data-list="star" data-id="4065135" ...></a>
  <a href=".../p/lightning-bolt-magic-2011/4065135" class="header"> Lightning Bolt </a></div>
<div class="price-display clearfix"><div class="float-left text-left">
  <span class="nowrap">&euro; 2,20</span> </div><div class="float-right">
  <a href=".../p/lightning-bolt-magic-2011/4065135" class="button cta alert"
     title="Keep me informed"><i class="fa-regular fa-bell"></i></a>
</div></div></div>
```
(An in-stock tile instead shows `<a class="button cta cart buy" title="Add to shopping basket"><i class="fa-cart-shopping-fast">`.)

Real snippet — **Spellenwinkel** (`/en-WW/query?name=Sol+Ring`) — note `div.products`, not
`div.singles`, so it is skipped by the singles selector:
```html
<div class="products"> <div class="thumb"> <a
  href="https://www.spellenwinkel.nl/en-WW/p/gamegenic-magic-the-gathering-prime-playmat-.../9152601"
  title="More information about Gamegenic Magic: The Gathering - Prime Playmat: ...">
  <img src="https://www.bazaargames.nl/images/products/225x200/9152601_1.jpg" alt="..."></a></div>
<div class="name"> <span class="brand">Gamegenic</span>
  <a class="float-right list toggle" data-list="star" data-id="9152601" ...>
  ...</div>
<div class="price-display clearfix">...<span class="nowrap">&euro; 24,95</span>...
  <a class="button cta cart buy" title="Add to shopping basket">...</a></div></div>
```

**Detail-page JSON-LD** (identical on both domains; the enrichment/fallback source).
`GET /en-WW/p/lightning-bolt-magic-2011/4065135` → one `<script type="application/ld+json">`
with `@type:"Product"`:
```json
{"@type":"Product","name":"Lightning Bolt - Magic 2011","sku":4065135,
 "category":"Magic 2011",
 "offers":{"@type":"Offer","priceCurrency":"EUR","price":2.2,
   "availability":"https://schema.org/OutOfStock",
   "itemCondition":"https://schema.org/NewCondition","seller":{...}}}
```
JSON-LD paths: price = `.offers.price` (float), currency = `.offers.priceCurrency`,
in/out = `.offers.availability` (`.../InStock` vs `.../OutOfStock`), grade =
`.offers.itemCondition` (always `.../NewCondition`), id = `.sku`, set = `.category`,
name = `.name`. (The detail page also has raw `class="price"` spans, but there are 3 of
them incl. a "related products" block, so they're ambiguous — **prefer JSON-LD**.)

Name/set/foil parser (used for both the tile `title` attr and the JSON-LD `name`):
pattern `"<Card> [(#<collector>)] [(foil)] - <Set>"`. Split on the **last** ` - `:
right = set/edition, left = card name (strip trailing `(#284)` / `(foil)` / treatment
tokens). Examples seen live:
- `Lightning Bolt - Magic 2011`
- `Sol Ring (#284) - Lord of the Rings: Tales of Middle-Earth Commander`
- `Sol Ring (foil) - Kaladesh Inventions`

---

## 5. Field mapping (→ `Offer`)

| `Offer` field | Value | Source / rule |
|---|---|---|
| `shop` | `self.shop_id` | `"bazaarofmagic"` or `"spellenwinkel"` |
| `card_name` | card name w/ parenthetical tokens stripped | left of ` - ` in tile `title` / JSON-LD `name` |
| `edition` | set name | right of ` - ` (tile `title`) / JSON-LD `.category` |
| `set_code` | `None` | platform exposes set **name** only, no 3-letter code |
| `condition` | `Condition.NM` | single grade — `NewCondition` → NM (see §7); never `UNKNOWN` |
| `language` | `None` | not exposed (default `en-WW` storefront) |
| `foil` | `True` if `(foil)` token in name; treatments (`borderless`/`showcase`/`etched`) → keep in name, `foil=False` unless also `(foil)` | from `title` / JSON-LD `name` |
| `price_czk` | `round(price_eur * self._eur_to_czk)` (int) | tile `span.nowrap` `"€ 2,60"` → `2.60`; or JSON-LD `.offers.price` when enriching (§6) |
| `stock_qty` | `1` if in-stock else `0` | **boolean only** — cart-button vs bell (tile) / `InStock` vs `OutOfStock` (JSON-LD). Exact qty is JS-filled (empty `<span class="quantity" data-id=...>`), so we cannot read a real count |
| `url` | `{base_url}/en-WW/p/<slug>/<id>` | tile `a[href]` |
| `shop_ref` | numeric product id as str | URL tail / `data-id` / JSON-LD `.sku` |

`_parse()` applies the same three filters blacklotus does: `query.name.lower() in
card_name.lower()`; if `query.edition`, substring-match against `edition`; if
`query.in_stock_only`, drop `stock_qty <= 0`.

---

## 6. Currency / FX

Prices are **EUR**. Precedent: `cardmarket.py` uses `MKM_EUR_TO_CZK` (default `24.5`) and
converts `price_czk = int(round(price_eur * rate))`.

Interim per-platform FX (pending the multi-currency refactor): one shared env var
**`CZ_MTG_BAZAARGAMES_EUR_TO_CZK`** (default `24.5`), plus a constructor `eur_to_czk`
override — both shops run the same platform and price in the same currency, so a single
knob is cleaner than two. (If per-shop control is later wanted, add
`CZ_MTG_{SHOP}_EUR_TO_CZK` fallbacks.)

Price parsing from the tile string:
```python
raw = unescape(span.text(strip=True))            # "€ 2,60"
digits = raw.replace("\xa0", " ").strip().lstrip("€").strip().replace(".", "").replace(",", ".")
price_eur = float(digits)                         # 2.60
price_czk = int(round(price_eur * self._eur_to_czk))
```
When enriching via detail JSON-LD, use `.offers.price` (already a float) directly.

Document the new env var in the README **Configuration reference** table (§12).

---

## 7. Condition normalization

The platform sells a **single grade** — every product's JSON-LD carries
`itemCondition: "https://schema.org/NewCondition"` and there is **no per-condition ladder**
(no NM/EX/LP/PL variants, no condition selector on the detail page). Map that single grade
to `Condition.NM` unconditionally (`normalize_condition("mint")`/`"near mint"` → `NM`
already exists in `normalize.py`; simplest is to hardcode `condition = Condition.NM`).

Do **not** leave it `UNKNOWN` — `NewCondition` is a definite grade. If a future page ever
lacks `NewCondition`, fall back to `Condition.NM` (the platform only sells new).

---

## 8. Non-playable filtering

Restricting extraction to `div.singles` tiles already **structurally excludes** all sealed
products and accessories (playmats, sleeves, deckboxes, boosters, bundles) — those render
as `div.products`. This is the primary defense, and it also stops accessories whose title
happens to contain the searched card name (e.g. a "…Sol Ring" playmat) from leaking in via
the substring name filter.

The existing `filters.filter_playable()` (Art Series / oversized / helper / spindown, etc.)
still runs in the aggregator and remains a belt-and-suspenders backstop. No new patterns
are required for these two shops.

---

## 9. Account features (login / cart)

**Defer.** Set `supports_login = supports_cart = supports_watchlist = False`. Rationale:
the "Bazaar Games" platform is custom (not Shoptet/Prestashop), and the cart is JS/AJAX —
no server-rendered `addCartItem` form or `priceId`-style input was found on the detail page
(the favorite/quantity widgets are `data-id`-driven client-side). Login/cart would need
reverse-engineering the AJAX endpoints, which is out of scope for the read-only first cut.

We still capture `shop_ref` (the numeric product id) on every offer so a future account
feature has the handle it needs.

---

## 10. Risks & blockers

1. **Spellenwinkel has no MTG singles catalog (highest-impact finding).** Confirmed live:
   `/en-WW/magic/singles` → **404**; `/en-WW/query?name=…` returns only `div.products`
   (sealed/accessories), **zero `div.singles`** for Lightning Bolt, Sol Ring, etc.; the
   Magic mega-menu links only `/c/magic-the-gathering/1000382` with no singles subcategory.
   The shared adapter is still correct and cheap for `spellenwinkel` — it will just return
   an empty offer list for singles queries. Ship it, but set expectations: Spellenwinkel
   contributes ~nothing to singles price comparison today. (If it ever adds singles, the
   adapter picks them up automatically.)
2. **No per-condition data.** Single `NewCondition` grade only; all offers are `NM`. Users
   cannot compare played-condition prices here.
3. **Stock quantity is JS-only.** We can only surface an in/out-of-stock **boolean**
   (→ `stock_qty` 1/0). The `<span class="quantity" data-id=…>` is empty in server HTML.
4. **Cloudflare bot filtering.** The generic WebFetch UA got **HTTP 403**; a realistic
   browser UA worked. The project's default client UA is
   `cz-mtg-compare-mcp/0.1 (+https://github.com/your-org/cz-mtg-compare-mcp)`
   (`http_client.py`) — **verify it isn't 403'd**; if it is, the adapter must send a
   realistic browser `User-Agent` (per-request header override) for these hosts.
5. **Two domains kept in sync** — mitigated by the single shared class; the only per-shop
   differences are `base_url` / `shop_id` / host.
6. **Treatment-vs-foil disambiguation.** Foil is the `(foil)` token; treatments
   (borderless / showcase / etched / `(#284)` collector numbers) also appear as
   parenthetical tokens in the name. Parse carefully: only `(foil)` sets `foil=True`;
   keep other tokens (or strip to a clean `card_name` and drop them). Distinct treatments
   are **distinct products** with their own id/price/URL (no on-page variant selector).
7. **Apex→www 301** (handled by pointing `base_url` at `www.` + `follow_redirects=True`)
   and **`bazaarofmagic.nl` TLS cert mismatch** (use `.eu`).

---

## 11. Tests & fixtures

Capture a fixture from **both** domains (put under `tests/fixtures/`), using a realistic
browser UA:

```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
# Bazaar of Magic — search (has div.singles) + one detail (JSON-LD Offer)
curl -sSL -A "$UA" -o tests/fixtures/bazaarofmagic_lightning_bolt.html \
  "https://www.bazaarofmagic.eu/en-WW/query?name=Lightning+Bolt"
curl -sSL -A "$UA" -o tests/fixtures/bazaarofmagic_detail_lightning-bolt-magic-2011.html \
  "https://www.bazaarofmagic.eu/en-WW/p/lightning-bolt-magic-2011/4065135"
# Spellenwinkel — search returns only sealed/accessories (no div.singles)
curl -sSL -A "$UA" -o tests/fixtures/spellenwinkel_sol_ring.html \
  "https://www.spellenwinkel.nl/en-WW/query?name=Sol+Ring"
```
Also capture a Bazaar search for a card with a **foil** variant (e.g. `Sol+Ring` →
"Sol Ring (foil) - Kaladesh Inventions") so a test can assert foil detection.

`tests/test_bazaargames_adapter.py` (mirrors `test_blacklotus_adapter.py`, uses the
`load_fixture` conftest fixture), **parametrized over the two shops**:

```python
@pytest.mark.parametrize("shop_id, base_url, fixture", [
    ("bazaarofmagic", "https://www.bazaarofmagic.eu", "bazaarofmagic_lightning_bolt.html"),
    ("spellenwinkel", "https://www.spellenwinkel.nl", "spellenwinkel_sol_ring.html"),
])
```

Assertions:
- **Bazaar**: `offers` non-empty; every `o.shop == "bazaarofmagic"`,
  `o.price_czk > 0`, `o.condition == Condition.NM`, `o.url.startswith(base_url)`,
  `o.shop_ref` is a numeric string, `o.edition` populated (e.g. "Magic 2011"); at least
  one foil offer from the foil fixture; `in_stock_only=True` yields a subset with
  `stock_qty > 0`.
- **Spellenwinkel**: parsing yields **`offers == []`** (no `div.singles`) — documents the
  §10 finding as an explicit, intentional test.
- **Detail JSON-LD** unit test: feed the detail fixture to the extractor and assert
  price=2.2 EUR→CZK, `availability` mapped to `stock_qty == 0` (OutOfStock),
  `condition == NM`, `shop_ref == "4065135"`, `edition == "Magic 2011"`.
- FX: instantiate with `eur_to_czk=25.0` and assert conversion math.

(A live smoke test in `test_live_smoke.py` is optional and network-gated like the others.)

---

## 12. Implementation checklist

1. `src/cz_mtg_compare/models.py` — add **both** `"bazaarofmagic"` and `"spellenwinkel"`
   to the `ShopId` `Literal` **and** to `ALL_SHOPS`.
2. `src/cz_mtg_compare/adapters/bazaargames.py` — new `BazaarGamesAdapter(ShopAdapter)`:
   constructor takes `shop_id` + `base_url` (+ `eur_to_czk`, `enrich_detail`); implement
   `search()`, `parse()`, `_parse()`, tile → `Offer` mapping (§4–§7), price/FX (§6),
   optional `_enrich_offers()`/`_apply_detail()` JSON-LD path. Account-feature flags all
   `False`.
3. `src/cz_mtg_compare/adapters/__init__.py` — import `BazaarGamesAdapter`, add to
   `__all__`, and register **two instances** in `build_default_adapters()`:
   ```python
   BazaarGamesAdapter(shop_id="bazaarofmagic", base_url="https://www.bazaarofmagic.eu"),
   BazaarGamesAdapter(shop_id="spellenwinkel", base_url="https://www.spellenwinkel.nl"),
   ```
   (One class, two instances, different `base_url`. `CZ_MTG_DISABLED_SHOPS` filtering and
   `Aggregator.get_adapter()` work unchanged since they key off `shop_id`.)
4. `tests/fixtures/` + `tests/test_bazaargames_adapter.py` — per §11.
5. **README.md** (required by `CLAUDE.md` for user-facing change):
   - **Supported shops** table: two rows, e.g.
     `` `bazaarofmagic.eu` `` — "HTML scrape (Bazaar Games) + JSON-LD Offer — name,
     edition, foil, stock (in/out), price; single NM grade; EUR→CZK", and
     `` `spellenwinkel.nl` `` — same mechanism, note "sealed/accessories focus, thin
     singles catalog".
   - **What this is** / intro: bump the shop count and mention EU (NL) EUR shops.
   - **Configuration reference** table: add `CZ_MTG_BAZAARGAMES_EUR_TO_CZK` (default `24.5`).
   - **How it works under the hood**: note the new NL EUR adapter (shared class, two
     instances, JSON-LD/tile extraction, in/out-of-stock boolean, single NM grade).
   - **Limitations**: no per-condition data, stock is in/out only, Spellenwinkel singles
     catalog is thin/empty.
6. Run `pytest` (fast suite) + typecheck/lint; confirm the README diff is non-empty.

---

## 13. Effort estimate

**~1 to 1.5 days.** Roughly: adapter class + tile/JSON-LD parsing ~3–4h; models/registry
wiring ~15m; fixtures + parametrized tests ~2–3h; README updates ~45m; buffer for the
Cloudflare-UA verification (risk #4) and foil/treatment title-parsing edge cases ~2h. The
bulk of the value is Bazaar of Magic; Spellenwinkel is near-free to include via the shared
class but delivers little singles data today.
