"""Supplier endpoints, and the batches listed and created under a supplier."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.dependencies import ActiveUser
from app.api.schemas.catalog import (
    Batch,
    BatchCreate,
    BatchList,
    Supplier,
    SupplierCreate,
    SupplierList,
    SupplierUpdate,
)
from app.api.services import catalog

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("", response_model=SupplierList)
def list_suppliers(profile: ActiveUser) -> dict[str, Any]:
    """List your suppliers."""
    return catalog.list_suppliers(profile)


@router.post("", response_model=Supplier, status_code=status.HTTP_201_CREATED)
def create_supplier(request: SupplierCreate, profile: ActiveUser) -> dict[str, Any]:
    """Create a supplier. 409 if you already have one with this name."""
    return catalog.create_supplier(profile, request.name)


@router.get("/{supplier_id}", response_model=Supplier)
def get_supplier(supplier_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """Return one of your suppliers."""
    return catalog.get_supplier(profile, supplier_id)


@router.patch("/{supplier_id}", response_model=Supplier)
def rename_supplier(
    supplier_id: UUID, request: SupplierUpdate, profile: ActiveUser
) -> dict[str, Any]:
    """Rename one of your suppliers."""
    return catalog.rename_supplier(profile, supplier_id, request.name)


@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supplier(supplier_id: UUID, profile: ActiveUser) -> Response:
    """Delete one of your suppliers. 409 while it still has batches."""
    catalog.delete_supplier(profile, supplier_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{supplier_id}/batches", response_model=BatchList)
def list_batches(supplier_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """List a supplier's batches, newest first."""
    return catalog.list_batches(profile, supplier_id)


@router.post("/{supplier_id}/batches", response_model=Batch, status_code=status.HTTP_201_CREATED)
def create_batch(supplier_id: UUID, request: BatchCreate, profile: ActiveUser) -> dict[str, Any]:
    """Create a draft batch for one of your suppliers."""
    return catalog.create_batch(profile, supplier_id, request.name)
