"""Temporary (M0B): submits and reads test runs. Removed in M1."""

import hmac
from typing import Any
from uuid import UUID

import psycopg
import psycopg_pool
from fastapi import HTTPException

from app.compute import get_worker_controller
from app.config import (
    get_admin_config,
    get_compute_config,
    get_pipeline_config,
    get_processing_config,
)
from app.db import DatabaseNotConfiguredError, get_pool
from app.jobs import queue
from app.jobs.models import StageSpec
from app.jobs.settings import get_queue_settings
from app.utils.logger import get_logger
from app.utils.request_id import get_request_id

logger = get_logger(__name__)

_DATABASE_ERRORS = (DatabaseNotConfiguredError, psycopg.OperationalError, psycopg_pool.PoolTimeout)


def is_authorized(token: str | None) -> bool:
    """Check the admin token. With no token configured, nothing is authorized."""
    expected = get_admin_config()["token"]
    if not expected or token is None:
        return False
    return hmac.compare_digest(token.encode(), expected.encode())


def submit_test_run(
    chunks: int, sleep_seconds: float, owner: str, fail: str | None
) -> dict[str, UUID]:
    """Queue a test run, then make sure a worker is available.

    The job commits first, so a failed worker start can't lose it.

    Raises:
        HTTPException: 503 if processing is off or the database is down, or 409 if the owner
            already has an active run.
    """
    if not get_processing_config()["enabled"]:
        logger.warning("Test run refused: reason=processing_disabled owner=%s", owner)
        raise HTTPException(status_code=503, detail="Processing is turned off")

    payload = {"chunks": chunks, "sleep_seconds": sleep_seconds, "fail": fail}
    stages = [
        StageSpec(stage=stage["stage"], job_type=stage["job_type"], payload=payload)
        for stage in get_pipeline_config("noop")
    ]
    try:
        with get_pool().connection() as conn:
            run_id = queue.create_run(
                conn, "noop", stages, owner, get_request_id(), get_queue_settings()
            )
    except psycopg.errors.UniqueViolation as exc:
        logger.info("Test run refused: reason=active_run_exists owner=%s", owner)
        raise HTTPException(status_code=409, detail="This owner has an active run") from exc
    except _DATABASE_ERRORS as exc:
        logger.error("Test run failed: error=%s", type(exc).__name__, exc_info=True)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    try:
        get_worker_controller(get_compute_config()["worker_controller"]).ensure_running()
    except Exception:  # noqa: BLE001
        # The job is safely queued, and the next check will try starting the worker again.
        logger.error("Worker start failed: run_id=%s", run_id, exc_info=True)
    return {"run_id": run_id}


def get_test_run(run_id: UUID) -> dict[str, Any]:
    """Return a run's progress.

    Raises:
        HTTPException: 404 if the run doesn't exist, or 503 if the database is down.
    """
    try:
        with get_pool().connection() as conn:
            status = queue.get_run_status(conn, run_id)
    except _DATABASE_ERRORS as exc:
        logger.error("Test run lookup failed: error=%s", type(exc).__name__, exc_info=True)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if status is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "run_id": status.run["id"],
        "status": status.run["status"],
        "created_at": status.run["created_at"],
        "started_at": status.run["started_at"],
        "finished_at": status.run["finished_at"],
        "stages": status.stages,
        "job_counts": status.job_counts,
    }
