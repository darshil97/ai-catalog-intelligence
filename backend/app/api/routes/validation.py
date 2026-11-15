"""Catalog checks (M8): each product's errors, warnings and info under a marketplace profile."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import ActiveUser
from app.api.schemas.validation import (
    BatchValidation,
    ProductValidation,
    ProfileRequest,
    SetAsideRequest,
)
from app.api.services import validation

router = APIRouter(tags=["validation"])


@router.get("/batches/{batch_id}/validation", response_model=BatchValidation)
def batch_validation(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """Check every product of a batch under its marketplace profile. Always current."""
    return validation.batch_validation(profile, batch_id)


@router.put("/batches/{batch_id}/validation/profile", response_model=BatchValidation)
def set_profile(batch_id: UUID, request: ProfileRequest, profile: ActiveUser) -> dict[str, Any]:
    """Choose the batch's marketplace profile (generic, shopify or amazon_in)."""
    return validation.set_profile(profile, batch_id, request.profile)


@router.post("/products/{product_id}/validation/set-aside", response_model=ProductValidation)
def set_aside(product_id: UUID, request: SetAsideRequest, profile: ActiveUser) -> dict[str, Any]:
    """Set a warning aside with a reason, until what it checked changes. Recorded for audit."""
    return validation.set_aside(profile, product_id, request)


@router.delete("/products/{product_id}/validation/set-aside", response_model=ProductValidation)
def undo_set_aside(
    product_id: UUID,
    profile: ActiveUser,
    rule: str = Query(..., min_length=1, max_length=100),
    subject: str = Query("", max_length=100),
) -> dict[str, Any]:
    """Bring a set-aside warning back. Recorded for audit."""
    return validation.undo_set_aside(profile, product_id, rule, subject)
