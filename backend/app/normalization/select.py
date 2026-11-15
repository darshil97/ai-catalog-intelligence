"""Chooses the value a product shows for an attribute, from its standard values.

verified: every value that can be relied on agrees, so the best source's is shown.
conflict: they disagree. A person decides; nothing is resolved here.
unclear:  values exist, but none can be relied on (no unit, a range, ...).
missing:  no source gave the attribute.

Numbers agree within a tolerance in the same unit, or currency for prices. Text and codes agree
when their comparable forms match.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.normalization.values import BLOCKING

VERIFIED = "verified"
CONFLICT = "conflict"
UNCLEAR = "unclear"
MISSING = "missing"
REVIEWER_SET = "reviewer_set"


@dataclass(frozen=True)
class Choice:
    """The outcome for one product attribute."""

    state: str
    selected_id: Any | None


def _usable(value: dict[str, Any]) -> bool:
    return not BLOCKING.intersection(value["flags"])


def agree(values: Sequence[dict[str, Any]], tolerance: float) -> bool:
    """True if every value says the same thing."""
    if len({value["unit"] for value in values}) > 1:
        return False
    numbers = [value["value_number"] for value in values]
    if all(number is not None for number in numbers):
        low, high = min(numbers), max(numbers)
        largest = max(abs(low), abs(high))
        return bool(high - low <= Decimal(str(tolerance)) * largest)
    return len({value["comparable"] for value in values}) == 1


def choose(values: Sequence[dict[str, Any]], tolerance: float) -> Choice:
    """Choose for one product attribute.

    Args:
        values: Its standard values, each with id, value_number, unit, comparable, flags,
            source_rank and order (file order, for ties).
    """
    if not values:
        return Choice(MISSING, None)
    usable = [value for value in values if _usable(value)]
    if not usable:
        return Choice(UNCLEAR, None)
    if not agree(usable, tolerance):
        return Choice(CONFLICT, None)
    best = min(usable, key=lambda value: (value["source_rank"], value["order"]))
    return Choice(VERIFIED, best["id"])
