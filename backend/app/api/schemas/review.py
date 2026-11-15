"""Schemas for image matches, review tasks and decisions."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class MatchEvidence(BaseModel):
    """What one signal found."""

    signal: str = Field(
        ...,
        description="code_in_text, code_in_name, from_product_page, brand_or_name_in_text, "
        "only_product, same_picture or looks_like_accepted.",
    )
    points: float = Field(..., description="How much it added to the score.")
    text: str = Field(..., description="What it found, for people.")
    detail: dict[str, Any] = Field(..., description="What it found, for programs.")


class MatchCandidate(BaseModel):
    """A product an image might show."""

    id: UUID = Field(..., description="Candidate ID.")
    product_id: UUID = Field(..., description="The product.")
    product_key: str = Field(..., description="Its key.")
    score: float = Field(..., description="Evidence total, from 0 to 1.")
    decision: str = Field(
        ..., description="auto_accepted, needs_review, accepted (by a person) or rejected."
    )
    decided_by: str = Field(..., description="'system', or the person who decided.")
    evidence: list[MatchEvidence] = Field(..., description="What each signal found.")


class ImageMatch(MatchCandidate):
    """A candidate, with the image it is for."""

    image_id: UUID = Field(..., description="The image.")
    source_file_id: UUID = Field(..., description="The image's file.")
    image_name: str = Field(..., description="The image's file name.")
    decided_at: datetime = Field(..., description="When the decision was made.")
    values_read_at: datetime | None = Field(
        None, description="When values were read from the photo for this product."
    )


class ImageMatchList(BaseModel):
    """Every candidate of a batch, in file order, best first for each image."""

    batch_id: UUID = Field(..., description="The batch.")
    items: list[ImageMatch] = Field(..., description="Candidates.")


class ReviewTask(BaseModel):
    """An image whose product a person needs to confirm."""

    id: UUID = Field(..., description="Task ID.")
    status: str = Field(..., description="open or resolved.")
    image_id: UUID = Field(..., description="The image.")
    source_file_id: UUID = Field(..., description="Its file.")
    image_name: str = Field(..., description="Its file name.")
    thumbnail_url: str | None = Field(None, description="A short-lived link to a small JPEG.")
    candidates: list[MatchCandidate] = Field(..., description="Products it might show, best first.")
    created_at: datetime = Field(..., description="When it was opened.")
    resolved_at: datetime | None = Field(None, description="When it was decided.")


class ReviewTaskList(BaseModel):
    """A batch's image-match tasks, open first."""

    batch_id: UUID = Field(..., description="The batch.")
    pending_photo_values: int = Field(
        ..., description="Accepted photos whose values haven't been read yet."
    )
    items: list[ReviewTask] = Field(..., description="Tasks.")


class DecisionRequest(BaseModel):
    """A person's decision on a task."""

    decision: Literal["accept", "reject"] = Field(
        ..., description="accept one product, or reject every candidate."
    )
    product_id: UUID | None = Field(None, description="The product to accept. Required to accept.")

    @model_validator(mode="after")
    def _product_for_accept(self) -> "DecisionRequest":
        if self.decision == "accept" and self.product_id is None:
            raise ValueError("product_id is required to accept")
        if self.decision == "reject" and self.product_id is not None:
            raise ValueError("product_id must be left out to reject")
        return self


class DecisionResult(BaseModel):
    """What a decision did."""

    task_id: UUID = Field(..., description="The task, now resolved.")
    accepted_product_id: UUID | None = Field(None, description="The product accepted, if any.")
    photo_values: Literal["queued", "waiting", "not_needed"] = Field(
        ...,
        description="queued: values are being read from the photo. waiting: another run is "
        "active, so read them later with POST /batches/{id}/photo-values. not_needed: nothing "
        "to read.",
    )


class PhotoValuesResult(BaseModel):
    """Whether reading values from accepted photos started."""

    photo_values: Literal["queued", "waiting", "not_needed"] = Field(
        ..., description="As for a decision."
    )
    pending: int = Field(..., description="Accepted photos not yet read.")
