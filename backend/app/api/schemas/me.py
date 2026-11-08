"""Schemas for the signed-in user's own account."""

from typing import Literal

from pydantic import BaseModel, Field


class MeResponse(BaseModel):
    """The signed-in user, their access status, and their POC limits and usage."""

    id: str = Field(..., description="User ID, from the token's sub claim.")
    email: str = Field(..., description="Account email, lowercased.")
    role: Literal["user", "admin"] = Field(..., description="Admins can run diagnostics.")
    status: Literal["active", "waitlisted"] = Field(
        ..., description="Waitlisted users can sign in but can't use the app yet."
    )
    limits: dict[str, int | None] | None = Field(
        None, description="Effective limits by name. Null means unlimited. Active users only."
    )
    usage: dict[str, int] | None = Field(
        None, description="Usage counted against each submission limit. Active users only."
    )
