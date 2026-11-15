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


class ConflictValue(BaseModel):
    """A value a person can choose in a conflict."""

    id: UUID = Field(..., description="Standard value ID, to choose it.")
    display: str = Field(..., description="The value in standard form.")
    raw_value: str = Field(..., description="Exactly as the source gave it.")
    source_file_name: str = Field(..., description="The file it came from.")
    location: dict[str, Any] = Field(..., description="Where in the file.")
    flags: list[str] = Field(..., description="How it was read.")


class ReviewTask(BaseModel):
    """Something a person needs to decide."""

    id: UUID = Field(..., description="Task ID.")
    kind: str = Field(..., description="image_match, attribute_conflict or category.")
    status: str = Field(..., description="open or resolved.")
    created_at: datetime = Field(..., description="When it was opened.")
    resolved_at: datetime | None = Field(None, description="When it was decided.")
    image_id: UUID | None = Field(None, description="image_match: the image.")
    source_file_id: UUID | None = Field(None, description="image_match: its file.")
    image_name: str | None = Field(None, description="image_match: its file name.")
    thumbnail_url: str | None = Field(None, description="image_match: a short-lived link.")
    candidates: list[MatchCandidate] = Field(
        default_factory=list, description="image_match: products it might show, best first."
    )
    product_id: UUID | None = Field(None, description="Conflicts and categories: the product.")
    product_key: str | None = Field(None, description="Its key.")
    attribute_code: str | None = Field(None, description="attribute_conflict: the attribute.")
    attribute_name: str | None = Field(None, description="attribute_conflict: its name.")
    values: list[ConflictValue] = Field(
        default_factory=list, description="attribute_conflict: every source's value."
    )
    suggested_category_id: UUID | None = Field(None, description="category: the suggestion.")
    suggested_category_name: str | None = Field(None, description="category: its name.")
    category_reason: str | None = Field(None, description="category: why, in a few words.")


class ReviewTaskList(BaseModel):
    """A batch's image-match tasks, open first."""

    batch_id: UUID = Field(..., description="The batch.")
    pending_photo_values: int = Field(
        ..., description="Accepted photos whose values haven't been read yet."
    )
    items: list[ReviewTask] = Field(..., description="Tasks.")


class DecisionRequest(BaseModel):
    """A person's decision on a task. Which fields are needed depends on the task's kind."""

    decision: Literal["accept", "reject", "choose", "set"] = Field(
        ...,
        description="image_match: accept one product or reject every candidate. "
        "attribute_conflict: choose one value, or set your own. category: choose one.",
    )
    product_id: UUID | None = Field(None, description="image_match accept: the product.")
    value_id: UUID | None = Field(None, description="attribute_conflict choose: the value.")
    value: str | None = Field(
        None, min_length=1, max_length=500, description="attribute_conflict set: your value."
    )
    category_id: UUID | None = Field(None, description="category choose: the category.")

    @model_validator(mode="after")
    def _fields_for_decision(self) -> "DecisionRequest":
        if self.decision == "accept" and self.product_id is None:
            raise ValueError("product_id is required to accept")
        if self.decision == "reject" and self.product_id is not None:
            raise ValueError("product_id must be left out to reject")
        if self.decision == "choose" and (self.value_id is None) == (self.category_id is None):
            raise ValueError("choose needs exactly one of value_id or category_id")
        if self.decision == "set" and not (self.value or "").strip():
            raise ValueError("set needs a value")
        return self


class DecisionResult(BaseModel):
    """What a decision did."""

    task_id: UUID = Field(..., description="The task, now resolved.")
    accepted_product_id: UUID | None = Field(None, description="The product accepted, if any.")
    shown: str | None = Field(None, description="Conflicts and categories: what it now shows.")
    photo_values: Literal["queued", "waiting", "not_needed"] = Field(
        ...,
        description="queued: values are being read from the photo. waiting: another run is "
        "active, so read them later with POST /batches/{id}/photo-values. not_needed: nothing "
        "to read.",
    )
    copy_writing: Literal["queued", "waiting", "not_needed"] = Field(
        "not_needed",
        description="queued: this was the batch's last review item, so copy is being written "
        "for products that need it. waiting: another run is active, so generate it later. "
        "not_needed: review items remain, or no copy needs writing.",
    )


class PhotoValuesResult(BaseModel):
    """Whether reading values from accepted photos started."""

    photo_values: Literal["queued", "waiting", "not_needed"] = Field(
        ..., description="As for a decision."
    )
    pending: int = Field(..., description="Accepted photos not yet read.")
