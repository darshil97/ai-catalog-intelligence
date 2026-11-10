"""Cleans product identifiers so the same product gets the same key however it was typed.

The supplier's own text is never changed: this produces a key beside it. Damage that can't be
undone, such as digits a spreadsheet dropped, is flagged and never guessed at.
"""

import re
import unicodedata
from dataclasses import dataclass

# Flags. Stored on the product and shown to the user.
SCIENTIFIC_NOTATION = "scientific_notation"
PRECISION_LOST = "precision_lost"
INVALID_GTIN = "invalid_gtin"

_WHITESPACE = re.compile(r"\s+")
_WHOLE_NUMBER_AS_DECIMAL = re.compile(r"^(\d+)\.0+$")
_SCIENTIFIC = re.compile(r"^\d(?:[.,]\d+)?E\+?\d+$", re.IGNORECASE)
_NOT_ALPHANUMERIC = re.compile(r"[\W_]+")
_GTIN_SEPARATORS = re.compile(r"[\s\-]")
_GTIN_LENGTHS = frozenset({8, 12, 13, 14})


@dataclass(frozen=True)
class Identifier:
    """A cleaned identifier.

    Attributes:
        key: What makes two rows the same product. Never empty.
        match_key: The key without punctuation, for looser matching such as file names.
        flags: Problems found, from the constants in this module.
    """

    key: str
    match_key: str
    flags: tuple[str, ...] = ()


def _base(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw)
    text = _WHITESPACE.sub(" ", text).strip()
    # Excel shows a leading apostrophe on text it was told not to treat as a number.
    if text.startswith("'"):
        text = text[1:].lstrip()
    return text


def canonical_code(raw: str, cell_flags: tuple[str, ...] = ()) -> Identifier | None:
    """Clean a SKU or model number.

    Case and surrounding spaces are ignored. Dashes, slashes and dots are kept, because
    "AB-12" and "AB12" can be different products. A whole number that picked up ".0" on its way
    through a spreadsheet loses it.

    Returns:
        The identifier, or None if the text is empty.
    """
    text = _base(raw)
    if not text:
        return None
    flags = []
    if _SCIENTIFIC.match(text):
        # 8.90123E+12 was a longer number once. The missing digits can't be recovered.
        flags.append(SCIENTIFIC_NOTATION)
    elif match := _WHOLE_NUMBER_AS_DECIMAL.match(text):
        text = match.group(1)
    if PRECISION_LOST in cell_flags:
        flags.append(PRECISION_LOST)
    key = text.upper()
    return Identifier(key=key, match_key=_NOT_ALPHANUMERIC.sub("", key) or key, flags=tuple(flags))


def gtin_check_digit_valid(digits: str) -> bool:
    """Return True if the last digit of a GTIN-8, 12, 13 or 14 matches the rest."""
    if len(digits) not in _GTIN_LENGTHS or not digits.isascii() or not digits.isdigit():
        return False
    body = digits[:-1]
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(body[::-1]))
    return (10 - total % 10) % 10 == int(digits[-1])


def canonical_gtin(raw: str, cell_flags: tuple[str, ...] = ()) -> Identifier | None:
    """Clean a barcode number (GTIN, EAN or UPC).

    Spaces and dashes are removed. Anything that isn't a valid barcode number is kept as typed
    and flagged, so the product still has a key and the problem is visible.

    Returns:
        The identifier, or None if the text is empty.
    """
    text = _base(raw)
    if not text:
        return None
    if _SCIENTIFIC.match(text):
        key = text.upper()
        return Identifier(key=key, match_key=key, flags=(SCIENTIFIC_NOTATION, INVALID_GTIN))
    if match := _WHOLE_NUMBER_AS_DECIMAL.match(text):
        text = match.group(1)
    digits = _GTIN_SEPARATORS.sub("", text)
    flags = [PRECISION_LOST] if PRECISION_LOST in cell_flags else []
    if not gtin_check_digit_valid(digits):
        flags.append(INVALID_GTIN)
        key = (digits if digits.isdigit() else text).upper()
        return Identifier(
            key=key, match_key=_NOT_ALPHANUMERIC.sub("", key) or key, flags=tuple(flags)
        )
    return Identifier(key=digits, match_key=digits, flags=tuple(flags))


def canonicalize(attribute: str, raw: str, cell_flags: tuple[str, ...] = ()) -> Identifier | None:
    """Clean an identifier of the given attribute: barcode rules for gtin, code rules otherwise."""
    if attribute == "gtin":
        return canonical_gtin(raw, cell_flags)
    return canonical_code(raw, cell_flags)
