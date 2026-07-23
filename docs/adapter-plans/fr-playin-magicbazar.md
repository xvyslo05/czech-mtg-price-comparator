# Adapter plan: Playin / Magic Bazar (playin)

Verdict: **MEDIUM-LOW to build today; MEDIUM-HIGH once the GraphQL search endpoint
is captured.** Effort: **Medium (~1–1.5 days)** — but see the hard blocker in §3/§10
first, because it changes *what* you build.

Recon was performed live (realistic Chrome UA) against `https://www.play-in.com`.
`magicbazar.fr` 301-redirects to `play-in.com` (same company). The site is
**Next.js 15 App Router with RSC streaming** (`self.__next_f.push([1,"…"])`) and an
**Apollo GraphQL** client running **client-side**. Two saved captures back every
claim below:

- `recherche?q=Lightning+Bolt` — 560 KB HTML, 72 `__next_f.push` chunks.
- `carte/2337/foudre` (Lightning Bolt = FR "Foudre", id `2337`) — 1.21 MB HTML,
  166 `__next_f.push` chunks → 43 fully-parsed offer objects.

**The single most important finding:** the offer data you want *is* server-rendered
as clean JSON inside the RSC stream — **but only on the `/carte/{id}/{slug}` detail
page, not on the search page.** The search page renders its card-singles results
**client-side via Apollo GraphQL**; the server HTML and the RSC flight both contain
**zero** card offers (verified: `0` `CardProduct` / `0` `CardDeclination` in both the
plain HTML and the `RSC: 1` flight). So httpx-without-JS can parse offers beautifully
*once it has a card id*, but it currently has **no confirmed way to turn a card name
into an id.** That gap — not the RSC parsing — is what makes this MEDIUM-LOW.

---

## 1. Identity

| Field | Value |
|-------|-------|
| `shop_id` | `playin` |
| `base_url` | `https://www.play-in.com` |
| host (for `host_slot(...)`) | `www.play-in.com` |
| Country / currency | France / **EUR** |
| Platform | Next.js 15 App Router (RSC streaming) + Apollo GraphQL (client-side); Cloudflare in front |
| New class | `PlayinAdapter` in `src/cz_mtg_compare/adapters/playin.py` |
| Locale | URLs are locale-prefixed: `/fr/...` (French) and `/en/...` (English). Use `/en/` if you want English card names without translating FR→EN. |

Model changes (`src/cz_mtg_compare/models.py`): add `"playin"` to both the `ShopId`
`Literal` (lines 9–17) and the `ALL_SHOPS` tuple (lines 18–26).

Registry changes (`src/cz_mtg_compare/adapters/__init__.py`): import `PlayinAdapter`,
add to `__all__`, append an instance to the `candidates` list in
`build_default_adapters()`. Read-only v1, so no credentials gate; it still honours
`CZ_MTG_DISABLED_SHOPS`.

## 2. Starting template

There is no single perfect template because the discovery layer is unresolved.
Depending on which path §3 lands on:

- **Path A (recommended — GraphQL found): template `adapters/najada.py`.** najada is
  the existing "internal JSON API" adapter: `search()` does a single GET/POST to a
  JSON endpoint, `parse()` re-uses the ABC slot to accept a saved JSON payload, and
  `_parse_payload()` walks the JSON into `Offer`s (najada lines 47–163). Playin under
  Path A is the same shape but a **POST** with a JSON GraphQL body.
- **Path B (fallback — RSC scrape of the card page): template `adapters/blacklotus.py`
  for the embedded-JSON-island idea**, but the extraction is *not* selectolax DOM —
  it is RSC-chunk reassembly (see §4). blacklotus's `_enrich_offers` (lines 68–91,
  per-URL async fan-out) is the right structural model *if* Path B ever needs to fetch
  N card pages. Path B cannot stand alone as a search adapter (no name→id source).
- **Both paths:** `adapters/cardmarket.py` (lines 104–117, 174) for the **EUR→CZK**
  constructor + env-var + `int(round(eur * fx))` pattern.

## 3. Search endpoint

**User-facing search URL (verified 200):**
`GET https://www.play-in.com/fr/recherche?q=<name>` — build with
`urllib.parse.urlencode({"q": query.name})`. Results are rendered by a client
component (`$L7e` in the flight, prop `{"lang":"fr","searchTerms":"Lightning Bolt"}`);
the card-singles list is fetched **after hydration via Apollo GraphQL**, so it is
**not present in the server response.** Concretely, on the search page:

- `CardProduct`: **0**, `CardDeclination`: **0**, `sellPrice`: 24 — but those 24
  belong to `SealedProduct` (booster boxes / featured products) and to nav widgets
  (`CommercialOffer` here is menu categories: "Nouveautés", "Meilleures ventes",
  "Soldes", "Prochaines sorties"), **not** the queried singles.
- `RSC: 1` flight fetch of the same URL: 106 KB, still `0` `CardProduct` / `0`
  `sellPrice`. Client-only, confirmed.

**No clean server route found (searched hard):**

- **No `__NEXT_DATA__`** (App Router, not Pages Router).
- **No `/_next/data/<buildId>/…json`** — that route only exists for the Pages Router;
  this is App Router, so it does not exist. No `buildId` is exposed in the HTML.
- **`?_rsc=` / `RSC: 1`** returns a `text/x-component` flight but *without* the
  client-only search results (tested above).
- **No public GraphQL endpoint located.** The app uses Apollo Client (verified:
  `ApolloError`, `graphQLErrors`, `graphql-tag` strings in
  `_next/static/chunks/0f9lcxllollqm.js` and `0.2u2bpqy.9ys.js`), but the endpoint
  URI is **not** a literal in any of the 42 chunks referenced by the two pages, and
  the string `/graphql` does not appear in them (it is env-injected or in an
  un-sampled chunk). Endpoint probes: `POST /graphql` → 307 to `/fr/graphql` (renders
  the SPA shell); `POST /api/graphql` → 404; `api.play-in.com` resolves (Cloudflare)
  but `/graphql` is unresponsive/404. **So there is no ready-made JSON search route
  to template najada on — yet.**

**How to unblock Path A (do this before writing code):** open the search page in a
real browser, DevTools → Network → filter *Fetch/XHR*, type a query, and capture the
GraphQL POST: its **URL**, `operationName`, and `variables` (the `q`/search term and
any `family: 1` = Magic filter, `searchType: "CARDS"` — both observed in the
server-rendered `FilterSet` data). That request returns the card list (each item is a
`CardProduct` with `_id`) as clean JSON, which is exactly what najada consumes. Record
the operation and endpoint in this doc, then build Path A.

**Pagination.** The search results are an Apollo-driven, filterable list (the
server-rendered `FilterSet` shortcuts expose `searchType: CARDS|SEALED_PRODUCTS` and
`family` ids, e.g. Magic = `1`). Once the GraphQL query is captured, page/limit will
be a variable on that operation (typical Apollo pattern: `first`/`after` or
`page`/`limit`) — cap at page 1 (or ~2) in v1 to respect the per-shop request budget,
mirroring the other adapters. The `/carte/{id}` detail page is **not** paginated — it
returns *all* offers for that card in one response (43 in the Lightning Bolt sample).

## 4. Offer extraction

**Primary clean source (Path A): the captured GraphQL response** — parse it exactly
like najada parses its JSON payload. Each result item is a `CardProduct` with the same
field shape documented below (the RSC stream on the detail page is the server
embedding the *same* GraphQL objects, so the field names transfer 1:1). No RSC
reassembly needed on Path A.

**Fallback source (Path B): the `/carte/{id}/{slug}` RSC stream** — fully
server-rendered, parseable with httpx + json, **no JS**. This is the concrete,
verified extraction (it works — 43/43 objects parsed on the sample):

**Step 1 — reassemble the flight.** The offers are split across ~166
`self.__next_f.push([1,"<payload>"])` calls; each `<payload>` is a JSON-encoded
string fragment. Match them and JSON-decode each fragment, then concatenate:

```python
import re, json
PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', re.S)
flight = "".join(json.loads(m.group(1)) for m in PUSH_RE.finditer(html))
# json.loads on each captured JS string handles the \" and \\ un-escaping for you.
```

(On the sample: 166 fragments → a 494 KB `flight` string.)

**Step 2 — pull out the offer objects.** The reassembled `flight` is RSC, not valid
JSON as a whole, so locate each offer by its stable anchor `"__typename":"CardProduct"`
and brace-match a balanced `{…}` object around it (respecting quotes/escapes), then
`json.loads` each object:

```python
def extract_objects(text, anchor='"__typename":"CardProduct"'):
    out = []
    for m in re.finditer(re.escape(anchor), text):
        start = text.rfind("{", 0, m.start())
        depth = 0; instr = False; esc = False; i = start
        while i < len(text):
            c = text[i]
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': instr = not instr
            elif not instr:
                if c == "{": depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        out.append(text[start:i+1]); break
            i += 1
    return [json.loads(o) for o in out]
```

**Real offer object (verbatim, un-escaped from the sample):**

```json
{
  "__typename": "CardProduct",
  "_id": 655379,
  "transName": "Foudre",
  "brandLabel": "Wizards of the coast",
  "family": { "__typename": "Family", "_id": 1, "name": "Magic", "transName": "Magic" },
  "linkedEdition": {
    "__typename": "CardEdition",
    "codeName": "EMSC",
    "transName": "Commander Marvel Super Heroes Extras",
    "releasedAt": "2026-06-26T00:00:00+02:00"
  },
  "cardData": {
    "variation": {
      "imageUrl": "https://media.play-in.com/images/cartes/mtg_msc/imported_lightning_bolt175349.png",
      "rarity": { "transName": "Unco" }
    },
    "declination": {
      "foil": "", "inked": "", "signed": "", "firstEdition": false, "scan": null,
      "grading": { "transName": "Mint/Nmint" },
      "lang":    { "transName": "Français" }
    }
  },
  "priceWithoutDiscount": 4, "isDiscounted": false, "discountedPrice": 4,
  "sellPrice": 4, "quantity": 8, "category": "…"
}
```

**Field-value distributions observed across the 43 sample offers** (use these to
build/verify the mapping, not as an exhaustive enum):

- `cardData.declination.grading.transName`: `Mint/Nmint` (25), `Exc` (8),
  `Played` (7), `Poor` (3). Play-in's full scale also includes `Good`.
- `cardData.declination.lang.transName`: `Français` (14), `Anglais` (29). Other
  languages will appear on other cards (`Allemand`, `Italien`, `Espagnol`,
  `Japonais`, …).
- `cardData.declination.foil`: `""` (34, = non-foil) vs `"1"` (9, = foil).
- `sellPrice`: EUR numbers incl. decimals (`4`, `4.5`, `7`, `12`).
- `linkedEdition.codeName`: real MTG-ish set codes — `EMSC`, `FCA`, `2X2`, `E2X2`,
  `CLB`, `CLBE`, `SLD`, `JMP`, `MB1`, `MFP`, `A25`, `ANB`, `MM2`, `PD2`, `M11`, …
  (20 distinct editions on this one card).

## 5. Field mapping (→ Offer)

| `Offer` field | Source (per `CardProduct`) | Notes |
|---------------|----------------------------|-------|
| `shop` | constant `"playin"` | |
| `card_name` | `transName` | Localized to the page locale: FR page → "Foudre", EN page → "Lightning Bolt". **Prefer fetching `/en/` (or the EN GraphQL locale)** so names match the English `SearchQuery.name` used everywhere else. If you stay on `/fr/`, apply the same `wanted in name.lower()` substring filter najada uses, but be aware FR names won't substring-match English queries — another reason to use EN. |
| `edition` | `linkedEdition.transName` | e.g. "Commander Marvel Super Heroes Extras" |
| `set_code` | `linkedEdition.codeName` | e.g. `EMSC`, `MM2`, `M11`; upper-case already |
| `condition` | `cardData.declination.grading.transName` → `normalize_condition` (see §7) | `Mint/Nmint`→NM, `Exc`→EX, `Good`→GD, `Played`→PL, `Poor`→HP |
| `language` | `cardData.declination.lang.transName` | Store as-is ("Français"/"Anglais") or map to codes; other adapters store free-form strings, so `"Français"`→`"FR"`, `"Anglais"`→`"EN"` is a nice-to-have, not required |
| `foil` | `cardData.declination.foil` | `bool(value)` — `""`→False, `"1"`→True |
| `price_czk` | `sellPrice` (fall back `discountedPrice`, then `priceWithoutDiscount`) → `int(round(eur * self._eur_to_czk))` | EUR floats; `sellPrice` is the effective price |
| `stock_qty` | `quantity` | integer, per-offer stock (e.g. 8) |
| `url` | `f"{base}/{locale}/carte/{card_id}/{slug}"` | The card-level detail URL. `card_id` is the **card** id from the search step (e.g. `2337`), *not* the per-offer `_id`. Slug can be taken from the search result; the id is the load-bearing part. |
| `shop_ref` | `str(CardProduct._id)` | Per-offer product id (e.g. `655379`) — the buyable line. Distinct from the card id `2337`. Keep it for a future `add_to_cart`. |
| `fetched_at` | model default | |

Note the **two different ids**: the **card** id (`2337`, used in the `/carte/{id}`
URL) and the per-offer **`CardProduct._id`** (`655379`, the specific
edition×lang×foil×condition line). `url` uses the former, `shop_ref` the latter.

Extra `declination` axes present but not modelled by `Offer`: `signed`, `inked`,
`firstEdition`, `scan` (graded/slabbed). Ignore in v1; they mostly mark oddities you
don't want anyway.

## 6. Currency / FX

Shop is EUR; `Offer.price_czk` is `int`. Follow the **cardmarket precedent**
(`DEFAULT_EUR_TO_CZK = 24.5`, `MKM_EUR_TO_CZK` env, `int(round(eur * fx))`;
cardmarket.py lines 21, 112–116, 174):

- Constructor: `def __init__(self, *, eur_to_czk: float | None = None) -> None:` with
  `self._eur_to_czk = eur_to_czk if eur_to_czk is not None else
  float(os.environ.get("PLAYIN_EUR_TO_CZK", DEFAULT_EUR_TO_CZK))`, `DEFAULT_EUR_TO_CZK
  = 24.5`.
- Passing `eur_to_czk` explicitly in tests makes `price_czk` deterministic.

**Pending multi-currency refactor — flag it.** Playin is the third EUR shop after
cardmarket (`MKM_EUR_TO_CZK`) and the planned MagicCorporation
(`CZ_MTG_MAGICCORPORATION_EUR_TO_CZK`). Each carries a private FX rate + duplicated
`DEFAULT_EUR_TO_CZK`. Leave a `# TODO: multi-currency — centralize EUR→CZK across
adapters` comment near the constant. Do **not** do the refactor in this PR — just add
the per-shop var and flag the debt.

## 7. Condition normalization

Play-in exposes condition as `grading.transName` on a fixed 5-tier scale:
`Mint/Nmint`, `Excellent`, `Good`, `Played`, `Poor` (observed strings on the sample:
`Mint/Nmint`, `Exc`, `Played`, `Poor`). Map to `Condition` by extending
`normalize._CONDITION_MAP` (normalize.py lines 7–29 — it lowercases the key before
lookup):

- add `"mint/nmint": Condition.NM`
- add `"exc": Condition.EX` (`"excellent"` already maps to EX)
- `"good"`→GD, `"played"`→PL, `"poor"`→HP are **already** present.

There is **no LP tier** at Play-in (its "Good" sits where other shops split
GD/LP/PL). Set unmapped/empty gradings to `Condition.UNKNOWN`.

**"Occasion" / used.** There is no separate `occasion`/`used` boolean in the payload
(the brief's guess doesn't match the live schema). "Used" simply means *graded below
Mint/Nmint* — i.e. the `condition` field **is** the used signal. Mint/Nmint copies are
the "new" stock; Exc/Good/Played/Poor are second-hand. No extra flag needed; the
`condition` value carries it. (The `scan` / `signed` / graded fields mark
collector oddities, not general "used".)

## 8. Non-playable filtering

Handled centrally: `aggregator.search()` calls `filters.filter_playable()` unless
`include_non_playable` (aggregator.py lines 72–73). No adapter-side work. Caveat: the
`filters.py` regexes (`art series`, `oversized`, `spindown`, …) are **English**. If
you parse the **FR** locale, `card_name`/`edition` are French and some patterns won't
match (e.g. an "oversized"/"surdimensionné" card). This is one more reason to prefer
the **EN** locale (§1/§5). Do not expand `filters.py` here (out of scope).

## 9. Account features (login / cart)

**Defer to a follow-up PR.** v1 sets `supports_login = supports_cart =
supports_watchlist = False` (ABC defaults raise `AccountFeatureNotSupported`).

Cart/login run through the same Apollo GraphQL layer as search (mutations, likely CSRF
+ session cookie; robots.txt disallows `/*/panier` and `/*/espace-client`, confirming
those are the cart/account areas). `shop_ref` already captures the per-offer
`CardProduct._id`, which is what an add-to-cart mutation would need. Building cart
requires the same GraphQL-endpoint capture as Path A, plus the cart mutation shape —
record both when you do the DevTools capture, but implement later.

## 10. Risks & blockers

1. **BLOCKER — search results are client-only; name→id has no confirmed httpx path
   (the #1 issue, above RSC brittleness).** The `/fr/recherche` server response
   contains no card offers; they load via client-side Apollo GraphQL whose endpoint I
   could not locate in sampled chunks or by probing common paths. Until that endpoint
   (URL + operation + variables) is captured from browser DevTools, you **cannot**
   build a working name→offers adapter with httpx alone. The RSC card-page parser
   (§4) is ready and clean, but it needs an id it has no way to obtain. **Recommend:
   do the ~30-min DevTools capture first.** If it succeeds, this becomes a
   najada-style clean-JSON adapter (Path A, MEDIUM-HIGH) and the RSC brittleness below
   is *moot*. If the endpoint turns out to be auth-gated/persisted-query-locked or
   otherwise unusable, **do not ship** a search adapter — there's no viable fallback,
   and I'd advise waiting rather than shipping a non-functional shop.
2. **RSC serialization brittleness (only relevant if you go Path B).** The
   `__next_f.push` chunk reassembly + brace-matching works today but depends on an
   undocumented Next.js streaming format that changes across framework
   upgrades/config (chunk boundaries, escaping, the `CardProduct` anchor). It will
   break silently on upgrades. The parser must fail soft (return `[]`, never throw) so
   the aggregator degrades gracefully, and it must be guarded by a fixture test that
   makes breakage visible. This is why Path A (a stable GraphQL contract) is strongly
   preferred over Path B.
3. **Cloudflare + no documented API.** Live fetches succeeded with a realistic Chrome
   UA, but repeated rapid requests during recon began returning `522`/timeouts
   (throttling). Set a realistic browser `User-Agent` and rely on the shared
   `http_client` host-slot concurrency limit. There is **no documented/public API** —
   everything here is reverse-engineered and unversioned; expect churn.
4. **Locale mismatch.** FR-locale card names ("Foudre") won't substring-match English
   `SearchQuery.name` ("Lightning Bolt"). Use the `/en/` locale (or the EN GraphQL
   locale variable) to keep names/filters consistent with the rest of the app.
5. **Two-id confusion.** `card_id` (URL) vs `CardProduct._id` (`shop_ref`) are
   different numbers; mixing them breaks `url` or a future cart call. Mapped
   explicitly in §5.

## 11. Tests & fixtures

Follow `tests/test_najada_adapter.py` (JSON fixture + `adapter.parse(payload, query)`)
for Path A, and the fixture-load pattern in `tests/conftest.py`.

- **Primary fixture (works today, Path B/RSC): save the captured card-detail HTML** as
  `tests/fixtures/playin_lightning_bolt_carte.html` — the `/fr/carte/2337/foudre` page
  (already captured during recon; ~1.2 MB, contains the 166 `__next_f` chunks and 43
  offers). Test the RSC reassembly + brace-match parser against it:
  - reassembles ≥ 40 `CardProduct` objects; all parse as JSON.
  - every offer: `shop == "playin"`, `price_czk > 0`, `url` starts with
    `https://www.play-in.com/` and contains `/carte/2337/`.
  - **foil detection:** at least one `foil is True` (the `"foil":"1"` rows) and one
    `foil is False`.
  - **condition mapping:** offers with grading `Mint/Nmint`→`NM`, `Exc`→`EX`,
    `Played`→`PL`, `Poor`→`HP` are present and mapped.
  - **language:** both `Français`/`Anglais` (or mapped `FR`/`EN`) appear.
  - **stock:** an offer has `stock_qty == 8` (the sample's `quantity:8` Mint row).
  - **FX determinism:** instantiate `PlayinAdapter(eur_to_czk=25.0)`; assert a known
    `sellPrice:4` offer → `price_czk == 100`.
  - **`in_stock_only`:** with `in_stock_only=True`, all `stock_qty > 0`.
- **Search fixture (documents the gap):** optionally save
  `tests/fixtures/playin_recherche_lightning_bolt.html` and assert the parser finds
  **zero** singles offers in it — a regression guard proving the search page is
  client-only (so nobody later "fixes" the adapter by parsing the wrong page).
- **Path A fixture (once the endpoint is captured):** save the GraphQL JSON response
  as `tests/fixtures/playin_search_lightning_bolt.json` and test `_parse_payload`
  najada-style; this becomes the primary test if Path A ships.
- Add `playin` to any all-shops parametrized tests (`test_adapter_robustness.py`,
  `test_account_features_more_shops.py`, `test_aggregator*`, `test_shop_optout.py`) if
  they enumerate every shop.

(Recon HTML currently lives only in the session scratchpad — the implementer must
re-capture into `tests/fixtures/` with a realistic UA.)

## 12. Implementation checklist

- [ ] **Unblock discovery first:** DevTools-capture the Apollo GraphQL search request
      (endpoint URL, `operationName`, `variables`) and record it here. If unavailable,
      stop and re-evaluate (§10.1) — do not ship a broken adapter.
- [ ] `models.py`: add `"playin"` to `ShopId` Literal and `ALL_SHOPS`.
- [ ] `normalize.py`: add `"mint/nmint"`→NM and `"exc"`→EX to `_CONDITION_MAP`.
- [ ] `adapters/playin.py`: new `PlayinAdapter`.
  - Path A (preferred): najada-shaped `search()` POST to the GraphQL endpoint →
    `_parse_payload()` over `CardProduct` items (§5 mapping); `parse()` accepts a saved
    JSON payload for tests.
  - Path B (fallback): `search()` = discover card id (source TBD) → GET
    `/{locale}/carte/{id}` → `_parse_rsc(html)` (§4 reassembly + brace-match);
    `parse(html, query)` delegates to `_parse_rsc`. Parser must fail soft.
  - EUR→CZK ctor like cardmarket; `PLAYIN_EUR_TO_CZK` env; `DEFAULT_EUR_TO_CZK = 24.5`;
    `# TODO: multi-currency` comment.
  - `foil = bool(declination["foil"])`; `condition` via `normalize_condition`;
    `set_code = linkedEdition.codeName`; `shop_ref = str(CardProduct._id)`; `url` from
    the **card** id; `supports_* = False`. Prefer the **EN** locale.
- [ ] `adapters/__init__.py`: import `PlayinAdapter`, add to `__all__`, append to
      `candidates`.
- [ ] `tests/fixtures/playin_lightning_bolt_carte.html` (+ optional search fixture, +
      Path A JSON fixture if applicable).
- [ ] `tests/test_playin_adapter.py` (§11 cases).
- [ ] Extend all-shops parametrized tests to include `playin`.
- [ ] **README.md** (required by CLAUDE.md — user-facing change):
  - Supported shops table (line 52 / row style ~line 62): add a `play-in.com` row —
    "France, EUR; Next.js/GraphQL; name, edition/set_code, condition, language, foil,
    stock, price; EUR→CZK".
  - Configuration reference table (line 464, rows ~468–472): add
    `| PLAYIN_EUR_TO_CZK | EUR → CZK conversion rate for Playin prices | 24.5 |`.
  - "What this is" (line 38) / intro: note a **French** EUR shop was added.
  - Per-shop capability matrix (line 362, row style ~line 372): add a `playin` row
    with ❌/❌/❌.
  - Known limitations (line 456) / Limitations (line 738): note Playin search relies on
    a reverse-engineered GraphQL layer and used vs new is expressed via `condition`.
  - "How it works under the hood" (line 567): add Playin to the adapter list, noting
    the JSON/GraphQL (or RSC) pipeline.
- [ ] Run lint (`ruff`) + `mypy` (or state if not configured) and `pytest`.

## 13. Effort estimate

**Medium — roughly 1 to 1.5 days**, front-loaded by the discovery blocker:

- **~0.5 day** — DevTools capture of the GraphQL search request and confirming it's
  usable unauthenticated (the gating risk). This determines whether the project is a
  clean Path A (~½ day more) or gets shelved.
- **~0.5 day (Path A)** — najada-style POST + `_parse_payload` + FX + tests + README.
  The field mapping (§5) and condition/FX plumbing are mechanical and already fully
  specified here.
- **+~0.5 day if forced onto Path B** — the RSC reassembly parser (§4, already
  prototyped and verified) plus its fixture tests and fail-soft hardening — *but* Path
  B still can't search by name without an id source, so this only pays off if a
  discovery route appears.

If the GraphQL endpoint capture fails or is auth-gated, the honest recommendation is
**do not build now** — wait until a stable name→id path exists. The offer extraction
is solved; the discovery layer is not.
