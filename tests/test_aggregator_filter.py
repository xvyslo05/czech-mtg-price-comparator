from __future__ import annotations

import pytest

from cz_mtg_compare.aggregator import Aggregator
from cz_mtg_compare.models import SearchQuery

from ._factories import StubAdapter, make_offer


@pytest.mark.asyncio
async def test_aggregator_excludes_non_playable_by_default():
    agg = Aggregator(
        [
            StubAdapter(
                "najada",
                [
                    make_offer("najada", "Lightning Bolt", 50, edition="M10"),
                    make_offer("najada", "Art Series: Lightning Bolt (V.2 - signed)", 200, edition=None),
                    make_offer("najada", "Lightning Bolt (Borderless)", 70, edition="2X2"),
                ],
            ),
        ]
    )
    offers = await agg.search(SearchQuery(name="Lightning Bolt"))
    names = {o.card_name for o in offers}
    assert "Art Series: Lightning Bolt (V.2 - signed)" not in names
    assert names == {"Lightning Bolt", "Lightning Bolt (Borderless)"}


@pytest.mark.asyncio
async def test_aggregator_keeps_non_playable_when_opted_in():
    agg = Aggregator(
        [
            StubAdapter(
                "najada",
                [
                    make_offer("najada", "Lightning Bolt", 50),
                    make_offer("najada", "Art Series: Lightning Bolt", 200),
                ],
            ),
        ]
    )
    offers = await agg.search(SearchQuery(name="Lightning Bolt", include_non_playable=True))
    names = {o.card_name for o in offers}
    assert "Art Series: Lightning Bolt" in names
