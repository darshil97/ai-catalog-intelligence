"""Turns one observed value into standard values: numbers in standard units, cleaned text, codes.

Pure and deterministic: the same text, attribute and settings always give the same result,
which is stored with the rule version. Nothing is guessed. A value that can't be relied on
(no unit, a range, "approx.", two values in one, a unit of the wrong kind) is kept, flagged,
and left out of agreement.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.catalog.identifiers import canonicalize

# Flags that keep a value out of agreement: a person has to look.
UNIT_MISSING = "unit_missing"
RANGE = "range"
APPROXIMATE = "approximate"
TWO_VALUES = "two_values"
WRONG_UNIT = "wrong_unit"
NOT_A_NUMBER = "not_a_number"
CURRENCY_UNKNOWN = "currency_unknown"
NOT_A_WHOLE_NUMBER = "not_a_whole_number"
BLOCKING = frozenset(
    {
        UNIT_MISSING,
        RANGE,
        APPROXIMATE,
        TWO_VALUES,
        WRONG_UNIT,
        NOT_A_NUMBER,
        CURRENCY_UNKNOWN,
        NOT_A_WHOLE_NUMBER,
    }
)
# Flags that only say how a value was read.
CURRENCY_ASSUMED = "currency_assumed"
DECIMAL_COMMA = "decimal_comma"
SPLIT_FROM_SIZE = "split_from_size"
FLUID_OUNCE_ASSUMED = "fluid_ounce_assumed"
UNIT_FROM_HEADER = "unit_from_header"

IDENTIFIER_CODES = frozenset({"sku", "model_number", "gtin"})
SIZE_CODES = ("length", "width", "height")

_NUMBER = r"\d[\d.,]*\d|\d"
_SPACE = re.compile(r"\s+")
_INTERNATIONAL_GROUPS = re.compile(r"^\d{1,3}(,\d{3})+$")
_INDIAN_GROUPS = re.compile(r"^\d{1,2}(,\d{2})*,\d{3}$")
_DECIMAL_COMMA = re.compile(r"^\d+,\d{1,2}$")
_COUNT_WORDS = re.compile(
    r"\b(pcs?|pieces?|pack|packs|set|sets|units?|nos?\.?|count|qty)\b|pack of", re.IGNORECASE
)


@dataclass(frozen=True)
class Normal:
    """A standard value.

    Attributes:
        attribute: The attribute it's for. Usually the observation's; a combined size gives
            length, width and height.
        value_number: The number in the standard unit, for numeric attributes.
        value_text: The cleaned text, for text attributes.
        unit: The standard unit (g, ml, mm), a currency code for a price, or None.
        comparable: What agreement compares, for text and codes.
        display: What people see, such as "1.5 L".
        flags: How it was read, and why it can't be relied on, if so.
    """

    attribute: str
    value_number: Decimal | None
    value_text: str | None
    unit: str | None
    comparable: str
    display: str
    flags: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """True if the value can count toward agreement."""
        return not BLOCKING.intersection(self.flags)


def clean_text(text: str) -> str:
    """Normalize width, and collapse runs of spaces and line breaks into one space."""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def fold(text: str) -> str:
    """Text for comparison: cleaned and case-folded."""
    return clean_text(text).casefold()


def parse_number(text: str) -> tuple[Decimal | None, tuple[str, ...]]:
    """Read one number written the Indian way: commas group digits, a dot is the decimal point.

    "1,00,000" and "100,000" are 100000. "1,5" (a comma and one or two digits) is read as a
    decimal comma and flagged. "1.234,5" is read the European way. Anything else unclear is
    not a number.
    """
    flags: tuple[str, ...] = ()
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
            flags = (DECIMAL_COMMA,)
        else:
            text = text.replace(",", "")
    elif "," in text:
        if _INTERNATIONAL_GROUPS.match(text) or _INDIAN_GROUPS.match(text):
            text = text.replace(",", "")
        elif _DECIMAL_COMMA.match(text):
            text = text.replace(",", ".")
            flags = (DECIMAL_COMMA,)
        else:
            return None, ()
    elif text.count(".") > 1:
        # "1.234.567" can only be dots grouping digits.
        text = text.replace(".", "")
    try:
        return Decimal(text), flags
    except InvalidOperation:
        return None, ()


def format_number(value: Decimal) -> str:
    """Show a number without trailing zeros, and with at most three decimals."""
    rounded = value.quantize(Decimal("0.001")).normalize()
    text = f"{rounded:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def display_quantity(value: Decimal, family: str) -> str:
    """Show a standard quantity in the unit people expect: kg and L above 1000, cm for length."""
    if family == "mass":
        return f"{format_number(value / 1000)} kg" if value >= 1000 else f"{format_number(value)} g"
    if family == "volume":
        return f"{format_number(value / 1000)} L" if value >= 1000 else f"{format_number(value)} ml"
    if family == "length":
        return f"{format_number(value / 10)} cm"
    return format_number(value)


class Rules:
    """The unit, currency and wording rules, built once from settings."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.units: dict[str, dict[str, Decimal]] = {
            family: {alias: Decimal(str(factor)) for alias, factor in aliases.items()}
            for family, aliases in settings["units"].items()
        }
        aliases = sorted(
            {alias for family in self.units.values() for alias in family}, key=len, reverse=True
        )
        unit_words = "|".join(re.escape(alias) for alias in aliases)
        self.quantity = re.compile(rf"({_NUMBER})\s*({unit_words})?(?![a-z])", re.IGNORECASE)
        self.size = re.compile(
            rf"({_NUMBER})\s*({unit_words})?\s*[x×*]\s*({_NUMBER})\s*({unit_words})?"
            rf"(?:\s*[x×*]\s*({_NUMBER})\s*({unit_words})?)?",
            re.IGNORECASE,
        )
        self.range = re.compile(rf"({_NUMBER})\s*(?:-|–|—|to)\s*({_NUMBER})", re.IGNORECASE)
        words = sorted(settings["approximate_words"], key=len, reverse=True)
        self.approximate = re.compile(
            "|".join(
                rf"(?<![\w]){re.escape(word)}(?![\w])" if word[0].isalnum() else re.escape(word)
                for word in words
            ),
            re.IGNORECASE,
        )
        self.currencies: dict[str, str] = settings["currencies"]
        currency_words = sorted(self.currencies, key=len, reverse=True)
        self.currency = re.compile(
            "|".join(
                rf"(?<![\w]){re.escape(word)}(?![\w])" if word[0].isalnum() else re.escape(word)
                for word in currency_words
            ),
            re.IGNORECASE,
        )

    def family_of(self, unit: str) -> str | None:
        """Return which family a unit as written belongs to, or None."""
        unit = unit.lower()
        return next((family for family, aliases in self.units.items() if unit in aliases), None)

    def header_unit(self, header: str | None, family: str) -> str | None:
        """Return the unit a column heading gives, such as "g" in "Net Wt (g)"."""
        if not header:
            return None
        bracketed = re.findall(r"[(\[]\s*([^)\]]+?)\s*[)\]]", header)
        candidates = bracketed + re.findall(r"\bin\s+(\w+)", header, re.IGNORECASE)
        for candidate in candidates:
            unit = str(candidate).lower()
            if unit in self.units.get(family, {}):
                return unit
        return None

    def find_currency(self, text: str) -> tuple[str | None, tuple[str, ...]]:
        """Return the currency a text names, and whether it was assumed from a lone "$"."""
        match = self.currency.search(text)
        if match:
            return self.currencies[match.group(0).lower()], ()
        if "$" in text:
            return self.settings["dollar_currency"], (CURRENCY_ASSUMED,)
        return None, ()


def _text_value(code: str, raw: str) -> list[Normal]:
    cleaned = clean_text(raw)
    return [Normal(code, None, cleaned, None, cleaned.casefold(), cleaned)]


def _identifier_value(code: str, raw: str) -> list[Normal]:
    identifier = canonicalize(code, raw)
    if identifier is None:
        return [Normal(code, None, "", None, "", clean_text(raw), (NOT_A_NUMBER,))]
    return [
        Normal(code, None, identifier.key, None, identifier.key, identifier.key, identifier.flags)
    ]


def _unreadable(code: str, raw: str, flag: str) -> list[Normal]:
    cleaned = clean_text(raw)
    return [Normal(code, None, cleaned, None, cleaned.casefold(), cleaned, (flag,))]


def _quantity_value(
    rules: Rules, code: str, family: str, raw: str, header: str | None
) -> list[Normal]:
    text = clean_text(raw)
    standard = rules.settings["standard_units"][family]

    if family == "length":
        size = rules.size.search(text)
        if size:
            numbers = [size.group(i) for i in (1, 3, 5) if size.group(i)]
            units = [size.group(i) for i in (2, 4, 6) if size.group(i)]
            return _split_size(rules, text, numbers, units, header)

    if rules.range.search(text):
        return _unreadable(code, raw, RANGE)
    quantities = rules.quantity.findall(text)
    if not quantities:
        return _unreadable(code, raw, NOT_A_NUMBER)
    distinct = {(number, (unit or "").lower()) for number, unit in quantities}
    if len(distinct) > 1:
        return _unreadable(code, raw, TWO_VALUES)
    number_text, unit = quantities[0]
    number, flags = parse_number(number_text)
    if number is None:
        return _unreadable(code, raw, NOT_A_NUMBER)
    if rules.approximate.search(text):
        flags = (*flags, APPROXIMATE)
    unit = unit.lower()
    if unit == "oz" and family == "volume":
        # On a capacity, an ounce is a fluid ounce.
        unit = "fl oz"
        flags = (*flags, FLUID_OUNCE_ASSUMED)
    if not unit:
        unit = rules.header_unit(header, family) or ""
        if unit:
            flags = (*flags, UNIT_FROM_HEADER)
    if not unit:
        return [
            Normal(code, number, None, None, format_number(number), text, (*flags, UNIT_MISSING))
        ]
    if rules.family_of(unit) != family:
        return [Normal(code, number, None, None, format_number(number), text, (*flags, WRONG_UNIT))]
    value = number * rules.units[family][unit]
    return [
        Normal(
            code,
            value,
            None,
            standard,
            f"{format_number(value)} {standard}",
            display_quantity(value, family),
            flags,
        )
    ]


def _split_size(
    rules: Rules, text: str, numbers: list[str], units: list[str], header: str | None
) -> list[Normal]:
    """A combined size, such as "10cm x 6cm x 3cm": length, width and height, in that order."""
    distinct_units = {unit.lower() for unit in units}
    unit = units[-1].lower() if len(distinct_units) == 1 else None
    flags: tuple[str, ...] = (SPLIT_FROM_SIZE,)
    if not units:
        unit = rules.header_unit(header, "length")
        flags = (*flags, UNIT_FROM_HEADER) if unit else flags
    if unit is None or rules.family_of(unit) != "length":
        reason = UNIT_MISSING if not units else WRONG_UNIT if unit else TWO_VALUES
        cleaned = clean_text(text)
        return [
            Normal(code, None, cleaned, None, cleaned.casefold(), cleaned, (*flags, reason))
            for code in SIZE_CODES[: len(numbers)]
        ]
    found = []
    for code, number_text in zip(SIZE_CODES, numbers, strict=False):
        number, number_flags = parse_number(number_text)
        if number is None:
            return _unreadable(code, text, NOT_A_NUMBER)
        value = number * rules.units["length"][unit]
        found.append(
            Normal(
                code,
                value,
                None,
                "mm",
                f"{format_number(value)} mm",
                display_quantity(value, "length"),
                (*flags, *number_flags),
            )
        )
    return found


def _price_value(rules: Rules, raw: str, product_currency: str | None) -> list[Normal]:
    text = clean_text(raw)
    if rules.range.search(text):
        return _unreadable("price", raw, RANGE)
    numbers = re.findall(_NUMBER, text)
    if not numbers:
        return _unreadable("price", raw, NOT_A_NUMBER)
    if len(set(numbers)) > 1:
        return _unreadable("price", raw, TWO_VALUES)
    number, flags = parse_number(numbers[0])
    if number is None:
        return _unreadable("price", raw, NOT_A_NUMBER)
    currency, currency_flags = rules.find_currency(text)
    flags = (*flags, *currency_flags)
    if currency is None:
        currency = product_currency
    if currency is None:
        currency = rules.settings["default_currency"]
        flags = (*flags, CURRENCY_ASSUMED)
    if rules.approximate.search(text):
        flags = (*flags, APPROXIMATE)
    shown = f"{format_number(number)} {currency}"
    return [Normal("price", number, None, currency, shown, shown, flags)]


def _currency_value(rules: Rules, raw: str) -> list[Normal]:
    currency, flags = rules.find_currency(clean_text(raw))
    if currency is None:
        return _unreadable("currency", raw, CURRENCY_UNKNOWN)
    return [Normal("currency", None, currency, None, currency, currency, flags)]


def _count_value(code: str, raw: str) -> list[Normal]:
    text = _COUNT_WORDS.sub(" ", clean_text(raw))
    numbers = re.findall(_NUMBER, text)
    if len(set(numbers)) != 1:
        return _unreadable(code, raw, TWO_VALUES if numbers else NOT_A_NUMBER)
    number, flags = parse_number(numbers[0])
    if number is None:
        return _unreadable(code, raw, NOT_A_NUMBER)
    if number != number.to_integral_value():
        return [Normal(code, number, None, None, format_number(number), clean_text(raw),
                       (*flags, NOT_A_WHOLE_NUMBER))]  # fmt: skip
    shown = format_number(number)
    return [Normal(code, number, None, None, shown, shown, flags)]


def normalize(
    rules: Rules,
    attribute: dict[str, Any],
    raw: str,
    *,
    header: str | None = None,
    product_currency: str | None = None,
) -> list[Normal]:
    """Normalize one observed value.

    Args:
        attribute: Its definition: code, data_type and unit_family.
        header: The spreadsheet column heading it came from, for a unit like "(g)".
        product_currency: The product's currency when its sources agree on one, for a price
            written without one.

    Returns:
        One standard value, or three for a combined size given as a length.
    """
    code = attribute["code"]
    if code in IDENTIFIER_CODES:
        return _identifier_value(code, raw)
    if code == "price":
        return _price_value(rules, raw, product_currency)
    if code == "currency":
        return _currency_value(rules, raw)
    family = attribute.get("unit_family")
    if family:
        return _quantity_value(rules, code, family, raw, header)
    if attribute["data_type"] == "number":
        return _count_value(code, raw)
    return _text_value(code, raw)
