"""Keeping exported cells inert (D41).

A cell starting with =, +, -, @, a tab or a carriage return can run as a formula when a
spreadsheet opens it. In CSV and Excel files such text gets a leading apostrophe, so the text
shows as typed. The Shopify file is imported rather than opened, and an apostrophe would end up
in the product, so there the characters are removed and the change is reported.
"""

from typing import Any

RISKY = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """A value for a CSV or Excel cell: risky text made inert, everything else unchanged."""
    if isinstance(value, str) and value.startswith(RISKY):
        return "'" + value
    return value


def shopify_text(value: str, where: str, notes: list[str]) -> str:
    """Text for the Shopify file: risky leading characters removed, and the change noted."""
    if not value.startswith(RISKY):
        return value
    cleaned = value.lstrip("".join(RISKY)).lstrip()
    notes.append(f"{where}: removed “{value[: len(value) - len(cleaned)].strip() or value[0]}” "
                 "from the start, so it can't run as a formula.")  # fmt: skip
    return cleaned
