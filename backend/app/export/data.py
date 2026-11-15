"""What an export reads: a batch's approved products and everything about them (D41). Shared.

Only products approved, and unchanged since approval (M9's rule), are included. Everything
else is listed with the reason it was left out.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from psycopg import Connection

from app.approval import rules
from app.approval.repository import load as load_approval
from app.config import get_normalization_config
from app.db import query
from app.enrichment.repository import category_paths
from app.normalization import Rules, normalize
from app.validation.base import ProductData

Row = dict[str, Any]

_LEFT_OUT = {
    rules.DRAFT: "Not approved yet.",
    rules.CHANGED: "Changed since it was approved; approve it again.",
    rules.REJECTED: "Rejected.",
}


@dataclass
class Item:
    """One exported product."""

    product: ProductData
    category_path: str | None
    photos: list[Row] = field(default_factory=list)
    sources: list[Row] = field(default_factory=list)

    @property
    def id(self) -> UUID:
        return self.product.id

    @property
    def key(self) -> str:
        return self.product.key

    def value(self, code: str) -> str | None:
        """A settled value as shown, or None."""
        attribute = self.product.value(code)
        return attribute.shown if attribute else None

    @property
    def title(self) -> str:
        return str(self.product.row.get("generated_title") or self.value("product_name") or "")

    @property
    def description(self) -> str:
        return str(self.product.row.get("generated_description") or "")

    @property
    def bullets(self) -> list[str]:
        return list(self.product.row.get("generated_bullets") or [])


@dataclass
class ExportData:
    """A batch ready to export."""

    batch: Row
    profile: str
    profile_name: str
    attributes: list[Row]
    items: list[Item]
    left_out: list[Row]
    # frozenset of two product IDs -> flagged, confirmed or dismissed.
    variant_pairs: dict[frozenset[UUID], str]


def load(conn: Connection[Any], owner: str, batch_id: UUID) -> ExportData | None:
    """Everything an export needs, or None if the owner has no such batch."""
    params = {"owner": owner, "batch_id": batch_id}
    batch = conn.execute(query("export", "batch"), params).fetchone()
    if batch is None:
        return None
    approval = load_approval(conn, owner, batch_id)
    paths = category_paths(conn)
    photos: dict[UUID, list[Row]] = {}
    for row in conn.execute(query("export", "photos"), params).fetchall():
        photos.setdefault(row["product_id"], []).append(row)
    sources: dict[UUID, list[Row]] = {}
    for row in conn.execute(query("export", "sources"), params).fetchall():
        sources.setdefault(row["product_id"], []).append(row)

    items, left_out = [], []
    for product in approval.ctx.products:
        state = rules.state(product, approval.profile)
        if state != rules.APPROVED:
            left_out.append({"product_key": product.key, "reason": _LEFT_OUT[state]})
            continue
        category = product.row.get("category_id")
        items.append(
            Item(
                product=product,
                category_path=paths.get(category) if category else None,
                photos=photos.get(product.id, []),
                sources=sources.get(product.id, []),
            )
        )
    return ExportData(
        batch=batch,
        profile=approval.profile,
        profile_name=approval.report.profile_name,
        attributes=conn.execute(query("export", "attributes")).fetchall(),
        items=items,
        left_out=left_out,
        variant_pairs=approval.ctx.variant_pairs,
    )


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def price_number(item: Item) -> tuple[Decimal | None, str | None]:
    """The price as a plain number, and its currency code."""
    attribute = item.product.value("price")
    if attribute is None or not attribute.shown:
        return None, None
    currency = attribute.shown.split()[-1] if " " in attribute.shown else None
    number = attribute.number
    if number is None:
        match = _NUMBER.search(attribute.shown)
        try:
            number = Decimal(match.group(0).replace(",", "")) if match else None
        except InvalidOperation:
            number = None
    return number, currency


def grams(item: Item) -> int | None:
    """The net weight in whole grams, as Shopify wants it."""
    attribute = item.product.value("net_weight")
    if attribute is None:
        return None
    number = attribute.number
    if number is None and attribute.shown:
        found = normalize(
            Rules(get_normalization_config()),
            {"code": "net_weight", "unit_family": "mass", "data_type": "number"},
            attribute.shown,
        )
        number = found[0].value_number if len(found) == 1 and found[0].usable else None
    return int(number.to_integral_value()) if number is not None else None


def where(source: Row) -> str:
    """Where a value was read, in words."""
    location = source.get("location") or {}
    if "row" in location:
        parts = []
        if location.get("sheet"):
            parts.append(f"sheet {location['sheet']}")
        parts.append(f"row {location['row']}")
        if location.get("column"):
            header = f" ({location['header']})" if location.get("header") else ""
            parts.append(f"column {location['column']}{header}")
        return ", ".join(parts)
    if location.get("quote"):
        page = f"page {location['page']}, " if location.get("page") else "photo text, "
        return f"{page}“{location['quote']}”"
    return ""
