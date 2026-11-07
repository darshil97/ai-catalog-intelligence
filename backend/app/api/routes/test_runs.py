"""Temporary (M0B): lets the owner run test jobs before sign-in exists. Removed in M1."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.schemas.test_runs import TestRunCreated, TestRunRequest, TestRunStatus
from app.api.services.test_runs import get_test_run, is_authorized, submit_test_run

router = APIRouter(prefix="/internal/test-runs", include_in_schema=False)


def _require_admin(token: Annotated[str | None, Header(alias="X-Admin-Token")] = None) -> None:
    """Hide these endpoints from anyone without the admin token."""
    if not is_authorized(token):
        raise HTTPException(status_code=404, detail="Not Found")


@router.post(
    "",
    response_model=TestRunCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_admin)],
)
def create(request: TestRunRequest) -> dict[str, UUID]:
    """Queue a test run."""
    return submit_test_run(request.chunks, request.sleep_seconds, request.owner, request.fail)


@router.get("/{run_id}", response_model=TestRunStatus, dependencies=[Depends(_require_admin)])
def read(run_id: UUID) -> dict[str, Any]:
    """Return a test run's progress."""
    return get_test_run(run_id)
