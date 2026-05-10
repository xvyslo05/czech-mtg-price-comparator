from __future__ import annotations

import pytest

from cz_mtg_compare.filters import filter_playable, is_non_playable
from cz_mtg_compare.models import Condition, Offer


def _o(name: str, edition: str | None = None) -> Offer:
    return Offer(
        shop="najada",
        card_name=name,
        edition=edition,
        condition=Condition.NM,
        foil=False,
        price_czk=10,
        stock_qty=1,
        url="https://example.com/x",
    )


@pytest.mark.parametrize("name", [
    "Art Series: Lightning Bolt",
    "Art Series: Lightning Bolt (V.2 - signed)",
    "Lightning Bolt (Art Card)",
    "Lightning Bolt Helper Card",
    "Lightning Bolt - Tip Card",
    "Lightning Bolt Checklist Card",
    "Spindown - Foundations",
    "Lightning Bolt (Oversized)",
])
def test_known_non_playable_names(name):
    assert is_non_playable(_o(name)) is True


@pytest.mark.parametrize("name,edition", [
    ("Lightning Bolt", "Magic 2010"),
    ("Lightning Bolt", "Battle for Baldur's Gate Extras"),
    ("Lightning Bolt", "Secret Lair Drop Series: Extra Life"),
    ("Lightning Bolt (Showcase)", "Commander Legends"),
    ("Emeritus of Conflict // Lightning Bolt", "Secrets of Strixhaven"),
])
def test_normal_cards_are_playable(name, edition):
    assert is_non_playable(_o(name, edition)) is False


def test_edition_match_also_filters():
    assert is_non_playable(_o("Lightning Bolt", edition="MTG Art Series 2024")) is True


def test_filter_playable_drops_non_playable():
    offers = [
        _o("Lightning Bolt", "M10"),
        _o("Art Series: Lightning Bolt"),
        _o("Lightning Bolt (Borderless)"),
        _o("Lightning Bolt Helper Card"),
    ]
    result = filter_playable(offers)
    names = {o.card_name for o in result}
    assert names == {"Lightning Bolt", "Lightning Bolt (Borderless)"}
