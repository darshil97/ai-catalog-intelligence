"""Schemas for human review (M9, D40): edits, approval, duplicates, history."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ValueEdit(BaseModel):
    """A value to set, or null to clear it."""

    value: str | None = Field(None, max_length=500, description="The new value; null clears it.")
    note: str | None = Field(None, max_length=500, description="Why, for the history.")
    revision: int = Field(..., description="The product's revision when the page loaded.")


class CopyEdit(BaseModel):
    """Copy a person wrote."""

    title: str = Field(..., min_length=1, max_length=600)
    description: str = Field("", max_length=4000)
    bullets: list[str] = Field(default_factory=list, max_length=10)
    revision: int = Field(..., description="The product's revision when the page loaded.")


class RevisionRequest(BaseModel):
    """A change that needs only the revision the page loaded."""

    revision: int = Field(..., description="The product's revision when the page loaded.")


class RejectRequest(BaseModel):
    """Reject a product."""

    reason: str = Field(..., min_length=1, max_length=500)
    revision: int = Field(..., description="The product's revision when the page loaded.")


class DuplicateRequest(BaseModel):
    """Settle a flagged duplicate pair."""

    other_id: UUID = Field(..., description="The other product of the pair.")
    keep: Literal["this", "other", "different"] = Field(
        ...,
        description="this or other: they're one product; the other is rejected. different: "
        "they're different products (not allowed for a shared barcode).",
    )
    revision: int = Field(..., description="This product's revision when the page loaded.")


class Approval(BaseModel):
    """Where a product stands for approval."""

    product_id: UUID = Field(..., description="The product.")
    product_key: str = Field(..., description="Its key.")
    state: str = Field(
        ...,
        description="draft, approved, changed_since_approval (it changed after approval, so "
        "it needs approving again), or rejected.",
    )
    revision: int = Field(..., description="Send this back with any change.")
    can_approve: bool = Field(..., description="No blockers, and not already approved.")
    blockers: list[str] = Field(..., description="Why it can't be approved yet, for people.")
    approved_at: datetime | None = Field(None, description="When it was approved.")
    approved_by: str | None = Field(None, description="Who approved it.")
    rejection_reason: str | None = Field(None, description="Why it was rejected.")
    duplicate_of: UUID | None = Field(None, description="The product kept, if a duplicate.")


class BatchApproval(BaseModel):
    """Every product's approval in a batch."""

    batch_id: UUID = Field(..., description="The batch.")
    approved: int = Field(..., description="Products approved and unchanged since.")
    ready: int = Field(..., description="Products that can be approved now.")
    items: list[Approval] = Field(..., description="Products, in file order.")


class Saved(BaseModel):
    """A change saved."""

    revision: int = Field(..., description="The product's new revision.")
    copy_writing: str = Field(
        "not_needed", description="queued when the change starts rewriting copy."
    )


class BatchApprovalRequest(BaseModel):
    """Which products to approve; omitted means every ready product."""

    product_ids: list[UUID] | None = Field(None, max_length=500)


class BatchRejectRequest(BaseModel):
    """Products to reject together."""

    product_ids: list[UUID] = Field(..., min_length=1, max_length=500)
    reason: str = Field(..., min_length=1, max_length=500)


class Skipped(BaseModel):
    """A product a bulk approval left out, and why."""

    product_id: UUID
    product_key: str
    reasons: list[str]


class BatchApprovalResult(BaseModel):
    """What a bulk approval did."""

    approved: list[UUID] = Field(..., description="Products approved now.")
    skipped: list[Skipped] = Field(..., description="Products not ready, with reasons.")


class HistoryEntry(BaseModel):
    """One change to a product."""

    at: datetime
    by: str = Field(..., description="you, system, or a person's ID.")
    kind: str
    action: str
    text: str = Field(..., description="What happened, in words.")
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class History(BaseModel):
    """A product's history, newest first."""

    product_id: UUID
    items: list[HistoryEntry]


class ReviewBatch(BaseModel):
    """A batch with review items waiting."""

    batch_id: UUID
    batch_name: str
    supplier_name: str
    open: int


class ReviewSummary(BaseModel):
    """Open review items across all your batches."""

    open: int = Field(..., description="All open review items.")
    batches: list[ReviewBatch] = Field(..., description="Batches with items, newest first.")
