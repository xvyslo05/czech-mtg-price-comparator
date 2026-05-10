from __future__ import annotations

import re

from .models import Offer

# Patterns that mark an offer as a *display-only* product — Art Series cards,
# oversized cards, helper/tip/checklist cards. None of these are legal in
# constructed Magic formats, so by default we exclude them from search results
# and the decklist optimizer. Pass `include_non_playable=True` on SearchQuery
# to keep them.
_NON_PLAYABLE_PATTERNS = [
    re.compile(r"\bart\s+series\b", re.IGNORECASE),
    re.compile(r"\bart\s+card\b", re.IGNORECASE),
    re.compile(r"\boversized\b", re.IGNORECASE),
    re.compile(r"\bhelper\s+card\b", re.IGNORECASE),
    re.compile(r"\btip\s+card\b", re.IGNORECASE),
    re.compile(r"\bchecklist\s+card\b", re.IGNORECASE),
    re.compile(r"\bspindown\b", re.IGNORECASE),
]


def is_non_playable(offer: Offer) -> bool:
    """True if the offer is a display-only product (Art Series, oversized, ...)."""
    haystacks = [offer.card_name or "", offer.edition or ""]
    return any(p.search(h) for h in haystacks for p in _NON_PLAYABLE_PATTERNS)


def filter_playable(offers: list[Offer]) -> list[Offer]:
    return [o for o in offers if not is_non_playable(o)]
