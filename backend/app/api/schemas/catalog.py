"""Schemas for suppliers and batches."""

import unicodedata
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class NameRequest(BaseModel):
    """A name for a supplier or batch."""

    name: str = Field(
        ...,
        description="Leading and trailing spaces are removed. Must not be empty.",
        examples=["Acme Kitchenware"],
    )

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        from app.config import get_catalog_config

        maximum = get_catalog_config()["max_name_length"]
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("name is empty, and must have at least one visible character")
        if len(cleaned) > maximum:
            raise ValueError(f"name has {len(cleaned)} characters, and must have at most {maximum}")
        # Control and format characters (such as zero-width spaces) make names that look equal
        # but aren't, which would defeat the duplicate-name check.
        if any(unicodedata.category(char) in {"Cc", "Cf"} for char in cleaned):
            raise ValueError("name contains control or invisible characters")
        return cleaned


class SupplierCreate(NameRequest):
    """A new supplier. Names are unique per user, ignoring case."""


class SupplierUpdate(NameRequest):
    """A supplier's new name."""


class BatchCreate(NameRequest):
    """A new batch for a supplier."""


class BatchUpdate(NameRequest):
    """A batch's new name."""


class Supplier(BaseModel):
    """A supplier owned by the signed-in user."""

    id: UUID = Field(..., description="Supplier ID.")
    name: str = Field(..., description="Supplier name.")
    batch_count: int = Field(..., description="How many batches it has.")
    created_at: datetime = Field(..., description="When it was created.")
    updated_at: datetime = Field(..., description="When it was last changed.")


class SupplierList(BaseModel):
    """The signed-in user's suppliers, by name."""

    items: list[Supplier] = Field(..., description="Suppliers, sorted by name.")


class Batch(BaseModel):
    """A batch of supplier files, processed together."""

    id: UUID = Field(..., description="Batch ID.")
    supplier_id: UUID = Field(..., description="The supplier it belongs to.")
    supplier_name: str | None = Field(None, description="The supplier's name.")
    name: str = Field(..., description="Batch name.")
    status: str = Field(..., description="Batch status. New batches are draft.")
    expires_at: datetime = Field(..., description="When it and its files will be deleted.")
    created_at: datetime = Field(..., description="When it was created.")
    updated_at: datetime = Field(..., description="When it was last changed.")


class BatchList(BaseModel):
    """A supplier's batches, newest first."""

    supplier_id: UUID = Field(..., description="The supplier.")
    supplier_name: str = Field(..., description="The supplier's name.")
    items: list[Batch] = Field(..., description="Batches, newest first.")
