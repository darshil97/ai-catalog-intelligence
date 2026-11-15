"""Human review (M9, D40): edit values and copy, settle duplicates, approve and reject."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import ActiveUser
from app.api.schemas.approval import (
    BatchApproval,
    BatchApprovalRequest,
    BatchApprovalResult,
    BatchRejectRequest,
    CopyEdit,
    DuplicateRequest,
    History,
    RejectRequest,
    ReviewSummary,
    RevisionRequest,
    Saved,
    ValueEdit,
)
from app.api.services import approval

router = APIRouter(tags=["approval"])


@router.put("/products/{product_id}/values/{code}", response_model=Saved)
def set_value(product_id: UUID, code: str, request: ValueEdit, profile: ActiveUser) -> Any:
    """Set a value (or clear it with null). Processing never overwrites it. Recorded."""
    return approval.set_value(profile, product_id, code, request)


@router.put("/products/{product_id}/copy", response_model=Saved)
def edit_copy(product_id: UUID, request: CopyEdit, profile: ActiveUser) -> Any:
    """Save copy you wrote. Automatic writing never replaces it. Recorded."""
    return approval.edit_copy(profile, product_id, request)


@router.post("/products/{product_id}/approve", response_model=Saved)
def approve(product_id: UUID, request: RevisionRequest, profile: ActiveUser) -> Any:
    """Approve: needs no errors, current copy and no open review items."""
    return approval.approve(profile, product_id, request.revision)


@router.post("/products/{product_id}/reject", response_model=Saved)
def reject(product_id: UUID, request: RejectRequest, profile: ActiveUser) -> Any:
    """Reject with a reason. It leaves checks, approval and export."""
    return approval.reject(profile, product_id, request.reason, request.revision)


@router.post("/products/{product_id}/restore", response_model=Saved)
def restore(product_id: UUID, request: RevisionRequest, profile: ActiveUser) -> Any:
    """Put an approved or rejected product back to draft."""
    return approval.restore(profile, product_id, request.revision)


@router.post("/products/{product_id}/duplicate", response_model=Saved)
def duplicate(product_id: UUID, request: DuplicateRequest, profile: ActiveUser) -> Any:
    """Settle a flagged duplicate pair: one product (keep this or the other), or different."""
    return approval.duplicate(profile, product_id, request)


@router.get("/products/{product_id}/history", response_model=History)
def history(product_id: UUID, profile: ActiveUser) -> Any:
    """Every recorded change to the product, newest first."""
    return approval.history(profile, product_id)


@router.get("/batches/{batch_id}/approval", response_model=BatchApproval)
def batch_approval(batch_id: UUID, profile: ActiveUser) -> Any:
    """Each product's approval state, and what blocks it."""
    return approval.batch_approval(profile, batch_id)


@router.post("/batches/{batch_id}/approve", response_model=BatchApprovalResult)
def approve_batch(batch_id: UUID, request: BatchApprovalRequest, profile: ActiveUser) -> Any:
    """Approve every ready product (or those listed). Says why others weren't."""
    return approval.approve_batch(profile, batch_id, request.product_ids)


@router.post("/batches/{batch_id}/reject", response_model=BatchApprovalResult)
def reject_batch(batch_id: UUID, request: BatchRejectRequest, profile: ActiveUser) -> Any:
    """Reject the listed products of a batch."""
    return approval.reject_batch(profile, batch_id, request.product_ids, request.reason)


@router.get("/review", response_model=ReviewSummary)
def review_summary(profile: ActiveUser) -> Any:
    """Open review items across all your batches."""
    return approval.review_summary(profile)
