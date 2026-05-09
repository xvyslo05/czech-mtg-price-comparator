from __future__ import annotations

import re

from .models import Condition

_CONDITION_MAP: dict[str, Condition] = {
    "near mint": Condition.NM,
    "nm": Condition.NM,
    "mint": Condition.NM,
    "m": Condition.NM,
    "excellent": Condition.EX,
    "ex": Condition.EX,
    "ex+": Condition.EX,
    "good": Condition.GD,
    "gd": Condition.GD,
    "lightly played": Condition.LP,
    "light played": Condition.LP,
    "lp": Condition.LP,
    "slightly played": Condition.LP,
    "sp": Condition.LP,
    "played": Condition.PL,
    "pl": Condition.PL,
    "moderately played": Condition.PL,
    "mp": Condition.PL,
    "heavily played": Condition.HP,
    "hp": Condition.HP,
    "poor": Condition.HP,
}

_PRICE_RE = re.compile(r"(\d[\d\s   .,]*)\s*(?:Kč|CZK)", re.IGNORECASE)
_STOCK_RE = re.compile(r"(\d+)\s*ks", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"\s*\(([^)]+)\)\s*$")


def parse_price_czk(text: str) -> int | None:
    """Extract integer CZK from strings like '1 449 Kč', '270 Kč', '1 449 Kč'."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        # Allow plain digits if no currency suffix (some shops)
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else None
    digits = re.sub(r"[^\d]", "", m.group(1))
    return int(digits) if digits else None


def parse_stock_qty(text: str) -> int:
    """Extract numeric stock count from strings like 'skladem 2 ks', 'Skladem (>4 ks)'."""
    if not text:
        return 0
    m = _STOCK_RE.search(text)
    return int(m.group(1)) if m else 0


def normalize_condition(text: str | None) -> Condition:
    if not text:
        return Condition.UNKNOWN
    key = text.strip().lower()
    return _CONDITION_MAP.get(key, Condition.UNKNOWN)


def strip_card_suffixes(name: str) -> tuple[str, bool, Condition]:
    """Pull '(foil)' / '(EX+)' style suffixes off a card name.

    Returns (clean_name, foil, condition). Suffixes may stack, e.g. 'Card (foil) (LP)'.
    """
    foil = False
    condition = Condition.UNKNOWN
    cleaned = name.strip()
    while True:
        m = _SUFFIX_RE.search(cleaned)
        if not m:
            break
        token = m.group(1).strip().lower()
        if token == "foil":
            foil = True
        else:
            cond = _CONDITION_MAP.get(token)
            if cond is not None:
                condition = cond
            else:
                # Unrecognized suffix — keep it on the name to preserve information.
                break
        cleaned = cleaned[: m.start()].rstrip()
    return cleaned, foil, condition
