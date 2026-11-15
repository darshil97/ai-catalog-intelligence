"""Loads what the rules read about one batch: seven queries, whatever its size."""

from collections import defaultdict
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection

from app.config import get_enrichment_config
from app.db import query
from app.enrichment import build_facts
from app.enrichment.repository import category_paths
from app.validation.base import Attribute, BatchContext, ProductData, Row


def _attribute(row: Row) -> Attribute:
    shown = row["shown"]
    if not shown and row["reviewer_value"]:
        shown = (row["reviewer_value"] or {}).get("text")
    number = row["value_number"]
    return Attribute(
        code=row["code"],
        name=row["name"],
        state=row["state"] or "missing",
        family=row["unit_family"],
        shown=shown,
        number=Decimal(str(number)) if number is not None else None,
        comparable=row["comparable"],
        flags=tuple(row["flags"] or ()),
    )


def _text_state(product: Row, current_fingerprint: str) -> str:
    """generated, out_of_date, skipped, failed or none, as M7 decides it."""
    stored = product["enrichment_state"]
    if stored in ("skipped", "failed"):
        return str(stored)
    if product["input_fingerprint"] is None:
        return "none"
    return "generated" if product["input_fingerprint"] == current_fingerprint else "out_of_date"


def load_batch(conn: Connection[Any], owner: str, batch_id: UUID) -> BatchContext:
    """Everything the rules read about a batch the owner has."""
    params = {"owner": owner, "batch_id": batch_id}
    products = conn.execute(query("validation", "products"), params).fetchall()
    rows: dict[UUID, list[Row]] = defaultdict(list)
    for row in conn.execute(query("validation", "attributes"), params).fetchall():
        rows[row["product_id"]].append(row)
    photos: dict[UUID, list[Row]] = defaultdict(list)
    for row in conn.execute(query("validation", "photos"), params).fetchall():
        photos[row["product_id"]].append(row)
    reviews: dict[UUID, list[str]] = defaultdict(list)
    for row in conn.execute(query("validation", "open_reviews"), params).fetchall():
        reviews[row["product_id"]].append(row["kind"])
    pairs = {
        frozenset((row["product_a_id"], row["product_b_id"])): row["status"]
        for row in conn.execute(query("validation", "variant_pairs"), params).fetchall()
    }
    paths = category_paths(conn) if products else {}
    enrichment = get_enrichment_config()

    out = []
    for product in products:
        attribute_rows = rows.get(product["id"], [])
        category = {
            "path": paths.get(product["category_id"]) if product["category_id"] else None,
            "source": product["category_source"],
            "state": product["category_state"],
        }
        sheet = build_facts(attribute_rows, category, enrichment)
        out.append(
            ProductData(
                id=product["id"],
                key=product["product_key"],
                row=product,
                attributes={row["code"]: _attribute(row) for row in attribute_rows},
                photos=photos.get(product["id"], []),
                open_reviews=reviews.get(product["id"], []),
                text_state=_text_state(product, sheet.fingerprint),
            )
        )
    return BatchContext(batch_id=batch_id, products=out, variant_pairs=pairs)
