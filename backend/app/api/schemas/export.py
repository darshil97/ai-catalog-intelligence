"""Schemas for exports and variant decisions (M10, D41)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ExportRequest(BaseModel):
    """Which file to make."""

    format: Literal["csv", "xlsx", "shopify_csv"] = Field(
        ..., description="csv, xlsx (with where every value came from) or shopify_csv."
    )


class LeftOut(BaseModel):
    """A product an export left out, and why."""

    product_key: str
    reason: str


class Export(BaseModel):
    """A file made from a batch's approved products."""

    id: UUID
    batch_id: UUID
    format: str = Field(..., description="csv, xlsx or shopify_csv.")
    status: str = Field(..., description="ready.")
    profile: str = Field(..., description="The marketplace profile it was approved under.")
    product_count: int = Field(..., description="Products in the file.")
    left_out_count: int = Field(..., description="Products not approved, so left out.")
    links_expire_at: datetime | None = Field(None, description="When its photo links stop.")
    size_bytes: int | None = None
    notes: list[str] = Field(default_factory=list, description="Changes made, for people.")
    created_at: datetime


class ExportMade(Export):
    """A new export, with its link and what was left out."""

    download_url: str = Field(..., description="Works for 15 minutes.")
    left_out: list[LeftOut] = Field(default_factory=list)


class ExportList(BaseModel):
    """A batch's exports, newest first."""

    batch_id: UUID
    items: list[Export]


class DownloadLink(BaseModel):
    """A fresh link to an export."""

    url: str = Field(..., description="Works for 15 minutes.")


class VariantDecision(BaseModel):
    """Confirm or dismiss a possible variant pair."""

    decision: Literal["confirmed", "dismissed"]


class VariantDecided(BaseModel):
    """A decided variant pair."""

    id: UUID
    status: str
