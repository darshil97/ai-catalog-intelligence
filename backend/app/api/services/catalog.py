"""Supplier and batch operations for the signed-in user.

Each operation is one query in one transaction. Another user's supplier or batch returns the
same 404 as a missing one.
"""

from typing import Any
from uuid import UUID

from app import catalog
from app.api.services.common import database, http_error, not_found
from app.auth import Profile
from app.config import get_catalog_config
from app.utils.logger import get_logger

logger = get_logger(__name__)


def list_suppliers(profile: Profile) -> dict[str, Any]:
    """Return the caller's suppliers."""
    limit = get_catalog_config()["max_list_items"]
    with database("list_suppliers") as conn:
        return {"items": catalog.list_suppliers(conn, profile.user_id, limit)}


def create_supplier(profile: Profile, name: str) -> dict[str, Any]:
    """Create a supplier.

    Raises:
        HTTPException: 409 if the caller already has a supplier with this name.
    """
    try:
        with database("create_supplier") as conn:
            return catalog.create_supplier(conn, profile.user_id, name)
    except catalog.DuplicateNameError as exc:
        logger.info("Supplier refused: reason=duplicate_name owner=%s", profile.user_id)
        raise http_error(
            409, "duplicate_name", "You already have a supplier with this name"
        ) from exc


def get_supplier(profile: Profile, supplier_id: UUID) -> dict[str, Any]:
    """Return one supplier, or 404."""
    try:
        with database("get_supplier") as conn:
            return catalog.get_supplier(conn, profile.user_id, supplier_id)
    except catalog.NotFoundError:
        not_found()


def rename_supplier(profile: Profile, supplier_id: UUID, name: str) -> dict[str, Any]:
    """Rename a supplier.

    Raises:
        HTTPException: 404 if missing, or 409 for a duplicate name.
    """
    try:
        with database("rename_supplier") as conn:
            return catalog.rename_supplier(conn, profile.user_id, supplier_id, name)
    except catalog.NotFoundError:
        not_found()
    except catalog.DuplicateNameError as exc:
        logger.info("Supplier rename refused: reason=duplicate_name owner=%s", profile.user_id)
        raise http_error(
            409, "duplicate_name", "You already have a supplier with this name"
        ) from exc


def delete_supplier(profile: Profile, supplier_id: UUID) -> None:
    """Delete a supplier with no batches.

    Raises:
        HTTPException: 404 if missing, or 409 if it still has batches.
    """
    try:
        with database("delete_supplier") as conn:
            catalog.delete_supplier(conn, profile.user_id, supplier_id)
    except catalog.NotFoundError:
        not_found()
    except catalog.SupplierHasBatchesError as exc:
        logger.info("Supplier delete refused: reason=has_batches supplier_id=%s", supplier_id)
        raise http_error(
            409, "supplier_has_batches", "Delete this supplier's batches first"
        ) from exc


def list_batches(profile: Profile, supplier_id: UUID) -> dict[str, Any]:
    """Return a supplier's batches, or 404 if the supplier isn't the caller's."""
    limit = get_catalog_config()["max_list_items"]
    try:
        with database("list_batches") as conn:
            row = catalog.list_batches(conn, profile.user_id, supplier_id, limit)
    except catalog.NotFoundError:
        not_found()
    items = [{**batch, "supplier_name": row["supplier_name"]} for batch in row["batches"]]
    return {
        "supplier_id": row["supplier_id"],
        "supplier_name": row["supplier_name"],
        "items": items,
    }


def create_batch(profile: Profile, supplier_id: UUID, name: str) -> dict[str, Any]:
    """Create a batch under one of the caller's suppliers, or 404."""
    retention_days = get_catalog_config()["retention_days"]
    try:
        with database("create_batch") as conn:
            return catalog.create_batch(conn, profile.user_id, supplier_id, name, retention_days)
    except catalog.NotFoundError:
        not_found()


def get_batch(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return one batch, or 404."""
    try:
        with database("get_batch") as conn:
            return catalog.get_batch(conn, profile.user_id, batch_id)
    except catalog.NotFoundError:
        not_found()


def rename_batch(profile: Profile, batch_id: UUID, name: str) -> dict[str, Any]:
    """Rename a batch, or 404."""
    try:
        with database("rename_batch") as conn:
            return catalog.rename_batch(conn, profile.user_id, batch_id, name)
    except catalog.NotFoundError:
        not_found()


def delete_batch(profile: Profile, batch_id: UUID) -> None:
    """Delete a batch, or 404."""
    try:
        with database("delete_batch") as conn:
            catalog.delete_batch(conn, profile.user_id, batch_id)
    except catalog.NotFoundError:
        not_found()
