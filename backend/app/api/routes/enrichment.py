"""Generated titles, descriptions and bullets (M7): start, resume, and see where each stands."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import ActiveUser
from app.api.schemas.enrichment import BatchEnrichment, EnrichmentRequest, EnrichmentStarted
from app.api.services import enrichment

router = APIRouter(tags=["enrichment"])


@router.get("/batches/{batch_id}/enrichment", response_model=BatchEnrichment)
def batch_enrichment(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """Each product's text state, what Generate would cover, and the latest run."""
    return enrichment.batch_enrichment(profile, batch_id)


@router.post(
    "/batches/{batch_id}/enrichment",
    response_model=EnrichmentStarted,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_enrichment(
    batch_id: UUID, profile: ActiveUser, request: EnrichmentRequest | None = None
) -> dict[str, Any]:
    """Write text for products whose text is missing, failed or out of date. Uses no submission.

    Only settled facts are used, and every claim is checked against them (D37).
    """
    return enrichment.start(profile, batch_id, request.product_ids if request else None)


@router.post(
    "/batches/{batch_id}/enrichment/resume",
    response_model=EnrichmentStarted,
    status_code=status.HTTP_202_ACCEPTED,
)
def resume_enrichment(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """Resume a run paused by the monthly LLM budget, once there's budget again."""
    return enrichment.resume(profile, batch_id)
