"""Schemas for catalog checks (M8, D38)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SetAsideInfo(BaseModel):
    """Who set a warning aside, and why."""

    reason: str = Field(..., description="Their reason.")
    by: str = Field(..., description="The person.")
    at: datetime = Field(..., description="When.")


class ValidationIssue(BaseModel):
    """One thing a check found."""

    rule: str = Field(..., description="The rule's code, such as required_attribute.")
    rule_version: int = Field(..., description="The rule's version.")
    severity: str = Field(
        ...,
        description="error (blocks approval; can't be set aside), warning (can be set aside "
        "with a reason) or info.",
    )
    subject: str = Field(
        ..., description="The attribute code, the other product's ID, or '' for the product."
    )
    message: str = Field(..., description="What's wrong, for people.")
    other_product_id: UUID | None = Field(None, description="The other product of a duplicate.")
    fix: str | None = Field(None, description="Where to fix it: review, generate or photos.")
    set_aside: SetAsideInfo | None = Field(None, description="Set aside, while unchanged.")


class ProductValidation(BaseModel):
    """A product's checks under its batch's profile."""

    profile: str = Field(..., description="The batch's marketplace profile.")
    profile_name: str = Field(..., description="Its name, such as Shopify.")
    can_approve: bool = Field(..., description="No errors. M9 approves only these.")
    errors: int = Field(..., description="Errors.")
    warnings: int = Field(..., description="Warnings not set aside.")
    info: int = Field(..., description="Info items.")
    issues: list[ValidationIssue] = Field(..., description="Errors first; set-aside last.")


class ProductValidationSummary(ProductValidation):
    """One product's checks, in a batch's list."""

    product_id: UUID = Field(..., description="The product.")
    product_key: str = Field(..., description="Its key.")


class ProfileOption(BaseModel):
    """A marketplace profile a batch can use."""

    code: str = Field(..., description="Profile code, such as shopify.")
    name: str = Field(..., description="Its name.")


class BatchValidation(BaseModel):
    """Every product's checks under the batch's profile."""

    batch_id: UUID = Field(..., description="The batch.")
    profile: str = Field(..., description="The batch's marketplace profile.")
    profile_name: str = Field(..., description="Its name.")
    profiles: list[ProfileOption] = Field(..., description="Profiles to choose from.")
    rules_version: str = Field(..., description="The rules' version, such as checks@1.")
    ready: int = Field(..., description="Products with no errors.")
    items: list[ProductValidationSummary] = Field(..., description="Products, in file order.")


class ProfileRequest(BaseModel):
    """A batch's new marketplace profile."""

    profile: str = Field(..., min_length=1, max_length=50, description="A profile code.")


class SetAsideRequest(BaseModel):
    """A warning to set aside."""

    rule: str = Field(..., min_length=1, max_length=100, description="The rule's code.")
    subject: str = Field("", max_length=100, description="The issue's subject.")
    reason: str = Field(..., min_length=1, max_length=500, description="Why it's fine.")
