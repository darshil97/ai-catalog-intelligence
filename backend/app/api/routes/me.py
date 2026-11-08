"""The signed-in user's own account."""

from typing import Any

from fastapi import APIRouter

from app.api.dependencies import SignedIn
from app.api.schemas.me import MeResponse
from app.api.services.me import get_me

router = APIRouter(tags=["account"])


@router.get("/me", response_model=MeResponse, response_model_exclude_none=True)
def me(profile: SignedIn) -> dict[str, Any]:
    """Return the caller's account. A waitlisted caller gets 200 with status "waitlisted"."""
    return get_me(profile)
