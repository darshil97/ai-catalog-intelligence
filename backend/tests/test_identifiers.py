"""Covers identifier cleanup. Pure functions: no database, nothing stubbed."""

import pytest

from app.catalog.identifiers import (
    INVALID_GTIN,
    PRECISION_LOST,
    SCIENTIFIC_NOTATION,
    canonical_code,
    canonical_gtin,
    canonicalize,
    gtin_check_digit_valid,
)


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("ab-12", "AB-12"),
        ("  AB-12  ", "AB-12"),
        ("AB \t 12", "AB 12"),
        ("12345.0", "12345"),
        ("12345.00", "12345"),
        ("'00123", "00123"),  # Excel's text marker goes; the leading zeros it protected stay
        ("00123", "00123"),
        ("ＡＢ１２", "AB12"),  # full-width characters from an East Asian keyboard
        ("12.50", "12.50"),  # not a whole number, so it's a code, not a damaged integer
        ("AB/12.5", "AB/12.5"),
    ],
)
def test_codes_are_cleaned_without_losing_what_tells_products_apart(raw: str, key: str) -> None:
    identifier = canonical_code(raw)
    assert identifier is not None
    assert (identifier.key, identifier.flags) == (key, ())


def test_punctuation_separates_products_but_not_match_keys() -> None:
    dashed, plain = canonical_code("AB-12"), canonical_code("AB12")
    assert dashed is not None and plain is not None
    assert dashed.key != plain.key
    assert dashed.match_key == plain.match_key == "AB12"


@pytest.mark.parametrize("raw", ["", "   ", "'", "\t\n"])
def test_empty_text_is_no_identifier(raw: str) -> None:
    assert canonical_code(raw) is None
    assert canonical_gtin(raw) is None


@pytest.mark.parametrize("raw", ["8.90123E+12", "8,90123E+12", "1E+15", "8.9e12"])
def test_scientific_notation_is_flagged_never_expanded(raw: str) -> None:
    identifier = canonical_code(raw)
    assert identifier is not None
    assert identifier.flags == (SCIENTIFIC_NOTATION,)
    assert identifier.key == raw.upper()


def test_a_model_number_that_only_looks_scientific_is_left_alone() -> None:
    identifier = canonical_code("E12")
    assert identifier is not None and identifier.flags == ()


def test_precision_lost_in_the_file_is_carried_onto_the_identifier() -> None:
    identifier = canonical_code("12345678901234567000", (PRECISION_LOST,))
    assert identifier is not None and identifier.flags == (PRECISION_LOST,)


@pytest.mark.parametrize(
    "digits", ["4006381333931", "036000291452", "96385074", "10614141000415", "00012345678905"]
)
def test_valid_barcodes(digits: str) -> None:
    assert gtin_check_digit_valid(digits)
    identifier = canonical_gtin(digits)
    assert identifier is not None and (identifier.key, identifier.flags) == (digits, ())


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("4006381333932", "4006381333932"),  # wrong check digit
        ("400638133393", "400638133393"),  # 12 digits, but not a valid UPC
        ("12345", "12345"),  # wrong length
        ("ABC-123", "ABC-123"),  # not a number at all
    ],
)
def test_invalid_barcodes_are_kept_and_flagged(raw: str, key: str) -> None:
    identifier = canonical_gtin(raw)
    assert identifier is not None
    assert (identifier.key, identifier.flags) == (key, (INVALID_GTIN,))


def test_barcode_separators_and_spreadsheet_damage() -> None:
    spaced = canonical_gtin("4 006381 333931")
    dashed = canonical_gtin("4006381-333931")
    decimal = canonical_gtin("4006381333931.0")
    assert spaced is not None and dashed is not None and decimal is not None
    assert spaced.key == dashed.key == decimal.key == "4006381333931"
    damaged = canonical_gtin("4.00638E+12")
    assert damaged is not None
    assert set(damaged.flags) == {SCIENTIFIC_NOTATION, INVALID_GTIN}


def test_canonicalize_picks_the_rules_by_attribute() -> None:
    code = canonicalize("sku", "4006381-333931")
    barcode = canonicalize("gtin", "4006381-333931")
    assert code is not None and barcode is not None
    assert code.key == "4006381-333931"
    assert barcode.key == "4006381333931"
