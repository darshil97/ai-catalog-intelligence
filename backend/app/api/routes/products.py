"""Product endpoints: what was found in a batch's spreadsheets, and where each value came from."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import ActiveUser
from app.api.schemas.products import (
    BatchImageList,
    DocumentList,
    ProductDetail,
    ProductList,
    SheetList,
)
from app.api.services import products

router = APIRouter(tags=["products"])


@router.get("/batches/{batch_id}/products", response_model=ProductList)
def list_products(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """List the products kept from a batch, in file order."""
    return products.list_products(profile, batch_id)


@router.get("/products/{product_id}", response_model=ProductDetail)
def get_product(product_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """Return a product and every value read for it, each with its file, sheet, row and column."""
    return products.get_product(profile, product_id)


@router.get("/batches/{batch_id}/sheets", response_model=SheetList)
def list_sheets(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """List a batch's sheets: how each column was mapped, and why a sheet wasn't used."""
    return products.list_sheets(profile, batch_id)


@router.get("/batches/{batch_id}/documents", response_model=DocumentList)
def list_documents(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """List a batch's PDFs and photos: how each was read, and what was found in it."""
    return products.list_documents(profile, batch_id)


@router.get("/batches/{batch_id}/images", response_model=BatchImageList)
def list_images(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """List a batch's kept images, with thumbnails and near-duplicate groups."""
    return products.list_images(profile, batch_id)
