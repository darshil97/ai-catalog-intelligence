"""Health check, called by UptimeRobot and Render."""

from fastapi import APIRouter

from app.api.schemas.health import HealthResponse
from app.api.services.health import get_health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> dict[str, str]:
    """Report that the API is running. Never touches the database."""
    return get_health()
