"""Exports (M10, D41): files of a batch's approved products, and variant decisions."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import ActiveUser
from app.api.schemas.export import (
    DownloadLink,
    ExportList,
    ExportMade,
    ExportRequest,
    VariantDecided,
    VariantDecision,
)
from app.api.services import export

router = APIRouter(tags=["export"])


@router.post(
    "/batches/{batch_id}/exports",
    response_model=ExportMade,
    status_code=status.HTTP_201_CREATED,
)
def create_export(batch_id: UUID, request: ExportRequest, profile: ActiveUser) -> Any:
    """Make a CSV, Excel or Shopify file of the batch's approved products (only those)."""
    return export.create(profile, batch_id, request.format)


@router.get("/batches/{batch_id}/exports", response_model=ExportList)
def list_exports(batch_id: UUID, profile: ActiveUser) -> Any:
    """The batch's exports, newest first."""
    return export.list_exports(profile, batch_id)


@router.get("/exports/{export_id}/download", response_model=DownloadLink)
def download(export_id: UUID, profile: ActiveUser) -> Any:
    """A fresh 15-minute link to download an export."""
    return export.download(profile, export_id)


@router.post("/variants/{variant_id}/decision", response_model=VariantDecided)
def decide_variant(variant_id: UUID, request: VariantDecision, profile: ActiveUser) -> Any:
    """Confirm two products as variants of one product, or say they aren't. Recorded."""
    return export.decide_variant(profile, variant_id, request.decision)
