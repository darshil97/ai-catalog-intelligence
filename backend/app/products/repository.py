"""Data access for products, their observations, and how each sheet was mapped.

Read-only: products are written by the worker's extract jobs. Functions take an open
connection, and every query is scoped to the owner.
"""

import re
import unicodedata
from typing import Any
from uuid import UUID

from psycopg import Connection

from app.db import query

Row = dict[str, Any]

_SPACE = re.compile(r"\s+")


def _comparable(value: str) -> str:
    """A value with case, width and spacing ignored, for spotting sources that disagree."""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()


def differing_attributes(observations: list[Row]) -> list[str]:
    """Return the attribute codes whose observations don't all say the same thing.

    Only case and spacing are ignored. Whether "1.5 L" and "1500 ml" agree is unit work for
    normalization, so here they differ. Nothing is resolved or overwritten.
    """
    seen: dict[str, set[str]] = {}
    for observation in observations:
        seen.setdefault(observation["attribute_code"], set()).add(
            _comparable(observation["raw_value"])
        )
    return sorted(code for code, values in seen.items() if len(values) > 1)


class ProductNotFoundError(Exception):
    """The owner has no such product."""


def list_products(conn: Connection[Any], owner: str, batch_id: UUID, limit: int) -> list[Row]:
    """Return a batch's products in file order. Empty if the batch isn't the owner's."""
    return conn.execute(
        query("products", "list_products"), {"owner": owner, "batch_id": batch_id, "limit": limit}
    ).fetchall()


def get_product(conn: Connection[Any], owner: str, product_id: UUID, limit: int) -> Row:
    """Return a product with its observations, each with the place it was read from.

    Raises:
        ProductNotFoundError: if the owner has no such product.
    """
    params = {"owner": owner, "product_id": product_id}
    product: Row | None = conn.execute(query("products", "get_product"), params).fetchone()
    if product is None:
        raise ProductNotFoundError(str(product_id))
    observations = conn.execute(
        query("products", "product_observations"), {**params, "limit": limit}
    ).fetchall()
    return {
        **product,
        "observations": observations,
        "differing_attributes": differing_attributes(observations),
    }


def list_sheets(conn: Connection[Any], owner: str, batch_id: UUID, limit: int) -> list[Row]:
    """Return how each sheet of a batch was mapped. Empty if the batch isn't the owner's."""
    return conn.execute(
        query("products", "list_sheets"), {"owner": owner, "batch_id": batch_id, "limit": limit}
    ).fetchall()


def list_documents(conn: Connection[Any], owner: str, batch_id: UUID, limit: int) -> list[Row]:
    """Return how each PDF and photo of a batch was read. Empty if the batch isn't the owner's."""
    return conn.execute(
        query("products", "list_documents"),
        {"owner": owner, "batch_id": batch_id, "limit": limit},
    ).fetchall()
