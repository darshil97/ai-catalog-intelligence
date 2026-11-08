"""Batch endpoints. Batches are listed and created under their supplier."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.dependencies import ActiveUser
from app.api.schemas.catalog import Batch, BatchUpdate
from app.api.services import catalog

router = APIRouter(prefix="/batches", tags=["batches"])


@router.get("/{batch_id}", response_model=Batch)
def get_batch(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """Return one of your batches."""
    return catalog.get_batch(profile, batch_id)


@router.patch("/{batch_id}", response_model=Batch)
def rename_batch(batch_id: UUID, request: BatchUpdate, profile: ActiveUser) -> dict[str, Any]:
    """Rename one of your batches."""
    return catalog.rename_batch(profile, batch_id, request.name)


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_id: UUID, profile: ActiveUser) -> Response:
    """Delete one of your batches, with everything in it."""
    catalog.delete_batch(profile, batch_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
