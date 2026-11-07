"""The job queue: creating, claiming and finishing jobs.

Functions take an open connection and run inside the caller's transaction, so job state and
results commit together. A claim commits immediately: ownership is held by the lease rather
than an open transaction, which is safer on Neon, where computes suspend.
"""

import hashlib
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.db import query
from app.jobs.errors import LeaseLostError
from app.jobs.models import (
    JOB_CONTRACT_VERSION,
    MIN_SUPPORTED_CONTRACT,
    ChunkSpec,
    Job,
    QueueSettings,
    RunStatus,
    StageSpec,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MAX_ERROR_LENGTH = 2000


def _idempotency_key(run_id: UUID, stage_run_id: UUID, chunk_key: str) -> str:
    return hashlib.sha256(f"{run_id}:{stage_run_id}:{chunk_key}".encode()).hexdigest()


def _insert_chunks(
    conn: Connection[Any],
    run_id: UUID,
    stage_run_id: UUID,
    owner: str,
    request_id: str | None,
    chunks: Sequence[ChunkSpec],
    settings: QueueSettings,
) -> int:
    """Insert chunks, skipping any that already exist. Returns how many were added."""
    added = 0
    for chunk in chunks:
        row = conn.execute(
            query("jobs", "insert_job"),
            {
                "run_id": run_id,
                "stage_run_id": stage_run_id,
                "owner": owner,
                "job_type": chunk.job_type,
                "payload": Jsonb(chunk.payload),
                "idempotency_key": _idempotency_key(run_id, stage_run_id, chunk.key),
                "max_attempts": settings.max_attempts,
                "contract_version": JOB_CONTRACT_VERSION,
                "request_id": request_id,
            },
        ).fetchone()
        added += 1 if row else 0
    return added


def create_run(
    conn: Connection[Any],
    pipeline: str,
    stages: Sequence[StageSpec],
    owner: str,
    request_id: str | None,
    settings: QueueSettings,
) -> UUID:
    """Create a pipeline run and queue the first job of its first stage.

    Raises:
        psycopg.errors.UniqueViolation: if the owner already has an active run.
        ValueError: if the pipeline has no stages.
    """
    if not stages:
        raise ValueError(f"Pipeline {pipeline!r} has no stages")
    row = conn.execute(
        query("jobs", "insert_run"),
        {"pipeline": pipeline, "owner": owner, "request_id": request_id},
    ).fetchone()
    assert row is not None  # noqa: S101
    run_id: UUID = row["id"]

    first_stage_id: UUID | None = None
    for position, stage in enumerate(stages):
        stage_row = conn.execute(
            query("jobs", "insert_stage"),
            {
                "run_id": run_id,
                "stage": stage.stage,
                "position": position,
                "job_type": stage.job_type,
                "payload": Jsonb(stage.payload),
            },
        ).fetchone()
        assert stage_row is not None  # noqa: S101
        first_stage_id = first_stage_id or stage_row["id"]

    assert first_stage_id is not None  # noqa: S101
    first = ChunkSpec(key="start", job_type=stages[0].job_type, payload=stages[0].payload)
    added = _insert_chunks(conn, run_id, first_stage_id, owner, request_id, [first], settings)
    conn.execute(query("jobs", "start_stage"), {"stage_run_id": first_stage_id, "added": added})
    logger.info("Run created: run_id=%s pipeline=%s owner=%s", run_id, pipeline, owner)
    return run_id


def claim_next(
    conn: Connection[Any],
    worker_id: str,
    code_sha: str,
    settings: QueueSettings,
) -> Job | None:
    """Claim the next job this code can run, or return None if there isn't one."""
    row = conn.execute(
        query("jobs", "claim"),
        {
            "worker_id": worker_id,
            "code_sha": code_sha,
            "lease_seconds": settings.lease_seconds,
            "min_contract": MIN_SUPPORTED_CONTRACT,
            "max_contract": JOB_CONTRACT_VERSION,
        },
    ).fetchone()
    if row is None:
        return None
    conn.execute(query("jobs", "mark_run_running"), {"run_id": row["pipeline_run_id"]})
    job = Job.from_row(row)
    logger.info(
        "Job claimed: job_id=%s job_type=%s attempt=%d/%d",
        job.id,
        job.job_type,
        job.attempts,
        job.max_attempts,
    )
    return job


def renew_lease(conn: Connection[Any], job: Job, settings: QueueSettings) -> bool:
    """Extend the lease. Returns False if this worker no longer owns the job."""
    result = conn.execute(
        query("jobs", "renew_lease"),
        {
            "job_id": job.id,
            "lease_token": job.lease_token,
            "lease_seconds": settings.lease_seconds,
        },
    )
    return result.rowcount == 1


def fence(conn: Connection[Any], job: Job) -> None:
    """Confirm this worker still owns the job. Call first in any transaction storing results.

    This locks the job's row, so a lease can't change hands before the transaction commits.

    Raises:
        LeaseLostError: if the job now belongs to someone else, or was cancelled.
    """
    result = conn.execute(
        query("jobs", "fence"), {"job_id": job.id, "lease_token": job.lease_token}
    )
    if result.rowcount != 1:
        raise LeaseLostError(f"Lease lost for job {job.id}")


def complete(
    conn: Connection[Any],
    job: Job,
    chunks: Sequence[ChunkSpec],
    settings: QueueSettings,
) -> None:
    """Mark a job succeeded, add any new chunks, and advance the pipeline.

    Raises:
        LeaseLostError: if this worker no longer owns the job.
    """
    fence(conn, job)
    added = _insert_chunks(
        conn,
        job.pipeline_run_id,
        job.stage_run_id,
        job.owner_user_id,
        job.request_id,
        chunks,
        settings,
    )
    if added:
        conn.execute(
            query("jobs", "add_chunks"), {"stage_run_id": job.stage_run_id, "added": added}
        )
    conn.execute(query("jobs", "mark_succeeded"), {"job_id": job.id})
    _record_chunk_result(conn, job, settings, completed=1, failed=0)
    logger.info("Job succeeded: job_id=%s chunks_added=%d", job.id, added)


def retry_or_fail(
    conn: Connection[Any],
    job: Job,
    error: BaseException,
    settings: QueueSettings,
) -> str:
    """Schedule a retry after a transient error, or fail the job if attempts are used up.

    Returns:
        The job's new status.

    Raises:
        LeaseLostError: if this worker no longer owns the job.
    """
    fence(conn, job)
    if job.attempts >= job.max_attempts:
        fail(conn, job, error, settings, fenced=True)
        return "failed"
    delay = settings.backoff_seconds(job.attempts)
    conn.execute(
        query("jobs", "mark_retry"),
        {
            "job_id": job.id,
            "delay_seconds": delay,
            "error": _error_text(error),
            "error_class": type(error).__name__,
        },
    )
    logger.warning(
        "Job will retry: job_id=%s attempt=%d/%d delay_seconds=%d error_class=%s",
        job.id,
        job.attempts,
        job.max_attempts,
        delay,
        type(error).__name__,
    )
    return "pending"


def fail(
    conn: Connection[Any],
    job: Job,
    error: BaseException,
    settings: QueueSettings,
    *,
    fenced: bool = False,
) -> None:
    """Fail a job permanently, which fails its run and cancels the run's remaining work.

    Raises:
        LeaseLostError: if this worker no longer owns the job.
    """
    if not fenced:
        fence(conn, job)
    _mark_failed(conn, job, _error_text(error), type(error).__name__, settings)


def release(conn: Connection[Any], job: Job) -> None:
    """Hand a job back unfinished, with no attempt counted, because the worker is stopping.

    Raises:
        LeaseLostError: if this worker no longer owns the job.
    """
    fence(conn, job)
    conn.execute(query("jobs", "mark_released"), {"job_id": job.id})
    logger.info("Job released: job_id=%s", job.id)


def reap_expired(conn: Connection[Any], settings: QueueSettings) -> int:
    """Recover jobs whose worker vanished. Returns how many were recovered or failed."""
    rows = conn.execute(query("jobs", "expired_jobs")).fetchall()
    for row in rows:
        job = Job.from_row(row)
        if job.attempts >= job.max_attempts:
            message = f"Lease expired on attempt {job.attempts} of {job.max_attempts}"
            _mark_failed(conn, job, message, "LeaseExpired", settings)
            logger.error("Job failed after lease expiry: job_id=%s", job.id)
        else:
            conn.execute(
                query("jobs", "mark_retry"),
                {
                    "job_id": job.id,
                    "delay_seconds": settings.backoff_seconds(job.attempts),
                    "error": "Lease expired",
                    "error_class": "LeaseExpired",
                },
            )
            logger.warning("Job recovered after lease expiry: job_id=%s", job.id)
    return len(rows)


def get_run_status(conn: Connection[Any], run_id: UUID) -> RunStatus | None:
    """Return a run's progress, or None if it doesn't exist."""
    run = conn.execute(query("jobs", "get_run"), {"run_id": run_id}).fetchone()
    if run is None:
        return None
    stages = conn.execute(query("jobs", "get_stages"), {"run_id": run_id}).fetchall()
    counts = conn.execute(query("jobs", "count_jobs_by_status"), {"run_id": run_id}).fetchall()
    return RunStatus(
        run=run,
        stages=stages,
        job_counts={row["status"]: row["count"] for row in counts},
    )


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:_MAX_ERROR_LENGTH]


def _mark_failed(
    conn: Connection[Any],
    job: Job,
    message: str,
    error_class: str,
    settings: QueueSettings,
) -> None:
    conn.execute(
        query("jobs", "mark_failed"),
        {"job_id": job.id, "error": message[:_MAX_ERROR_LENGTH], "error_class": error_class},
    )
    conn.execute(
        query("jobs", "insert_error"),
        {
            "run_id": job.pipeline_run_id,
            "job_id": job.id,
            "error_code": error_class,
            "message": message[:_MAX_ERROR_LENGTH],
        },
    )
    _record_chunk_result(conn, job, settings, completed=0, failed=1)
    logger.error("Job failed: job_id=%s error_class=%s", job.id, error_class)


def _record_chunk_result(
    conn: Connection[Any],
    job: Job,
    settings: QueueSettings,
    *,
    completed: int,
    failed: int,
) -> None:
    """Update the stage's counters, then finish the stage or run if this was the last chunk."""
    stage = conn.execute(
        query("jobs", "record_chunk_result"),
        {"stage_run_id": job.stage_run_id, "completed": completed, "failed": failed},
    ).fetchone()
    assert stage is not None  # noqa: S101
    run_id = stage["pipeline_run_id"]

    if stage["failed_chunks"] > 0:
        conn.execute(
            query("jobs", "finish_stage"), {"stage_run_id": stage["id"], "status": "failed"}
        )
        conn.execute(query("jobs", "cancel_remaining_jobs"), {"run_id": run_id})
        conn.execute(query("jobs", "cancel_remaining_stages"), {"run_id": run_id})
        conn.execute(query("jobs", "finish_run"), {"run_id": run_id, "status": "failed"})
        return

    if stage["completed_chunks"] < stage["total_chunks"]:
        return

    conn.execute(
        query("jobs", "finish_stage"), {"stage_run_id": stage["id"], "status": "succeeded"}
    )
    next_stage = conn.execute(
        query("jobs", "next_stage"), {"run_id": run_id, "position": stage["position"] + 1}
    ).fetchone()
    if next_stage is None:
        conn.execute(query("jobs", "finish_run"), {"run_id": run_id, "status": "succeeded"})
        logger.info("Run succeeded: run_id=%s", run_id)
        return

    first = ChunkSpec(key="start", job_type=next_stage["job_type"], payload=next_stage["payload"])
    added = _insert_chunks(
        conn, run_id, next_stage["id"], job.owner_user_id, job.request_id, [first], settings
    )
    conn.execute(query("jobs", "start_stage"), {"stage_run_id": next_stage["id"], "added": added})
    logger.info("Stage started: run_id=%s stage=%s", run_id, next_stage["stage"])
