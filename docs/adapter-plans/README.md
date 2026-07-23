# EU shop adapter plans

Implementation plans for adding non-Czech European MTG-singles shops to the
comparator. Each plan was written against a **live** recon of the shop (fetched
2026-07-21/22) plus the existing adapter/model/test code, and follows a fixed
13-section skeleton (identity → search endpoint → extraction → `Offer` field
mapping → FX → condition map → non-playable filter → account features → risks →
tests/fixtures → checklist → effort).

These are the **Tier 1 / Tier 2** candidates from the compatibility survey — the
shops that actually sell MTG singles on a server-rendered or JSON surface that the
`httpx` + `selectolax` (no-JS) stack can read. Tier 3 (anti-bot-walled) and Tier 4
(no singles / wrong catalog) shops were not planned; see the survey summary.

## The plans

| Plan file | Shop | Country | Cur | Tier | Effort | Extraction mechanism | #1 risk |
|-----------|------|---------|-----|------|--------|----------------------|---------|
| [uk-axion-now.md](uk-axion-now.md) | Axion Now | 🇬🇧 | GBP | **HIGH** | 0.5–1d | Shopify `suggest.json` → `/products/{handle}.js` JSON | pence-int vs pound-string price; stock is binary |
| [pl-mtgspot.md](pl-mtgspot.md) | MTGSpot | 🇵🇱 | PLN | **HIGH** | ~0.5d | Clean JSON API `gateway.mtgspot.pl/api/shop/articles` (X-Api-Key) | embedded API key may rotate |
| [fr-magiccorporation.md](fr-magiccorporation.md) | MagicCorporation | 🇫🇷 | EUR | **HIGH** | 4–6h | 1 GET; `select[name=variant] option[data-max]` per row | only in-stock NEW rows render; used copies deferred |
| [de-jk-entertainment.md](de-jk-entertainment.md) | JK Entertainment | 🇩🇪 | EUR | **HIGH** | ~1–2d | Shopware in-page `onEventDataLayer` JSON → `ecommerce.items[]` | dataLayer exposes only the default variant/listing |
| [nl-bazaar-spellenwinkel.md](nl-bazaar-spellenwinkel.md) | Bazaar of Magic (+ Spellenwinkel) | 🇳🇱 | EUR | **HIGH** | 1–1.5d | HTML `div.singles` tiles + detail JSON-LD Offer | Cloudflare UA gate; **Spellenwinkel has no singles**; stock JS-only |
| [de-trader-online.md](de-trader-online.md) | Trader-Online | 🇩🇪 | EUR | MED-HIGH | 4–6h | HTML tiles `div.card.product-card` | search mixes in **buylist (we-buy) prices** — must filter |
| [uk-magic-madhouse.md](uk-magic-madhouse.md) | Magic Madhouse | 🇬🇧 | GBP | MED-HIGH | 1.5–2d | BigCommerce `BODL.search.products[]` + `product-attributes` POST | Cloudflare UA gate; cross-game search; POST-per-product |
| [it-magicstore.md](it-magicstore.md) | Magic Store | 🇮🇹 | EUR | MEDIUM | 0.5–1d | HTML rows `div.s_item` (`id_cat=9`) | **card names are Italian-localized** ("FULMINE") |
| [fr-playin-magicbazar.md](fr-playin-magicbazar.md) | Playin / Magic Bazar | 🇫🇷 | EUR | MED-LOW* | 1–1.5d | Next.js `__next_f` RSC parse (detail pages only) | search API (Apollo GraphQL) not yet captured |

\* Playin is MED-LOW to build **today**; rises to MED-HIGH once the GraphQL search
endpoint is captured via browser DevTools (the RSC parser already works on
`/fr/carte/{id}` — 43 offers parsed with price/qty/foil/grading/lang). The blocker
is name→id discovery, not extraction.

## Recommended build order

1. **MTGSpot** and **Axion Now** — both are clean JSON, near-clones of `najada.py`,
   ~half a day each. Fastest path to proving the EU expansion end-to-end.
2. **MagicCorporation** and **Trader-Online** — server-rendered HTML, one/few GETs,
   4–6h each, no anti-bot.
3. **JK Entertainment** — in-page JSON, HIGH value (structured condition/foil/lang),
   ~1–2d.
4. **Bazaar of Magic** and **Magic Madhouse** — HIGH/MED-HIGH but gated behind the
   Cloudflare UA work (see below); do them together once that's solved.
5. **Magic Store** — after the localized-name matching is solved (see below).
6. **Playin** — only after someone captures the GraphQL search request in a browser.

## Cross-cutting prerequisites (read before starting ANY plan)

### 1. Multi-currency — a hard prerequisite for all nine
Every one of these shops prices in EUR / GBP / PLN, but `Offer.price_czk` is a
CZK int and the only precedent for conversion is cardmarket's `MKM_EUR_TO_CZK`
env var. Each plan proposes an **interim per-shop FX env var**
(`JK_EUR_TO_CZK`, `MTGSPOT_PLN_TO_CZK`, …), but that pattern does not scale to
nine shops. **Strongly recommended:** do the general multi-currency refactor first
— e.g. store `price` + `currency` on `Offer` and convert at presentation with one
FX source — then land the adapters on top. This is the single biggest decision and
it blocks all nine plans. (The project name/`price_czk` field and the README's
"Czech shops" framing also need revisiting.)

### 2. Anti-bot / User-Agent
- **Bazaar of Magic** and **Magic Madhouse** return **Cloudflare 403** to
  non-browser User-Agents. A browser-like UA is required; confirm the project's
  default UA (`http_client.py`) works before building these two.
- JK Entertainment, MagicCorporation, Trader-Online, and the MTGSpot gateway API
  all accept the project UA fine (verified live).

### 3. Localized card names (Magic Store, IT)
Magic Store returns Italian names ("FULMINE" for Lightning Bolt), which breaks the
English-name substring filter and weakens optimizer matching. The bridge is
Scryfall's multilingual printed names (already fetched by `scryfall.py`) — resolve
the query name to its Italian printed name before matching. Playin (FR) has the
same latent issue for `codeName`/localized fields.

### 4. Condition ladders vary a lot
Full/structured (JK via `item_id`, MTGSpot, Playin grading) → shallow (Axion NM+EX,
Magic Madhouse) → single grade / none (MagicCorporation, Magic Store, Bazaar all
default NM). Extend `normalize.py`'s condition map per shop; where no condition axis
exists, document the NM default rather than guessing.

## Not planned (from the survey)
- **Tier 3 (anti-bot-walled, otherwise good):** Distrito Zero 🇪🇸 & Fantasia Store 🇮🇹
  (both PrestaShop — ideal if the Cloudflare/IP block is solved), Chaos Cards 🇬🇧,
  Games Island 🇩🇪, Outpost 🇧🇪, Parkage 🇫🇷.
- **Tier 4 (no singles / wrong catalog):** Red Goblin, Gameology, Geek Hub, Cardhunter,
  Mazvigo, Kaissa, Efantasy, Warlock, Game Mania, Fox & Co, World's End.
- **Unidentified:** Temple of Deceit 🇪🇸, Planszówki i Karcianki 🇵🇱, Sklep Goblin 🇵🇱.
- **Better RO leads to investigate:** arcanainn.ro (~7,700 singles), shop.guildhall.ro.
