"""Temporary (M0A): measures API-to-Neon latency from Render. Remove once recorded.

Render's free tier has no shell, so this is the way to measure it from there.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException

from app.api.schemas.diagnostics import DbLatencyResponse
from app.api.services.diagnostics import is_authorized, measure_db_latency

router = APIRouter(tags=["diagnostics"], include_in_schema=False)


@router.get("/internal/db-latency", response_model=DbLatencyResponse)
def db_latency(
    token: Annotated[str | None, Header(alias="X-Diagnostics-Token")] = None,
) -> dict[str, Any]:
    """Measure connection and query time, for callers holding the diagnostics token."""
    if not is_authorized(token):
        raise HTTPException(status_code=404, detail="Not Found")
    return measure_db_latency()
