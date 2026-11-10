"""Data access for products, their observations, and how each sheet was mapped.

Read-only: products are written by the worker's extract jobs. Functions take an open
connection, and every query is scoped to the owner.
"""

from typing import Any
from uuid import UUID

from psycopg import Connection

from app.db import query

Row = dict[str, Any]


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
    return {**product, "observations": observations}


def list_sheets(conn: Connection[Any], owner: str, batch_id: UUID, limit: int) -> list[Row]:
    """Return how each sheet of a batch was mapped. Empty if the batch isn't the owner's."""
    return conn.execute(
        query("products", "list_sheets"), {"owner": owner, "batch_id": batch_id, "limit": limit}
    ).fetchall()
