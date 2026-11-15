"""Image matches and review: which product each photo shows, and a person's decisions."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import ActiveUser
from app.api.schemas.review import (
    DecisionRequest,
    DecisionResult,
    ImageMatchList,
    PhotoValuesResult,
    ReviewTaskList,
)
from app.api.services import review

router = APIRouter(tags=["review"])


@router.get("/batches/{batch_id}/matches", response_model=ImageMatchList)
def list_matches(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """List every image-product candidate of a batch, with its evidence and decision."""
    return review.list_matches(profile, batch_id)


@router.get("/batches/{batch_id}/review-tasks", response_model=ReviewTaskList)
def list_review_tasks(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """List the photos whose product needs confirming, open first."""
    return review.list_tasks(profile, batch_id)


@router.post("/review-tasks/{task_id}/decision", response_model=DecisionResult)
def decide(task_id: UUID, request: DecisionRequest, profile: ActiveUser) -> dict[str, Any]:
    """Accept one product for the photo, or reject every candidate. Recorded for audit.

    Accepting starts reading the photo's values for the product.
    """
    return review.decide(profile, task_id, request.decision, request.product_id)


@router.post(
    "/batches/{batch_id}/photo-values",
    response_model=PhotoValuesResult,
    status_code=status.HTTP_202_ACCEPTED,
)
def read_photo_values(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """Read values from accepted photos not read yet. Uses no submission."""
    return review.read_photo_values(profile, batch_id)
