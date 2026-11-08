"""The signed-in user's own account."""

from typing import Any

from app.api.services.common import database
from app.auth import STATUS_ACTIVE, Profile
from app.usage import get_usage_summary


def get_me(profile: Profile) -> dict[str, Any]:
    """Return the caller's account. Limits and usage are included for active users only."""
    result: dict[str, Any] = {
        "id": profile.user_id,
        "email": profile.email,
        "role": profile.role,
        "status": profile.status,
    }
    if profile.status == STATUS_ACTIVE:
        with database("get_me") as conn:
            summary = get_usage_summary(conn, profile.user_id)
        result["limits"] = summary.limits
        result["usage"] = summary.used
    return result
