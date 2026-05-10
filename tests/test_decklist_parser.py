from __future__ import annotations

import pytest

from cz_mtg_compare.decklist import (
    MAX_TOTAL_CARDS,
    DeckSection,
    parse_decklist,
)


def test_parses_basic_arena_format():
    result = parse_decklist(
        """
        4 Lightning Bolt
        4 Counterspell
        2 Sol Ring
        """
    )
    assert result.total_cards == 10
    assert {e.name for e in result.entries} == {"Lightning Bolt", "Counterspell", "Sol Ring"}
    assert all(e.section == DeckSection.MAIN for e in result.entries)


def test_strips_arena_set_collector_and_foil_annotations():
    result = parse_decklist(
        """
        1 Sol Ring (CMR) 263
        1 Lightning Bolt (M11) 149 *F*
        """
    )
    names = [e.name for e in result.entries]
    assert names == ["Sol Ring", "Lightning Bolt"]


def test_handles_x_suffix():
    result = parse_decklist("4x Lightning Bolt\n2x Sol Ring\n")
    assert result.total_cards == 6
    assert [e.quantity for e in result.entries] == [4, 2]


def test_section_headers_change_section():
    result = parse_decklist(
        """
        Deck
        4 Lightning Bolt

        Sideboard:
        1 Negate

        Commander
        1 Atraxa
        """
    )
    by_section = {(e.name): e.section for e in result.entries}
    assert by_section["Lightning Bolt"] == DeckSection.MAIN
    assert by_section["Negate"] == DeckSection.SIDEBOARD
    assert by_section["Atraxa"] == DeckSection.COMMANDER


def test_skips_comments_and_blank_lines():
    result = parse_decklist(
        """
        // burn package
        4 Lightning Bolt

        # mana
        2 Mountain
        """
    )
    assert result.total_cards == 6


def test_collects_errors_for_garbage_lines():
    result = parse_decklist(
        """
        4 Lightning Bolt
        not a real entry line
        2 Counterspell
        """
    )
    assert result.total_cards == 6
    assert len(result.errors) == 1
    assert "not a real entry line" in result.errors[0]


def test_unique_names_dedupes_case_insensitive():
    result = parse_decklist("4 Lightning Bolt\n2 lightning bolt\n")
    assert result.unique_names == ["lightning bolt"]
    assert result.total_cards == 6


def test_rejects_oversized_decklist():
    big = "\n".join(f"1 Card{i}" for i in range(MAX_TOTAL_CARDS + 1))
    with pytest.raises(ValueError, match="exceeds"):
        parse_decklist(big)


def test_at_limit_is_allowed():
    body = "\n".join(f"1 Card{i}" for i in range(MAX_TOTAL_CARDS))
    result = parse_decklist(body)
    assert result.total_cards == MAX_TOTAL_CARDS
