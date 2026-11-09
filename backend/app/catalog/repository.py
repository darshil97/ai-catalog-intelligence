"""Suppliers and batches, always scoped to their owner.

Every function takes the caller's user ID and filters on it, so another user's supplier or
batch behaves exactly like one that doesn't exist. Each function is a single query, because
each round trip from the API to Neon costs about 95 ms.
"""

from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection

from app.db import query
from app.utils.logger import get_logger

logger = get_logger(__name__)

Row = dict[str, Any]

_SUPPLIER_NAME_INDEX = "suppliers_owner_name"


class CatalogError(Exception):
    """Base class for catalog errors the API reports to the caller."""


class NotFoundError(CatalogError):
    """No such supplier or batch for this owner."""


class DuplicateNameError(CatalogError):
    """The owner already has a supplier with this name, ignoring case."""


class SupplierHasBatchesError(CatalogError):
    """A supplier can't be deleted while it has batches."""


class BatchBusyError(CatalogError):
    """A batch can't be deleted while it's queued or processing."""


def _is_duplicate_supplier_name(exc: psycopg.errors.UniqueViolation) -> bool:
    return exc.diag.constraint_name == _SUPPLIER_NAME_INDEX


def list_suppliers(conn: Connection[Any], owner: str, limit: int) -> list[Row]:
    """Return the owner's suppliers by name, each with its batch count."""
    return conn.execute(
        query("catalog", "list_suppliers"), {"owner": owner, "limit": limit}
    ).fetchall()


def create_supplier(conn: Connection[Any], owner: str, name: str) -> Row:
    """Create a supplier.

    Raises:
        DuplicateNameError: if the owner already has one with this name.
    """
    try:
        with conn.transaction():
            row: Row | None = conn.execute(
                query("catalog", "insert_supplier"), {"owner": owner, "name": name}
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        if _is_duplicate_supplier_name(exc):
            raise DuplicateNameError(name) from exc
        raise
    assert row is not None  # noqa: S101
    logger.info("Supplier created: supplier_id=%s owner=%s", row["id"], owner)
    return row


def get_supplier(conn: Connection[Any], owner: str, supplier_id: UUID) -> Row:
    """Return one supplier with its batch count.

    Raises:
        NotFoundError: if the owner has no such supplier.
    """
    row: Row | None = conn.execute(
        query("catalog", "get_supplier"), {"owner": owner, "supplier_id": supplier_id}
    ).fetchone()
    if row is None:
        raise NotFoundError(str(supplier_id))
    return row


def rename_supplier(conn: Connection[Any], owner: str, supplier_id: UUID, name: str) -> Row:
    """Rename a supplier.

    Raises:
        NotFoundError: if the owner has no such supplier.
        DuplicateNameError: if another of the owner's suppliers has this name.
    """
    try:
        with conn.transaction():
            row: Row | None = conn.execute(
                query("catalog", "rename_supplier"),
                {"owner": owner, "supplier_id": supplier_id, "name": name},
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        if _is_duplicate_supplier_name(exc):
            raise DuplicateNameError(name) from exc
        raise
    if row is None:
        raise NotFoundError(str(supplier_id))
    return row


def delete_supplier(conn: Connection[Any], owner: str, supplier_id: UUID) -> None:
    """Delete a supplier that has no batches.

    Raises:
        NotFoundError: if the owner has no such supplier.
        SupplierHasBatchesError: if it still has batches.
    """
    try:
        with conn.transaction():
            row: Row | None = conn.execute(
                query("catalog", "delete_supplier"), {"owner": owner, "supplier_id": supplier_id}
            ).fetchone()
    except psycopg.errors.ForeignKeyViolation as exc:
        # A batch was created between the check and the delete.
        raise SupplierHasBatchesError(str(supplier_id)) from exc
    if row is None:
        raise NotFoundError(str(supplier_id))
    if row["has_batches"]:
        raise SupplierHasBatchesError(str(supplier_id))
    logger.info("Supplier deleted: supplier_id=%s owner=%s", supplier_id, owner)


def list_batches(conn: Connection[Any], owner: str, supplier_id: UUID, limit: int) -> Row:
    """Return a supplier's ID and name, with its batches newest first under `batches`.

    Raises:
        NotFoundError: if the owner has no such supplier.
    """
    row: Row | None = conn.execute(
        query("catalog", "list_batches"),
        {"owner": owner, "supplier_id": supplier_id, "limit": limit},
    ).fetchone()
    if row is None:
        raise NotFoundError(str(supplier_id))
    return row


def create_batch(
    conn: Connection[Any], owner: str, supplier_id: UUID, name: str, retention_days: int
) -> Row:
    """Create a draft batch that expires after the retention period.

    Raises:
        NotFoundError: if the owner has no such supplier.
    """
    row: Row | None = conn.execute(
        query("catalog", "insert_batch"),
        {
            "owner": owner,
            "supplier_id": supplier_id,
            "name": name,
            "retention_days": retention_days,
        },
    ).fetchone()
    if row is None:
        raise NotFoundError(str(supplier_id))
    logger.info("Batch created: batch_id=%s supplier_id=%s owner=%s", row["id"], supplier_id, owner)
    return row


def get_batch(conn: Connection[Any], owner: str, batch_id: UUID) -> Row:
    """Return one batch with its supplier's name.

    Raises:
        NotFoundError: if the owner has no such batch.
    """
    row: Row | None = conn.execute(
        query("catalog", "get_batch"), {"owner": owner, "batch_id": batch_id}
    ).fetchone()
    if row is None:
        raise NotFoundError(str(batch_id))
    return row


def rename_batch(conn: Connection[Any], owner: str, batch_id: UUID, name: str) -> Row:
    """Rename a batch.

    Raises:
        NotFoundError: if the owner has no such batch.
    """
    row: Row | None = conn.execute(
        query("catalog", "rename_batch"), {"owner": owner, "batch_id": batch_id, "name": name}
    ).fetchone()
    if row is None:
        raise NotFoundError(str(batch_id))
    return row


def delete_batch(conn: Connection[Any], owner: str, batch_id: UUID) -> list[str]:
    """Delete a batch and, through cascades, everything in it.

    Returns:
        The storage keys of its files, to delete once the transaction commits.

    Raises:
        NotFoundError: if the owner has no such batch.
        BatchBusyError: if it's queued or processing.
    """
    row: Row | None = conn.execute(
        query("catalog", "delete_batch"), {"owner": owner, "batch_id": batch_id}
    ).fetchone()
    if row is None:
        raise NotFoundError(str(batch_id))
    if not row["deleted"]:
        raise BatchBusyError(str(batch_id))
    logger.info("Batch deleted: batch_id=%s owner=%s files=%d", batch_id, owner, len(row["keys"]))
    return list(row["keys"])
