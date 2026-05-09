from cz_mtg_compare.models import Condition
from cz_mtg_compare.normalize import (
    parse_price_czk,
    parse_stock_qty,
    strip_card_suffixes,
)


def test_parse_price_handles_thinspace_and_currency():
    assert parse_price_czk("1 449 Kč") == 1449
    assert parse_price_czk("1 449 Kč") == 1449
    assert parse_price_czk("1 449 Kč") == 1449
    assert parse_price_czk("270 Kč") == 270
    assert parse_price_czk("CZK 35") == 35


def test_parse_price_returns_none_for_garbage():
    assert parse_price_czk("") is None
    assert parse_price_czk("no price here") is None


def test_parse_stock_qty():
    assert parse_stock_qty("skladem 2 ks") == 2
    assert parse_stock_qty("Skladem (>4 ks)") == 4
    assert parse_stock_qty("Skladem 0 ks") == 0
    assert parse_stock_qty("Předobjednávka") == 0
    assert parse_stock_qty("") == 0


def test_strip_card_suffixes_foil():
    name, foil, cond = strip_card_suffixes("Lightning Bolt (foil)")
    assert name == "Lightning Bolt"
    assert foil is True
    assert cond == Condition.UNKNOWN


def test_strip_card_suffixes_condition():
    name, foil, cond = strip_card_suffixes("Lightning Bolt (LP)")
    assert name == "Lightning Bolt"
    assert foil is False
    assert cond == Condition.LP


def test_strip_card_suffixes_stacked():
    name, foil, cond = strip_card_suffixes("Lightning Bolt (foil) (PL)")
    assert name == "Lightning Bolt"
    assert foil is True
    assert cond == Condition.PL


def test_strip_card_suffixes_unknown_token_preserved():
    # Unknown suffix should not be stripped.
    name, foil, cond = strip_card_suffixes("Card Name (Promo)")
    assert name == "Card Name (Promo)"
    assert foil is False
    assert cond == Condition.UNKNOWN
