# Adapter plan: MTGSpot (mtgspot)

> **Recon verdict: HIGH** (upgraded from the pre-recon MEDIUM). A clean, undocumented
> JSON API was found that returns per-condition singles **with prices**. The
> `window.__NUXT__` parsing path is **dead** (offers are not in the SSR payload). The
> adapter is a near-clone of `najada.py`.

---

## 1. Identity

| Field | Value |
|-------|-------|
| `shop_id` | `"mtgspot"` |
| `base_url` | `https://mtgspot.pl` |
| API host (data) | `https://gateway.mtgspot.pl` |
| Country / currency | Poland / **PLN** |
| Platform | Nuxt.js/Vue SPA over a Sylius (Symfony) backend, fronted by a custom API gateway |
| Rate-limit host slot | `host_slot("gateway.mtgspot.pl")` (all data requests go to the gateway, not `mtgspot.pl`) |

Registration touch-points:
- `src/cz_mtg_compare/models.py` — add `"mtgspot"` to the `ShopId` `Literal` **and** to `ALL_SHOPS`.
- `src/cz_mtg_compare/adapters/__init__.py` — import `MtgspotAdapter`, add to `__all__`, and append `MtgspotAdapter()` to the `candidates` list in `build_default_adapters()`. No credentials gating needed (search is anonymous), so it goes in the always-on list like `najada` — not the conditional cardmarket block.

---

## 2. Starting template

**`najada.py`** (JSON API), not `blacklotus.py`.

Rationale: recon found a first-class JSON endpoint that returns structured offers with
prices. najada's shape is a near-exact match:
- `search()` builds a URL + params, GETs JSON with custom headers under a `host_slot`, calls `parse()`.
- `parse(html, query)` re-uses the ABC slot to accept a **JSON string** (fixture-friendly), `json.loads`, then `_parse_payload`.
- `_parse_payload` iterates results, applies `query.name` / `query.edition` / `in_stock_only` filters, and builds `Offer`s.
- Condition handling identical (`Condition[cond.upper()]` with a `normalize_condition` fallback).

Borrow the **FX handling** from `cardmarket.py` (constructor arg + env-var default; see §6).

The `blacklotus`-style `__NUXT__` approach was investigated and **rejected** — see §4 and §10.

---

## 3. Search endpoint

### 3a. The JSON gateway (THE crux — this is what we use)

```
GET https://gateway.mtgspot.pl/api/shop/articles
```

Required headers (the gateway is a thin auth proxy in front of Sylius):

| Header | Value | Notes |
|--------|-------|-------|
| `X-Api-Key` | `b3d39321-5dc4-4298-98c6-0399432a948b` | **Public** SPA key, shipped to every anonymous browser. Found in the page config (`config.public.apiKey`). Reverse-engineered → treat as brittle; see §10 for a live-scrape fallback. |
| `X-Game-Id` | `1` | MTG. The SPA defaults to `"1"` when no game is selected (`initGameHeaders`). Other TCGs (Lorcana, Star Wars, …) use other ids — MTG is `1`. |
| `Accept` | `application/json` | |
| `Origin` / `Referer` | `https://mtgspot.pl` / `https://mtgspot.pl/single` | belt-and-suspenders; not strictly enforced |

**Query params — Sylius API-Platform filter syntax.** This is the second half of the
crux: the SPA passes a flat object through a `$prepareFilters` transform that rewrites
every key. The transform (recovered from the bundle) is:
- `limit`, `offset` → `page[limit]`, `page[offset]`
- `sort` → `sort` (bare)
- everything else → `filter[<key>]`
- empty / `null` values are dropped

So the wire format the adapter must emit is:

| Param | Example | Meaning |
|-------|---------|---------|
| `filter[name]` | `Lightning Bolt` | **The search.** Substring/token match on card title. (`filter[search]` / `filter[title]` / `filter[phrase]` are **ignored** — verified live; only `filter[name]` narrows results.) |
| `filter[id_category]` | `1` | Restrict to the **Singles** category. Verified: drops "Full Set", "Booster Box", "Lots", decks, gift boxes. |
| `filter[is_in_channel]` | `1` | In active sales channel / effectively in-stock. Verified: `Lightning Bolt` singles total 72 → 15 with this flag; wire to `query.in_stock_only`. |
| `sort` | `name` | The SPA default. |
| `page[limit]` | `100` | Cap results. `total` for a bare query is the whole catalog (~147k), so a limit is mandatory. |
| `page[offset]` | `0` | Pagination (optional; one page is plenty for a name lookup). |
| `filter[is_foil]` | `1` | optional |
| `filter[condition]` | `NM` | optional |
| `filter[id_expansion]` | `3746` | optional; we don't have a set-name→id map, so filter edition **client-side** on `expansion_name` instead. |

Example (URL-encoded) confirmed working live:
```
https://gateway.mtgspot.pl/api/shop/articles?filter%5Bname%5D=Lightning+Bolt&filter%5Bid_category%5D=1&filter%5Bis_in_channel%5D=1&sort=name&page%5Blimit%5D=100
```

Implementation note: httpx will URL-encode `filter[name]` etc. correctly if you pass a
plain dict `{"filter[name]": query.name, "filter[id_category]": 1, ...}` as `params`.

### 3b. HTML page (NOT used for data)

`GET https://mtgspot.pl/single?search=<name>` returns HTTP 200 (~523 KB) but the
**search results are not server-rendered** — the DOM has zero occurrences of the queried
card name; the list is fetched client-side from the gateway after hydration. Prices
(`zł`) appear **0 times** in the HTML. This page is useful only as a human-facing
`Offer.url` target, not for parsing.

---

## 4. Offer extraction

### Primary (and only viable) path: the gateway JSON

Response envelope (the gateway wraps the Sylius payload in `response`):

```json
{
  "response": {
    "total": 72,
    "data": [
      {
        "type": "shop/articles",
        "id": "e134c9347d53199",
        "attributes": {
          "title": "Lightning Bolt",
          "description": "",
          "language": "English",
          "condition": "NM",
          "is_foil": false,
          "is_signed": false,
          "is_playset": false,
          "is_altered": false,
          "id_article": null,
          "id_product": 666173,
          "id_expansion": 3746,
          "id_category": 1,
          "category_name": "Single",
          "expansion_name": "Commander: Marvel Super Heroes: Extras",
          "rarity": "",
          "stock": 33,
          "price": "8.41",
          "image": "https://gateway.mtgspot.pl/images/singles/666173/666173.png",
          "type": "single",
          "metadata": {
            "types": null, "subtypes": null, "text": null,
            "flavor": null, "mana_const": null, "power": null, "toughness": null
          }
        },
        "links": { "self": "/shop/articles/e134c9347d53199" }
      }
    ]
  }
}
```

Parsing (mirror najada's `parse` → `_parse_payload`):
1. `json.loads(text)`; guard empty/invalid → `[]`.
2. `data = payload["response"]["data"]` (defensively `.get`). `total = payload["response"]["total"]`.
3. For each item take `a = item["attributes"]`. Skip non-singles: keep only
   `a["type"] == "single"` (equivalently `a["id_category"] == 1`) — the `filter[id_category]=1`
   param already does this server-side, but double-check client-side because the search is broad.
4. Name filter: `query.name.lower() in a["title"].strip().lower()` (broad match returns e.g.
   `"Art Series: Lightning Bolt"`, `"Emeritus of Conflict // Lightning Bolt"` — the substring
   check keeps genuine matches; Art Series is then dropped by `filters.py`, see §8).
5. Edition filter: if `query.edition`, require it as a substring of `a["expansion_name"]`.
6. Stock filter: if `query.in_stock_only`, require `stock > 0` (belt-and-suspenders on top of `filter[is_in_channel]=1`).
7. Build the `Offer` (see §5).

Live-verified data points for `Lightning Bolt` (proves prices flow):
`8.41 PLN / NM / non-foil / Commander: Marvel Super Heroes: Extras / stock 33`,
`21.98 PLN / NM / foil / …Extras / stock 6`,
`4927.75 PLN / NM / Summer Magic / stock 0`. Conditions seen across the result set: NM, EX, GD, LP.

### Fallback: parse `window.__NUXT__` — REJECTED

`window.__NUXT__` **is** present (function-arg-compressed, one occurrence) but it contains
only: site `config` (incl. the `apiKey`/`gatewayApi` values), i18n strings, and a **static
expansions reference list** (~710 `attributes` entries that are set names, e.g. "Marvel Super
Heroes", "Secrets of Strixhaven"). It contains **zero** offer records: `price`, `condition`,
`is_foil`, `expansion_name` all occur **0 times**. Offers are fetched client-side only.
Therefore there is no HTML/`__NUXT__` fallback for prices — the gateway JSON is the sole
source. (The un-minify "Nuxt dance" would buy us nothing here.)

---

## 5. Field mapping (→ `Offer`)

| `Offer` field | Source (`a = item["attributes"]`) | Transform |
|---------------|-----------------------------------|-----------|
| `shop` | — | literal `"mtgspot"` |
| `card_name` | `a["title"]` | `.strip()` (titles have a leading space); `" ".join(...split())` to collapse whitespace |
| `edition` | `a["expansion_name"]` | as-is (or `None` if empty) |
| `set_code` | *(none available)* | `None` — the API exposes only `id_expansion` (int) + `expansion_name`, no short code. Leave `None` (blacklotus does the same). |
| `condition` | `a["condition"]` | `Condition[val.upper()]` with `normalize_condition(val)` fallback → `UNKNOWN` (values are already NM/EX/GD/LP/PL/HP) |
| `language` | `a["language"]` | normalize full word → 2-letter (`"English"`→`"EN"`, `"German"`→`"DE"`, …); see §7 |
| `foil` | `a["is_foil"]` | `bool(...)` |
| `price_czk` | `a["price"]` (decimal **string**, PLN) | `int(round(float(price) * pln_to_czk))`; guard `ValueError`/`None` → skip offer (return `None` like najada) |
| `stock_qty` | `a["stock"]` | `int(...)`, default 0 |
| `url` | — | `https://mtgspot.pl/single?search=<urlencoded card_name>` (human-facing search page; a stable per-article deep link isn't exposed — `links.self` is an API path, not a site route) |
| `shop_ref` | top-level `item["id"]` (e.g. `"e134c9347d53199"`) | `str(...)`; this is the article id used by `links.self` (`/shop/articles/<id>`). Captured now for a future cart feature (§9); harmless if unused. |

Extra flags available but not mapped (no `Offer` field): `is_playset`, `is_signed`,
`is_altered`, `rarity`, `metadata.*`. Note `is_playset=True` would mean a 4-card bundle —
consider skipping those for price comparison (all were `False` in the sample).

---

## 6. Currency / FX (PLN → CZK)

Follow the **cardmarket precedent** exactly (`MKM_EUR_TO_CZK`):

- Constructor arg `pln_to_czk: float | None = None`.
- Default from env: `float(os.environ.get("MTGSPOT_PLN_TO_CZK", DEFAULT_PLN_TO_CZK))`.
- `DEFAULT_PLN_TO_CZK` ≈ `5.9` (approximate 2026 rate; document it as a user-tunable placeholder, not an authoritative rate).
- Conversion: `price_czk = int(round(float(a["price"]) * self._pln_to_czk))`.

This is the **interim per-shop FX env var** flagged in the brief. Add a `# TODO(multi-currency)`
comment noting this is a stopgap pending a real multi-currency refactor (same debt cardmarket
carries). Prices are therefore **indicative**, not exact.

---

## 7. Condition & language normalization

**Condition:** The JSON API already returns our scheme (`NM`, `EX`, `GD`, `LP`, and by
extension `PL`/`HP`). Reuse najada's approach verbatim:
```python
cond_raw = (a.get("condition") or "").strip()
condition = normalize_condition(cond_raw)
if condition is Condition.UNKNOWN and cond_raw:
    try:
        condition = Condition[cond_raw.upper()]
    except KeyError:
        condition = Condition.UNKNOWN
```
No change to `normalize.py` needed for the JSON path.

**Polish "Stan" labels are irrelevant to the JSON path.** They exist only in the SPA's DOM
filter UI (`table.state` → "Stan", `is_foil` → "Folia", `price` → "Cena") and in the
condition-picker component (`[{name:"Near mint",value:"NM"}, {name:"Excelent",value:"EX"}, …]`).
The API emits the `value` codes (`NM`/`EX`/…), never the Polish `name`s. Document this so a
future maintainer doesn't add dead Polish→Condition mappings.

**Language:** the API returns full English words (`"English"`, `"German"`, …). Add a small
local map in the adapter (or a `normalize_language` helper in `normalize.py`) →
`"English"→"EN"`, `"German"→"DE"`, `"French"→"FR"`, `"Italian"→"IT"`, `"Spanish"→"ES"`,
`"Japanese"→"JP"`, `"Portuguese"→"PT"`, `"Russian"→"RU"`, `"Korean"→"KO"`,
`"Chinese Simplified"/"Chinese Traditional"→"ZH"`; unknown → keep raw. (najada already
returns 2-letter codes, so this normalization keeps `Offer.language` consistent across shops.)

---

## 8. Non-playable filtering

Two layers:
1. **Server-side:** `filter[id_category]=1` restricts to the Singles category, dropping
   packed products (sets/decks/boosters/gift boxes/lots). Confirmed via the live
   `category_name` distribution (`Single`, `Zestawy i decki`, `Booster Boxy`, `Lots`, …).
   Double-check client-side with `a["type"] == "single"`.
2. **Existing `filters.py`:** the aggregator already runs `filter_playable`, whose patterns
   catch `Art Series`, `oversized`, etc. The broad `filter[name]` search returns items like
   `"Art Series: Lightning Bolt (V.1/V.2)"` — these survive the substring name check but are
   removed by `filter_playable` (matches `\bart\s+series\b`). No new patterns required, though
   consider skipping `is_playset=True` articles in the adapter (they're 4-card bundles, not
   single cards).

---

## 9. Account features (login / cart)

**Feasible** via the same gateway (it proxies the full Sylius Shop API), but **defer to a
follow-up PR** — ship read-only search first (same staging najada/cardmarket used). Set
`supports_login = supports_cart = supports_watchlist = False` initially.

Endpoints recovered from the bundle (all under `gateway.mtgspot.pl`, same `X-Api-Key`/`X-Game-Id` headers):
- **Login:** `POST /api/shop/users/token` → returns `access_token` (+ refresh). The gateway
  client then sends `Authorization: Bearer <access_token>` on subsequent calls.
- **Token refresh:** `POST /api/shop/users/token-refresh`.
- **Cart:** `GET /api/shop/carts`; add item `POST /api/shop/carts/single`; per-item
  `/api/shop/carts/single/{id}`; `POST /api/shop/carts/assign`.
- **Account mgmt:** `POST /api/shop/users` (register), `/api/shop/users/activate/{token}`,
  `/api/shop/users/recover-password`, `/api/shop/users/change-password/`.

When implemented: credentials via `CZ_MTG_MTGSPOT_USER` / `CZ_MTG_MTGSPOT_PASS`
(the existing `credentials_for("mtgspot")` convention), and `shop_ref` = the article `id`
already captured in §5. Anonymous carts also work (`X-User-Id` from a `user_id` cookie), but
a logged-in `Bearer` flow mirrors najada more cleanly.

---

## 10. Risks & blockers

**Recommended extraction path: the gateway JSON API (§3a/§4).** It is clean, structured,
and actually *more* stable than the HTML-scraping adapters (blacklotus/tolarie) because it's
a typed Sylius API-Platform surface. Recon verdict: **HIGH**.

Risks, most to least important:
1. **Hard-coded public `apiKey`.** `b3d39321-…` is embedded in the JS bundle and could rotate
   on a frontend rebuild. It's a public anonymous key (every browser gets it), so rotation is
   infrequent — but if it changes, search returns 401 and the adapter is fully down.
   **Mitigation (recommend building in):** on 401 (or as a lazy one-time bootstrap), scrape the
   current key live from `https://mtgspot.pl` — it sits in the HTML as
   `apiKey:"<uuid>"` inside the `config.public` block (regex
   `apiKey:"([0-9a-f-]{36})"`). Cache it on the instance. This removes the single hardest
   dependency.
2. **No HTML/`__NUXT__` fallback for prices.** Confirmed: offers are not in the SSR payload
   (§4). So the gateway is a single point of failure — if the endpoint/filter contract
   changes, there is no degraded mode. Accept this; the endpoint is a stable Sylius pattern.
3. **Reverse-engineered, undocumented contract.** Endpoint path `/api/shop/articles` and the
   `filter[…]`/`page[…]` syntax were recovered from the minified bundle; no official docs.
   Low churn risk (API-Platform convention) but pin it with a fixture test so a shape change
   fails loudly.
4. **Broad `filter[name]` search.** Token/substring match over ~147k articles; `total` is huge
   without filters. Mandatory: `page[limit]` cap + `filter[id_category]=1` + client-side name
   filtering. Double-faced cards (`X // Lightning Bolt`) legitimately match.
5. **FX approximation.** Prices are decimal **PLN**; converted with a hard-coded env rate
   (`MTGSPOT_PLN_TO_CZK`) → indicative only, pending multi-currency refactor.
6. **No `set_code`.** Only `expansion_name` + integer `id_expansion`; `set_code` stays `None`.
7. **Language is full-word** (`"English"`) → needs the normalization map in §7.

No hard blockers. Search works anonymously; no login, no JS execution, no headless browser.

---

## 11. Tests & fixtures

Mirror `tests/test_najada_adapter.py` (JSON-fixture pattern via the `load_fixture` conftest fixture).

Fixture: **`tests/fixtures/mtgspot_lightning_bolt.json`** — a trimmed real gateway response
(the `{"response":{"total":…,"data":[…]}}` envelope) captured from
`GET /api/shop/articles?filter[name]=Lightning Bolt&filter[id_category]=1&sort=name&page[limit]=100`.
Trim `data` to ~10–15 items that deliberately include: at least one exact-title `Lightning
Bolt` single, one `is_foil: true`, one `stock: 0` and one `stock > 0`, an `"English"` language
value, and one `Art Series: Lightning Bolt` (to exercise `filter_playable`). A raw capture is
already staged in the scratchpad (`fixture_sample.json`) and can be curated into this file.

`tests/test_mtgspot_adapter.py` cases (construct the adapter with a **fixed** rate,
`MtgspotAdapter(pln_to_czk=6.0)`, so price assertions are deterministic):
- `test_parses_lightning_bolt_json`: `parse(payload, SearchQuery(name="Lightning Bolt", in_stock_only=False))` → non-empty; every offer `shop == "mtgspot"`, `"lightning bolt" in card_name.lower()`, `price_czk > 0`, `condition in Condition`, `url.startswith("https://mtgspot.pl/single")`.
- `test_in_stock_filter`: `in_stock_only=True` yields a subset with all `stock_qty > 0`.
- `test_foil_carried_through`: at least one `foil` offer.
- `test_edition_and_language`: at least one offer with non-`None` `edition`; language normalized to `"EN"`.
- `test_non_singles_and_art_series_excluded`: the `Art Series` item and any non-`single` `type` are absent from results (via `filter_playable` + the `type=="single"` guard).
- `test_empty_payload`: `parse("", query)` and `parse("{}", query)` → `[]` (robustness; also add to `test_adapter_robustness.py`).

Do **not** add a live network test to the default suite; if desired, gate a smoke check in
`test_live_smoke.py` behind the existing opt-in marker.

---

## 12. Implementation checklist

1. `src/cz_mtg_compare/models.py` — add `"mtgspot"` to `ShopId` `Literal` **and** `ALL_SHOPS`.
2. `src/cz_mtg_compare/adapters/mtgspot.py` — new `MtgspotAdapter(ShopAdapter)`:
   - Constants: `SHOP_BASE = "https://mtgspot.pl"`, `GATEWAY = "https://gateway.mtgspot.pl"`,
     `ENDPOINT = "/api/shop/articles"`, `API_KEY = "b3d39321-…"`, `GAME_ID = "1"`,
     `DEFAULT_PLN_TO_CZK = 5.9`, `SINGLES_CATEGORY = 1`.
   - `__init__(self, *, pln_to_czk: float | None = None)` (cardmarket-style FX).
   - `search()`: build the `filter[…]`/`page[…]`/`sort` params dict (wire `filter[is_in_channel]`
     & the stock guard to `query.in_stock_only`), `get_client()`, `async with host_slot("gateway.mtgspot.pl")`,
     GET with `X-Api-Key`/`X-Game-Id`/`Accept`/`Origin`/`Referer` headers, `raise_for_status()`, `await self.parse(resp.text, query)`.
     Optional: on 401, live-scrape the key (§10.1) and retry once.
   - `parse()` / `_parse_payload()` / `_article_to_offer()` per §4–§5; small `_normalize_language` helper per §7.
3. `src/cz_mtg_compare/adapters/__init__.py` — import + `__all__` + append `MtgspotAdapter()` to `candidates`.
4. `tests/fixtures/mtgspot_lightning_bolt.json` + `tests/test_mtgspot_adapter.py` (§11).
5. **README.md** (mandatory per `CLAUDE.md`, user-facing change):
   - "Shops covered" table (line ~54): add `mtgspot.pl` row — Mechanism "JSON API (`gateway.mtgspot.pl`)",
     Covered fields "name, edition, condition, language, foil, stock count, price (PLN→CZK)".
   - "What this is" / shop-list bullets and the ASCII pipeline diagram (line ~577): add `mtgspot`.
   - **Configuration reference** table (line ~464): add `MTGSPOT_PLN_TO_CZK` (default `5.9`).
   - Account features table (line ~364): add `mtgspot` → Login ❌ / Cart ❌ / Watchlist ❌ (planned).
   - Limitations: note PLN prices via approximate FX (indicative), reverse-engineered API, no `set_code`.
   - Add a "What you can ask Claude" example mentioning a Polish shop / mtgspot if useful.
6. Verify: `ruff check`, `mypy`, `pytest tests/test_mtgspot_adapter.py` — and confirm the README
   diff is non-empty before opening the PR.

---

## 13. Effort estimate

**~0.5 day (4–6 h). Low–medium complexity.**

It's essentially `najada.py` (JSON API, `parse`-slot fixture pattern) + cardmarket's FX arg,
with a thin field-mapping layer and a language-normalization helper. No auth, no JS, no
headless. The recon (the genuinely hard part — locating the gateway, the `X-Api-Key`/`X-Game-Id`
headers, and the `filter[name]` / `$prepareFilters` contract) is **done and live-verified**.
Remaining work is mechanical: adapter (~120 LOC), fixture capture/curation, tests, README.
Account features (login/cart) are a separate follow-up PR of comparable size when wanted.
