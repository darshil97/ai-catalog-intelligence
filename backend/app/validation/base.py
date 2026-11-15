"""The rule contract and registry for catalog checks (D38).

A rule reads one batch (a BatchContext) and returns findings. It never changes anything. The
engine gives each finding its severity from config and the batch's marketplace profile.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

Row = dict[str, Any]

ERROR = "error"
WARNING = "warning"
INFO = "info"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}

SETTLED = ("verified", "reviewer_set")


@dataclass(frozen=True)
class Finding:
    """What a rule found about one product.

    Attributes:
        product_id: The product it's about.
        message: Plain words for people.
        subject: The attribute code, the other product's ID, or "" for the product itself.
        seen: What the rule looked at, so a set-aside warning returns if it changes.
        other_product_id: The other product of a duplicate pair.
        fix: Where to fix it: review, generate, photos, or None.
        attribute: The attribute a person can edit to fix it (M9), if any.
    """

    product_id: UUID
    message: str
    subject: str = ""
    seen: str = ""
    other_product_id: UUID | None = None
    fix: str | None = None
    attribute: str | None = None


@dataclass(frozen=True)
class Attribute:
    """One attribute of a product as the rules see it."""

    code: str
    name: str
    state: str
    family: str | None
    shown: str | None
    number: Decimal | None
    comparable: str | None
    flags: tuple[str, ...]

    @property
    def settled(self) -> bool:
        """Verified, or set by a person, with a value to show."""
        return self.state in SETTLED and bool(self.shown)


@dataclass
class ProductData:
    """One product and everything the rules read about it."""

    id: UUID
    key: str
    row: Row
    attributes: dict[str, Attribute]
    photos: list[Row] = field(default_factory=list)
    open_reviews: list[str] = field(default_factory=list)
    text_state: str = "none"
    # Its settled facts (M7), for checking copy a person wrote.
    facts: tuple[Any, ...] = ()

    @property
    def rejected(self) -> bool:
        """A person rejected it (M9)."""
        return self.row.get("status") == "rejected"

    def value(self, code: str) -> Attribute | None:
        """A settled attribute, or None."""
        attribute = self.attributes.get(code)
        return attribute if attribute and attribute.settled else None


@dataclass
class BatchContext:
    """A batch's products, and what relates them."""

    batch_id: UUID
    products: list[ProductData]
    # frozenset of two product IDs -> flagged, confirmed or dismissed.
    variant_pairs: dict[frozenset[UUID], str] = field(default_factory=dict)


class Rule(Protocol):
    """One check. Must be pure: the same batch always gives the same findings."""

    code: str
    version: int
    description: str

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        """Return findings for any products that break the rule."""
        ...


_REGISTRY: dict[str, Rule] = {}


def register_rule(rule: Rule) -> Rule:
    """Add a rule under its code. Codes must be unique."""
    if rule.code in _REGISTRY:
        raise ValueError(f"Duplicate rule code: {rule.code!r}")
    _REGISTRY[rule.code] = rule
    return rule


def registered_rules() -> dict[str, Rule]:
    """Every registered rule, by code."""
    return dict(_REGISTRY)


def fingerprint(rule: str, subject: str, seen: str) -> str:
    """A short hash of what a rule saw."""
    return hashlib.sha256(f"{rule}\x1f{subject}\x1f{seen}".encode()).hexdigest()[:32]
