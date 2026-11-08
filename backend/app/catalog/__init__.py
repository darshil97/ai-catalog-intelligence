"""Catalog structure: suppliers and batches, shared by the API and worker."""

from app.catalog.repository import (
    CatalogError,
    DuplicateNameError,
    NotFoundError,
    SupplierHasBatchesError,
    create_batch,
    create_supplier,
    delete_batch,
    delete_supplier,
    get_batch,
    get_supplier,
    list_batches,
    list_suppliers,
    rename_batch,
    rename_supplier,
)

__all__ = [
    "CatalogError",
    "DuplicateNameError",
    "NotFoundError",
    "SupplierHasBatchesError",
    "create_batch",
    "create_supplier",
    "delete_batch",
    "delete_supplier",
    "get_batch",
    "get_supplier",
    "list_batches",
    "list_suppliers",
    "rename_batch",
    "rename_supplier",
]
