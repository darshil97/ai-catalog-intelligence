"""Turns a value stored in a workbook into the text a spreadsheet program shows for it.

Only what changes a value's meaning is reproduced: decimal places, leading zeros, percentages
and dates. Thousands separators, currency symbols and colours are left out, and the cell's
display format is kept alongside the text so nothing is lost.
"""

import datetime
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# Spreadsheets keep 15 significant digits. A whole number at or above this has lost some.
PRECISION_LIMIT = 10**15

_QUOTED = re.compile(r'"[^"]*"|\\.|_.|\*.|\[[^\]]*\]')
_ZEROS_ONLY = re.compile(r"^0+$")


def _clean_format(number_format: str | None) -> str:
    """Return the positive-number section of a format, without literals, colours or padding."""
    section = (number_format or "General").split(";")[0]
    return _QUOTED.sub("", section).strip()


def _decimals(fmt: str) -> tuple[int, int] | None:
    """Return the fewest and most decimal places a format shows, or None if it has no pattern."""
    if "." not in fmt:
        return None
    after = fmt.split(".", 1)[1]
    places = re.match(r"[0#?]*", after)
    shown = places.group(0) if places else ""
    return shown.count("0"), len(shown)


def _fixed(value: Decimal, least: int, most: int) -> str:
    """Round half up, as spreadsheets do, then drop optional trailing zeros."""
    text = f"{value.quantize(Decimal(1).scaleb(-most), rounding=ROUND_HALF_UP):f}"
    if most > least and "." in text:
        whole, fraction = text.split(".")
        fraction = fraction[:least] + fraction[least:].rstrip("0")
        text = f"{whole}.{fraction}" if fraction else whole
    return text


def render_number(value: int | float, number_format: str | None) -> str:
    """Return a stored number as text, following its display format where that matters."""
    fmt = _clean_format(number_format)
    general = fmt in ("", "General", "@") or "E+" in fmt.upper() or "E-" in fmt.upper()
    number = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)

    if not general and "%" in fmt:
        least, most = _decimals(fmt) or (0, 0)
        return _fixed(number * 100, least, most) + "%"
    decimals = None if general else _decimals(fmt)
    if decimals is not None:
        return _fixed(number, *decimals)
    if not general and _ZEROS_ONLY.match(fmt):
        # A format such as 00000 is how leading zeros survive in a number cell.
        whole = number.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return f"{'-' if whole < 0 else ''}{abs(int(whole)):0{len(fmt)}d}"
    if not general and re.search(r"[0#]", fmt):
        return f"{number.quantize(Decimal(1), rounding=ROUND_HALF_UP):f}"
    if isinstance(value, int) or (value.is_integer() and abs(value) < PRECISION_LIMIT):
        return str(int(value))
    return format(value, ".15g")


def render_value(value: Any, number_format: str | None) -> str:
    """Return any stored cell value as text. None becomes ""."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return render_number(value, number_format)
    if isinstance(value, datetime.datetime):
        if value.time() == datetime.time(0):
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, datetime.date | datetime.time):
        return value.isoformat()
    return str(value)
