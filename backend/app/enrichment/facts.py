"""The facts a product's text may be written from, and what was left out and why (D37).

Pure and shared: the worker builds the prompt from these, and the API uses the same rules to
say which texts are out of date. Only settled attributes become facts: verified ones and ones a
person set. The category counts when a person chose it or the LLM was sure.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

CATEGORY = "category"

# Facts come first in this order, then the rest by name, so the same facts give the same prompt.
_FIRST = ("product_name", "brand", CATEGORY)

_REASONS = {
    "conflict": "Sources disagree; waiting for review.",
    "unclear": "Its value can't be relied on yet; waiting for review.",
    "missing": "No source gives it.",
}


@dataclass(frozen=True)
class Fact:
    """One thing the text may say.

    Attributes:
        id: Its short name in the prompt, such as "F3".
        code: The attribute's code, or "category".
        name: What people call it, such as "Capacity".
        display: The value as people see it, such as "1.5 L".
        attribute_id: The attribute, or None for the category.
        state: verified or reviewer_set, or the category's state (assigned, or person).
        selected_value_id: The standard value shown, if a source's.
        reviewer: True when a person typed the value.
        family: mass, volume or length, for a quantity.
        number: The value in standard units (g, ml, mm), or a count, when known.
        title_only: True when it may only appear in the title.
    """

    id: str
    code: str
    name: str
    display: str
    attribute_id: UUID | None
    state: str
    selected_value_id: UUID | None
    reviewer: bool
    family: str | None
    number: Decimal | None
    title_only: bool

    def citation(self) -> dict[str, Any]:
        """What a stored claim keeps about this fact, frozen as it is now."""
        return {
            "fact": self.id,
            "attribute_id": str(self.attribute_id) if self.attribute_id else None,
            "code": self.code,
            "name": self.name,
            "display": self.display,
            "state": self.state,
            "selected_value_id": str(self.selected_value_id) if self.selected_value_id else None,
            "reviewer": self.reviewer,
        }


@dataclass(frozen=True)
class LeftOut:
    """An attribute the text can't use, and why."""

    code: str
    name: str
    state: str
    reason: str


@dataclass(frozen=True)
class FactSheet:
    """A product's facts, what was left out, and the fingerprint of the facts."""

    facts: tuple[Fact, ...]
    left_out: tuple[LeftOut, ...]
    fingerprint: str
    enough: bool


def _shown(row: dict[str, Any], fact_max_chars: int) -> str | None:
    text = row.get("shown")
    if not text and row.get("reviewer_value"):
        text = (row["reviewer_value"] or {}).get("text")
    if not text:
        return None
    text = " ".join(str(text).split())
    return text[:fact_max_chars] if len(text) > fact_max_chars else text


def _number(row: dict[str, Any]) -> Decimal | None:
    value = row.get("value_number")
    if value is None or row.get("reviewer_value"):
        # A person's own value is read by the grounding rules from its text.
        return None
    return Decimal(str(value))


def build_facts(
    attributes: list[dict[str, Any]],
    category: dict[str, Any] | None,
    settings: dict[str, Any],
) -> FactSheet:
    """Choose a product's facts.

    Args:
        attributes: One row per attribute definition, with keys attribute_id, code, name,
            unit_family, state (None means missing), shown, value_number, reviewer_value and
            selected_value_id.
        category: The product's category, with keys path and source (llm or person) and state,
            or None when it has none.
        settings: The enrichment settings.

    Returns:
        The facts in a fixed order, what was left out, the fingerprint, and whether there's
        enough to write about: a name, or a brand and a category.
    """
    usable = set(settings["usable_states"])
    excluded = set(settings["excluded_attributes"])
    title_only = set(settings["title_only_attributes"])
    limit = settings["fact_max_chars"]

    found: list[dict[str, Any]] = []
    left_out: list[LeftOut] = []
    for row in attributes:
        code = row["code"]
        if code in excluded:
            continue
        state = row.get("state") or "missing"
        shown = _shown(row, limit) if state in usable else None
        if shown is None:
            if state in usable:
                state = "missing"
            left_out.append(
                LeftOut(code, row["name"], state, _REASONS.get(state, _REASONS["missing"]))
            )
            continue
        found.append(
            {
                "code": code,
                "name": row["name"],
                "display": shown,
                "attribute_id": row["attribute_id"],
                "state": state,
                "selected_value_id": row.get("selected_value_id"),
                "reviewer": bool(row.get("reviewer_value")),
                "family": row.get("unit_family"),
                "number": _number(row),
            }
        )
    if (
        category
        and category.get("path")
        and (category.get("source") == "person" or category.get("state") == "assigned")
    ):
        found.append(
            {
                "code": CATEGORY,
                "name": "Category",
                "display": category["path"],
                "attribute_id": None,
                "state": "person" if category.get("source") == "person" else "assigned",
                "selected_value_id": None,
                "reviewer": category.get("source") == "person",
                "family": None,
                "number": None,
            }
        )
    elif category is not None:
        left_out.append(
            LeftOut(CATEGORY, "Category", "needs_review", "No category is settled yet.")
        )

    found.sort(
        key=lambda f: (
            _FIRST.index(f["code"]) if f["code"] in _FIRST else len(_FIRST),
            f["name"].casefold(),
        )
    )
    facts = tuple(
        Fact(id=f"F{index}", title_only=fact["code"] in title_only, **fact)
        for index, fact in enumerate(found, start=1)
    )
    codes = {fact.code for fact in facts}
    enough = "product_name" in codes or {"brand", CATEGORY} <= codes
    return FactSheet(
        facts=facts,
        left_out=tuple(sorted(left_out, key=lambda item: item.name.casefold())),
        fingerprint=fingerprint(facts, settings["rules_version"]),
        enough=enough,
    )


def fingerprint(facts: tuple[Fact, ...], rules_version: str) -> str:
    """A hash of what the text was written from. It changes when any fact's value changes."""
    payload = [rules_version, [[fact.code, fact.display, fact.title_only] for fact in facts]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
