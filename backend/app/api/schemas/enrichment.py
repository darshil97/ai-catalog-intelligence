"""Schemas for generated titles, descriptions and bullets (M7)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class EnrichmentRequest(BaseModel):
    """Which products to write text for."""

    product_ids: list[UUID] | None = Field(
        None,
        max_length=500,
        description="Products of this batch. Omitted: every product whose text is missing, "
        "failed or out of date.",
    )


class EnrichmentRun(BaseModel):
    """The batch's latest enrich run."""

    id: UUID = Field(..., description="Run ID.")
    status: str = Field(
        ...,
        description="queued, running, paused (a global cap was reached; resume it later), "
        "succeeded, failed or cancelled.",
    )
    pause_reason: str | None = Field(None, description="llm_spend_cap when paused.")
    blocked: int = Field(..., description="Products waiting because the run is paused.")
    waiting: int = Field(..., description="Jobs still queued or running.")
    done: int = Field(..., description="Products finished in this run.")
    total: int = Field(..., description="Products in this run.")
    created_at: datetime = Field(..., description="When it was started.")
    finished_at: datetime | None = Field(None, description="When it finished.")


class EnrichmentStarted(BaseModel):
    """A started or resumed run."""

    run_id: UUID = Field(..., description="The run.")
    products: int = Field(..., description="Products it will write text for.")


class LeftOutAttribute(BaseModel):
    """An attribute the text can't use, and why."""

    code: str = Field(..., description="Attribute code, or category.")
    name: str = Field(..., description="Attribute name.")
    state: str = Field(..., description="conflict, unclear, missing or needs_review.")
    reason: str = Field(..., description="Why, for people.")


class ProductEnrichment(BaseModel):
    """One product's text, in short."""

    product_id: UUID = Field(..., description="The product.")
    product_key: str = Field(..., description="Its key.")
    state: str = Field(
        ...,
        description="none, generated, out_of_date (its facts changed since), skipped (not "
        "enough settled information) or failed.",
    )
    message: str | None = Field(None, description="Why it was skipped or failed.")
    version: int | None = Field(None, description="The current text's version.")
    title: str | None = Field(None, description="The current title.")
    facts: int = Field(..., description="Settled facts it can be written from.")
    left_out: int = Field(..., description="Attributes that can't be used yet.")
    can_generate: bool = Field(..., description="Whether generating now could change anything.")
    open_reviews: int = Field(0, description="Review items about it still waiting.")
    reasons: list[str] = Field(
        default_factory=list,
        description="Why its copy is missing, waiting or out of date, for people.",
    )


class BatchEnrichment(BaseModel):
    """Every product's text state, and the latest run."""

    batch_id: UUID = Field(..., description="The batch.")
    open_reviews: int = Field(
        ..., description="Conflict and category decisions waiting; those facts are left out."
    )
    can_generate: int = Field(..., description="Products Generate would cover.")
    budget_reached: bool = Field(..., description="Whether this month's LLM budget is used up.")
    run: EnrichmentRun | None = Field(None, description="The latest enrich run.")
    items: list[ProductEnrichment] = Field(..., description="Products, in file order.")


class Citation(BaseModel):
    """A fact a claim cites, as it was when the text was written."""

    code: str = Field(..., description="Attribute code, or category.")
    name: str = Field(..., description="Attribute name.")
    display: str = Field(..., description="Its value then.")
    state: str = Field(..., description="verified, reviewer_set, assigned or person.")
    attribute_id: UUID | None = Field(None, description="The attribute, or null for category.")


class GeneratedClaim(BaseModel):
    """The title, one description sentence, or one bullet."""

    field: str = Field(..., description="title, description or bullets.")
    index: int = Field(..., description="Its position in the field.")
    text: str = Field(..., description="What it says.")
    facts: list[Citation] = Field(..., description="The facts it uses.")


class GeneratedContent(BaseModel):
    """A product's current generated text."""

    version: int | None = Field(None, description="1, 2, ..., or null before any text.")
    state: str = Field(..., description="generated, out_of_date, skipped or failed.")
    message: str | None = Field(None, description="Why the last attempt was skipped or failed.")
    title: str | None = Field(None, description="The title.")
    description: str | None = Field(None, description="The description.")
    bullets: list[str] = Field(default_factory=list, description="The bullets.")
    claims: list[GeneratedClaim] = Field(default_factory=list, description="Each with its facts.")
    status: str | None = Field(None, description="generated (M9 adds edited and approved).")
    model_id: str | None = Field(None, description="The model that wrote it.")
    prompt_version: str | None = Field(None, description="The prompt's version.")
    rules_version: str | None = Field(None, description="The grounding rules' version.")
    created_at: datetime | None = Field(None, description="When it was written.")
    left_out: list[LeftOutAttribute] = Field(
        default_factory=list, description="Attributes the text can't use yet, and why."
    )
    can_generate: bool = Field(..., description="Whether Regenerate could change anything.")
    open_reviews: int = Field(0, description="Review items about it still waiting.")
    reasons: list[str] = Field(
        default_factory=list,
        description="Why its copy is missing, waiting or out of date, for people.",
    )
