"""Admin-only diagnostics: runs the no-op pipeline to check the queue and worker end to end."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import AdminUser
from app.api.schemas.test_runs import TestRunCreated, TestRunRequest, TestRunStatus
from app.api.services.test_runs import get_test_run, submit_test_run

# Hidden from the public schema. Anyone but an active admin gets 404.
router = APIRouter(prefix="/internal/test-runs", include_in_schema=False)


@router.post("", response_model=TestRunCreated, status_code=status.HTTP_201_CREATED)
def create(request: TestRunRequest, profile: AdminUser) -> dict[str, UUID]:
    """Queue a test run owned by the calling admin."""
    return submit_test_run(request.chunks, request.sleep_seconds, profile.user_id, request.fail)


@router.get("/{run_id}", response_model=TestRunStatus)
def read(run_id: UUID, profile: AdminUser) -> dict[str, Any]:
    """Return the progress of one of the calling admin's test runs."""
    return get_test_run(run_id, profile.user_id)
