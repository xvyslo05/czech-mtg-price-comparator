from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel

MAX_TOTAL_CARDS = 100  # Commander format limit; covers most casual decks.

# Each line is `<qty> <name>` or `<qty>x <name>`. Set/collector hints in parens
# are stripped because they vary by source — we only canonicalize on name.
#
#   "4 Lightning Bolt"
#   "4x Lightning Bolt"
#   "1 Sol Ring (CMR) 263"
#   "1 Lightning Bolt (M11) 149 *F*"
_LINE_RE = re.compile(
    r"""
    ^\s*
    (?P<qty>\d+)\s*x?           # quantity, optional 'x'
    \s+
    (?P<name>[^(*\n]+?)         # name, stops at '(' or '*' or end of line
    \s*
    (?:\([^)]*\)[^*]*)?         # optional set/collector chunk
    (?:\*[^*]*\*)?              # optional *F* foil marker (Arena export)
    \s*$
    """,
    re.VERBOSE,
)


class DeckSection(str, Enum):
    MAIN = "main"
    SIDEBOARD = "sideboard"
    MAYBEBOARD = "maybeboard"
    COMMANDER = "commander"


_SECTION_KEYWORDS: dict[str, DeckSection] = {
    "deck": DeckSection.MAIN,
    "main": DeckSection.MAIN,
    "mainboard": DeckSection.MAIN,
    "main deck": DeckSection.MAIN,
    "sideboard": DeckSection.SIDEBOARD,
    "side": DeckSection.SIDEBOARD,
    "sb": DeckSection.SIDEBOARD,
    "maybeboard": DeckSection.MAYBEBOARD,
    "maybe": DeckSection.MAYBEBOARD,
    "commander": DeckSection.COMMANDER,
    "commanders": DeckSection.COMMANDER,
}


class DecklistEntry(BaseModel):
    quantity: int
    name: str
    section: DeckSection = DeckSection.MAIN
    raw_line: str


@dataclass
class ParseError:
    line_no: int
    line: str
    reason: str


class DecklistParseResult(BaseModel):
    entries: list[DecklistEntry]
    errors: list[str] = []
    total_cards: int

    @property
    def unique_names(self) -> list[str]:
        seen: dict[str, None] = {}
        for e in self.entries:
            seen.setdefault(e.name.lower(), None)
        return list(seen.keys())


def parse_decklist(text: str) -> DecklistParseResult:
    """Parse Arena/MTGO-style decklist text. Tolerant of comments, blank lines,
    section headers (Deck / Sideboard / Maybeboard / Commander), and Arena-style
    set + collector + foil annotations.

    Raises ValueError if the parsed total exceeds MAX_TOTAL_CARDS.
    """
    entries: list[DecklistEntry] = []
    errors: list[str] = []
    section = DeckSection.MAIN
    total = 0

    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("//") or line.startswith("#"):
            continue

        # Section header? Match if the whole line (sans trailing colon) is a known keyword.
        header = line.rstrip(":").strip().lower()
        if header in _SECTION_KEYWORDS:
            section = _SECTION_KEYWORDS[header]
            continue

        m = _LINE_RE.match(line)
        if not m:
            errors.append(f"line {i}: could not parse {raw!r}")
            continue

        qty = int(m.group("qty"))
        name = " ".join(m.group("name").split())  # collapse whitespace
        if qty <= 0 or not name:
            errors.append(f"line {i}: invalid entry {raw!r}")
            continue

        total += qty
        entries.append(DecklistEntry(quantity=qty, name=name, section=section, raw_line=raw))

    if total > MAX_TOTAL_CARDS:
        raise ValueError(
            f"decklist has {total} cards, exceeds the {MAX_TOTAL_CARDS}-card limit"
        )

    return DecklistParseResult(entries=entries, errors=errors, total_cards=total)
