from __future__ import annotations

import asyncio
import re
import urllib.parse
from html import unescape
from typing import Any

from selectolax.parser import HTMLParser, Node

from ..credentials import CredentialError, credentials_for, has_credentials
from ..http_client import get_client, host_slot
from ..models import Condition, Offer, SearchQuery
from ..normalize import normalize_condition, parse_price_czk, parse_stock_qty
from .base import ShopAdapter

BASE = "https://www.cernyrytir.cz"
SEARCH_PATH = "/index.php3?akce=3"
LOGIN_URL = f"{BASE}/index.php3?akce=0"
# Cart endpoints reverse-engineered from the logged-in per-row "Vložit do
# košíku" form (POST search-endpoint with ``nakupzbozi=Pridat``) and the cart
# page's per-line "Uprav / Zruš" forms (POST ``kosicek=1`` with
# ``nakupzbozi=Upravit``, ``kusu=0`` to delete).
CART_URL = f"{BASE}/index.php3?akce=3&kosicek=1"
CART_ADD_URL = f"{BASE}/index.php3?akce=3"
_DATABASE = "kusovkymagic"
_NAKUP_ADD = "Pridat"
_NAKUP_EDIT = "Upravit"
PAGE_SIZE = 100  # poczob — most queries fit in a single page at 100 results.

# Cards with no real price (out of stock placeholders) are quoted at 9999 Kč.
SENTINEL_PRICE = 9999

# Cernyrytir encodes condition + foil as a textual suffix on the card name:
#   "Lightning Bolt", "Lightning Bolt - foil", "Lightning Bolt - lightly played",
#   "Lightning Bolt - foil - lightly played"
_SUFFIX_SPLIT_RE = re.compile(r"\s+-\s+")
_SET_ICON_RE = re.compile(r"/images/kusovky/([a-z0-9]+)\.gif", re.IGNORECASE)


class CernyRytirAdapter(ShopAdapter):
    shop_id = "cernyrytir"
    base_url = BASE
    supports_login = True
    supports_cart = True
    supports_watchlist = False

    def __init__(self) -> None:
        self._authenticated = False
        self._auth_lock = asyncio.Lock()

    def _request_url(self) -> str:
        return f"{BASE}{SEARCH_PATH}"

    def _result_url(self, query: SearchQuery) -> str:
        params = {
            "akce": "3",
            "jmenokarty": query.name,
            "edice_magic": "libovolna",
            "poczob": str(PAGE_SIZE),
            "triditpodle": "ceny",
            "hledej_pouze_magic": "1",
            "submit": "Vyhledej",
        }
        return f"{BASE}/index.php3?{urllib.parse.urlencode(params)}"

    async def search(self, query: SearchQuery) -> list[Offer]:
        # Auto-login when credentials are configured so each in-stock row's
        # per-product "Vložit do košíku" form (and its hidden ``carovy_kod``
        # input that backs ``Offer.shop_ref``) is included in the HTML.
        # Anonymous results render a "Pro přidání položek do košíku je třeba
        # se přihlásit" message instead, so anonymous search still works but
        # can't surface shop_refs.
        if has_credentials("cernyrytir"):
            await self._ensure_auth()
        client = await get_client()
        async with host_slot("cernyrytir.cz"):
            resp = await client.post(
                self._request_url(),
                data={
                    "jmenokarty": query.name,
                    "edice_magic": "libovolna",
                    "poczob": str(PAGE_SIZE),
                    "triditpodle": "ceny",
                    "hledej_pouze_magic": "1",
                    "submit": "Vyhledej",
                },
            )
        resp.raise_for_status()
        # Site is windows-1250 encoded; httpx may guess wrong otherwise.
        resp.encoding = "windows-1250"
        return self._parse(resp.text, query)

    async def parse(self, html: str, query: SearchQuery) -> list[Offer]:
        return self._parse(html, query)

    def _parse(self, html: str, query: SearchQuery) -> list[Offer]:
        tree = HTMLParser(html)
        rows = tree.css("tr")
        offers: list[Offer] = []
        wanted = query.name.lower()
        edition_filter = (query.edition or "").lower().strip() or None

        # Each product spans 3 consecutive rows. The first row of a product carries a
        # td with rowspan="3" wrapping the card image; subsequent rows continue the same
        # bgcolor. We walk rows and group them by leading rowspan markers.
        i = 0
        n = len(rows)
        while i < n:
            row = rows[i]
            first_td = row.css_first("td")
            if first_td is None or (first_td.attributes.get("rowspan") or "").strip() != "3":
                i += 1
                continue
            group = [row, rows[i + 1] if i + 1 < n else None, rows[i + 2] if i + 2 < n else None]
            i += 3
            if any(g is None for g in group):
                continue
            offer = self._parse_group(group, query)  # type: ignore[arg-type]
            if offer is None:
                continue
            if wanted not in offer.card_name.lower():
                continue
            if edition_filter and (
                (not offer.edition or edition_filter not in offer.edition.lower())
                and (not offer.set_code or edition_filter not in offer.set_code.lower())
            ):
                continue
            if query.in_stock_only and offer.stock_qty <= 0:
                continue
            offers.append(offer)
        return offers

    def _parse_group(self, rows: list[Node], query: SearchQuery) -> Offer | None:
        row1, row2, row3 = rows

        # ----- Row 1: name + foil/condition suffix -----
        bold = row1.css_first("font[style]")
        if bold is None:
            return None
        raw_name = unescape(bold.text(strip=True))
        if not raw_name:
            return None
        card_name, foil, condition = self._split_name_suffix(raw_name)
        # Default condition NM unless the suffix says otherwise.
        if condition is Condition.UNKNOWN:
            condition = Condition.NM

        # ----- Row 2: edition (icon + name) -----
        edition: str | None = None
        set_code: str | None = None
        for td in row2.css("td"):
            img = td.css_first("img[src]")
            if img is None:
                continue
            m = _SET_ICON_RE.search(img.attributes.get("src") or "")
            if m:
                set_code = m.group(1).upper()
                # text after the image is the edition name
                edition_text = unescape(td.text(strip=True))
                edition = " ".join(edition_text.split()) or None
                break

        # ----- Row 3: rarity, stock, price -----
        cells = row3.css("td")
        stock_qty = 0
        price_czk: int | None = None
        # The relevant cells contain a bold <font>; parse all and pick by content shape.
        for cell in cells:
            text = unescape(cell.text(separator=" ", strip=True))
            if "ks" in text and stock_qty == 0:
                stock_qty = parse_stock_qty(text)
            elif "Kč" in text and price_czk is None:
                price_czk = parse_price_czk(text)

        if price_czk is None:
            return None
        # Filter the 9999 Kč "ask us" placeholders if the user wants stocked offers.
        if price_czk >= SENTINEL_PRICE and stock_qty == 0 and query.in_stock_only:
            return None

        # ``carovy_kod`` (the per-product id used by the cart endpoint) is
        # rendered inside the row-3 "Vložit do košíku" form when the row is
        # in stock *and* the viewer is logged in. Out-of-stock rows render a
        # "Hlidat" (watch) form with the same ``carovy_kod`` but
        # ``nakupzbozi=Hlidat`` instead of ``Pridat`` — we only lift the id
        # for genuine cart-add rows so callers can't accidentally call
        # add_to_cart on a watchlist sku.
        shop_ref = self._extract_carovy_kod_for_pridat(row3)

        return Offer(
            shop="cernyrytir",
            card_name=card_name,
            edition=edition,
            set_code=set_code,
            condition=condition,
            language=None,
            foil=foil,
            price_czk=price_czk,
            stock_qty=stock_qty,
            url=self._result_url(query),
            shop_ref=shop_ref,
        )

    @staticmethod
    def _extract_carovy_kod_for_pridat(row: Node) -> str | None:
        """Return the ``carovy_kod`` from this row's per-product cart form
        only when it carries ``nakupzbozi=Pridat`` (cart-add) — not when it
        carries ``Hlidat`` (watchlist). selectolax sees the inputs from
        whichever ``<form>`` they sit in via ``row.html``; we operate on the
        raw HTML so we can correlate the two hidden inputs that live in the
        same form."""
        html = row.html or ""
        # Scan all <form>...</form> blocks inside the row and pick the one
        # whose body contains nakupzbozi=Pridat. selectolax re-serialises
        # attribute quotes to double-quotes when we read ``row.html`` even
        # though the raw site uses single quotes, so the regex below must
        # accept either.
        for form_html in re.findall(r"<form[^>]*>.*?</form>", html, re.DOTALL):
            if "nakupzbozi" not in form_html or _NAKUP_ADD not in form_html:
                continue
            m = re.search(r"""carovy_kod['"]\s*value=['"](\d+)['"]""", form_html)
            if m:
                return m.group(1)
        return None

    # --- Account features ---------------------------------------------------

    async def login(self) -> None:
        async with self._auth_lock:
            await self._login_locked()

    async def _ensure_auth(self) -> None:
        if self._authenticated:
            return
        async with self._auth_lock:
            if self._authenticated:
                return
            await self._login_locked()

    async def _get_decoded(self, url: str) -> str:
        client = await get_client()
        async with host_slot("cernyrytir.cz"):
            resp = await client.get(url, follow_redirects=False)
        resp.encoding = "windows-1250"
        body = resp.text
        # cernyrytir's session-expired flow re-renders the login form on the
        # cart page (200, no redirect) — same trick rishada pulls. Surface
        # that to the caller so they can retry once after a fresh login.
        return body

    async def add_to_cart(self, shop_ref: str, count: int = 1) -> dict[str, Any]:
        """Add ``count`` copies of cernyrytir product ``shop_ref`` (numeric
        ``carovy_kod`` captured during ``search()``) to the logged-in user's
        cart.

        Mimics the per-row "Vložit do košíku" form submit: POST
        ``databaze=kusovkymagic``, ``carovy_kod=<id>``, ``nakupzbozi=Pridat``,
        ``kusu=<count>`` to the search endpoint. The form's action is the
        current search URL but cernyrytir's PHP dispatcher reads ``nakupzbozi``
        and ``carovy_kod`` regardless of which results URL we post to, so we
        target the bare ``akce=3`` endpoint for stability.

        The shop silently clamps ``kusu`` to the row's available stock — a
        request for more than is available adds the maximum the shop has.
        """
        if not shop_ref:
            raise ValueError("shop_ref is required (cernyrytir carovy_kod)")
        if not shop_ref.isdigit():
            raise ValueError(
                f"cernyrytir shop_ref must be a numeric carovy_kod, got {shop_ref!r}"
            )
        if count < 1:
            raise ValueError("count must be >= 1")
        await self._ensure_auth()
        client = await get_client()
        payload = {
            "databaze": _DATABASE,
            "carovy_kod": shop_ref,
            "nakupzbozi": _NAKUP_ADD,
            "kusu": str(count),
        }
        async with host_slot("cernyrytir.cz"):
            resp = await client.post(
                CART_ADD_URL,
                data=payload,
                headers={"Referer": f"{BASE}/"},
                follow_redirects=False,
            )
        resp.encoding = "windows-1250"
        body = resp.text
        # If the server quietly re-rendered the anonymous search page (login
        # cookie expired), retry once after a fresh login. The anonymous
        # response carries the top-banner login form (``name='uzivjmeno'``).
        if "uzivjmeno" in body and "uzivheslo" in body and "carovy_kod" not in body:
            self._authenticated = False
            await self.login()
            async with host_slot("cernyrytir.cz"):
                resp = await client.post(
                    CART_ADD_URL,
                    data=payload,
                    headers={"Referer": f"{BASE}/"},
                    follow_redirects=False,
                )
            resp.encoding = "windows-1250"
            body = resp.text
        resp.raise_for_status()
        summary = self._parse_cart_summary(body)
        return {
            "carovy_kod": shop_ref,
            "count": count,
            "cart_total_czk": summary.get("total_czk"),
            "cart_item_count": summary.get("item_count"),
        }

    async def view_cart(self) -> dict[str, Any]:
        """Fetch the cart page (``index.php3?akce=3&kosicek=1``) and return
        the parsed list of line items plus the sidebar summary."""
        await self._ensure_auth()
        body = await self._get_decoded(CART_URL)
        # session-expired safety net (see add_to_cart)
        if "uzivjmeno" in body and "uzivheslo" in body and "carovy_kod" not in body:
            self._authenticated = False
            await self.login()
            body = await self._get_decoded(CART_URL)
        items = self._parse_cart_items(body)
        summary = self._parse_cart_summary(body)
        return {
            "items": items,
            "item_count": summary.get("item_count", len(items)),
            "total_czk": summary.get("total_czk"),
            "url": CART_URL,
        }

    async def clear_cart(self) -> dict[str, Any]:
        """Delete every line item by POSTing ``Upravit`` with ``kusu=0`` to
        each item's cart form (the same form the "Zruš položku" button
        renders, the only delete trigger cernyrytir exposes)."""
        await self._ensure_auth()
        cart = await self.view_cart()
        client = await get_client()
        removed = 0
        for item in cart.get("items", []):
            code = item.get("carovy_kod")
            if not code:
                continue
            async with host_slot("cernyrytir.cz"):
                resp = await client.post(
                    CART_URL,
                    data={
                        "databaze": _DATABASE,
                        "carovy_kod": code,
                        "nakupzbozi": _NAKUP_EDIT,
                        "kusu": "0",
                    },
                    headers={"Referer": CART_URL},
                    follow_redirects=False,
                )
            if resp.status_code in (200, 302, 303):
                removed += 1
        return {"removed_items": removed}

    @staticmethod
    def _parse_cart_summary(html: str) -> dict[str, Any]:
        """Extract cart totals from whichever cernyrytir page rendered them.

        Two shapes:

        * Search-result and product pages render a sidebar
          ``<div class="lista-kosik-polozka">V košíku máte N položky za X Kč</div>``.
          Singular-item carts collapse to
          ``V košíku máte 1 x <name> za X Kč`` (one item, fully spelled out).
        * The cart page itself drops the sidebar and renders a totals table:
          ``Cena zboží | X Kč`` / ``Cena celkem | Y Kč``. The grand total is
          what we surface; the cart page has no native item count, so callers
          should fall back to ``len(items)``.
        """
        tree = HTMLParser(html)
        # 1) sidebar
        div = tree.css_first(".lista-kosik-polozka")
        if div is not None:
            text = " ".join(unescape(div.text(separator=" ", strip=True)).split())
            # Pull the leading count and the final "za <N> Kč" total.
            count_m = re.search(r"V košíku máte\s+(\d+)", text)
            total_m = re.search(r"za\s+([\d ]+)\s*Kč", text)
            if count_m and total_m:
                return {
                    "item_count": int(count_m.group(1)),
                    "total_czk": int(re.sub(r"\D", "", total_m.group(1)) or 0),
                }
        # 2) cart-page totals table. The outer DOM nests the totals table
        # inside another table, so a plain "find a <tr> whose td contains
        # 'Cena celkem'" recurses into all descendant text. Match against
        # the raw HTML so we only ever read the leaf <td> pair.
        m = re.search(
            r"<td[^>]*>\s*Cena celkem\s*</td>\s*"
            r"<td[^>]*>\s*([\d ]+)\s*Kč\s*</td>",
            html,
        )
        if m:
            return {"total_czk": int(re.sub(r"\D", "", m.group(1)) or 0)}
        return {}

    @staticmethod
    def _parse_cart_items(html: str) -> list[dict[str, Any]]:
        """Parse one entry per line item from the cart table.

        Each item is rendered as a ``<tr>`` followed by two ``<form>``s with
        ``action=index.php3?akce=3&kosicek=1`` — one to update the quantity
        and one (with hidden ``kusu=0``) to delete. We pair the visible name
        cell with the first form's ``carovy_kod`` + visible ``kusu`` input +
        line-total cell."""
        items: list[dict[str, Any]] = []
        # Each item block contains: row preamble (name + per-unit) → first
        # form (kusu visible input + line total + Upravit submit) → second
        # form (Upravit kusu=0 = delete). Match the first form of each item.
        block_re = re.compile(
            r"<tr[^>]*>\s*<td[^>]*>([^<]+)</td>\s*"        # name
            r"<td[^>]*>\s*(\d+)\s*</td>\s*"                # per-unit price (no Kč)
            r"<form[^>]*action='[^']*kosicek=1'[^>]*>"     # update form opens
            r"\s*<td[^>]*>\s*<input[^>]*name='kusu'[^>]*value='(\d+)'[^>]*>\s*</td>"
            r"\s*<td[^>]*>\s*([\d ]+)\s*Kč\s*</td>"        # line total
            r".*?carovy_kod'\s*value='(\d+)'"              # id (in same form)
            r".*?nakupzbozi'\s*value='Upravit'",
            re.DOTALL,
        )
        for m in block_re.finditer(html):
            name = " ".join(unescape(m.group(1)).split())
            try:
                price_czk = int(m.group(2))
            except ValueError:
                price_czk = None
            qty = int(m.group(3))
            line_total_czk = int(re.sub(r"\D", "", m.group(4)) or 0)
            carovy_kod = m.group(5)
            items.append(
                {
                    "carovy_kod": carovy_kod,
                    "name": name or None,
                    "qty": qty,
                    "price_czk": price_czk,
                    "line_total_czk": line_total_czk,
                }
            )
        return items

    async def _login_locked(self) -> None:
        creds = credentials_for("cernyrytir")
        if creds is None:
            raise CredentialError(
                "cernyrytir credentials not configured "
                "(set CZ_MTG_CERNYRYTIR_USER and CZ_MTG_CERNYRYTIR_PASS)"
            )
        client = await get_client()
        # The login form is the small POST on the top-banner — fields
        # ``uzivjmeno`` + ``uzivheslo`` + hidden ``login=LOG IN``. The server
        # responds with the same page (200) and a PHPSESSID cookie that's
        # bound to the user account on success, or to an anonymous browser
        # session on failure. We follow that up with a request that should
        # only succeed when logged in (a "logout" link is rendered on the
        # account page) to confirm.
        async with host_slot("cernyrytir.cz"):
            resp = await client.post(
                LOGIN_URL,
                data={
                    "uzivjmeno": creds.username,
                    "uzivheslo": creds.password,
                    "login": "LOG IN",
                },
                headers={"Referer": f"{BASE}/"},
                follow_redirects=False,
            )
        if resp.status_code not in (200, 302, 303):
            raise CredentialError(
                f"cernyrytir login failed (status {resp.status_code})"
            )
        # Probe the account page; a logged-in session renders an "Odhlásit"
        # (log out) link, anonymous renders the login form again.
        async with host_slot("cernyrytir.cz"):
            check = await client.get(LOGIN_URL)
        try:
            check.encoding = "windows-1250"
            body = check.text
        except Exception:  # noqa: BLE001
            body = check.content.decode("windows-1250", errors="replace")
        if "Odhlášení" in body or "odhlas" in body.lower() or "odhlásit" in body.lower():
            self._authenticated = True
            return
        self._authenticated = False
        raise CredentialError(
            "cernyrytir login: server didn't return a logged-in account page; "
            "check CZ_MTG_CERNYRYTIR_* credentials"
        )

    @staticmethod
    def _split_name_suffix(raw: str) -> tuple[str, bool, Condition]:
        # Suffixes are joined by " - ": "Name", "Name - foil", "Name - foil - lightly played"
        parts = [p.strip() for p in _SUFFIX_SPLIT_RE.split(raw) if p.strip()]
        if not parts:
            return raw, False, Condition.UNKNOWN
        name = parts[0]
        foil = False
        condition = Condition.UNKNOWN
        for token in parts[1:]:
            low = token.lower()
            if low == "foil":
                foil = True
                continue
            cond = normalize_condition(token)
            if cond is not Condition.UNKNOWN:
                condition = cond
            else:
                # Unrecognized suffix — fold it back into the name.
                name = f"{name} - {token}"
        return name, foil, condition
