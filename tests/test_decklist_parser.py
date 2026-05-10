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


def test_handles_crlf_line_endings():
    result = parse_decklist("4 Lightning Bolt\r\n2 Sol Ring\r\n")
    assert result.total_cards == 6
    assert {e.name for e in result.entries} == {"Lightning Bolt", "Sol Ring"}


def test_empty_input_yields_empty_result():
    result = parse_decklist("")
    assert result.total_cards == 0
    assert result.entries == []
    assert result.errors == []


def test_only_blank_and_comment_lines_yields_empty_result():
    result = parse_decklist("\n\n// header\n# also a comment\n   \n")
    assert result.total_cards == 0
    assert result.entries == []


def test_quantity_zero_is_recorded_as_error_not_silently_dropped():
    result = parse_decklist("0 Lightning Bolt\n4 Counterspell\n")
    assert result.total_cards == 4  # only Counterspell counts
    assert result.errors  # qty=0 should be flagged
    assert any("Lightning Bolt" in e for e in result.errors)


def test_handles_real_commander_deck_fixture(fixtures_dir):
    text = (fixtures_dir / "krenko_commander_100.txt").read_text()
    result = parse_decklist(text)
    assert result.total_cards == 100
    assert result.errors == []
    # Names with apostrophes, commas, hyphens, '//' must all parse cleanly.
    names = {e.name for e in result.entries}
    for tricky in [
        "Krenko, Mob Boss",
        "Inventors' Fair",
        "Siege-Gang Commander",
        "Sting, the Glinting Dagger",
        "Muxus, Goblin Grandee",
    ]:
        assert tricky in names, f"failed to parse {tricky!r}"


def test_section_headers_with_and_without_colons():
    result = parse_decklist(
        "Deck\n4 Lightning Bolt\n"
        "Sideboard:\n1 Negate\n"
    )
    by_name = {e.name: e for e in result.entries}
    from cz_mtg_compare.decklist import DeckSection
    assert by_name["Lightning Bolt"].section == DeckSection.MAIN
    assert by_name["Negate"].section == DeckSection.SIDEBOARD
