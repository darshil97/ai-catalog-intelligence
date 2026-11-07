"""The worker's records in the database: registration, heartbeat, pause and shutdown checks."""

from typing import Any
from uuid import UUID

from psycopg import Connection

from app.db import query
from app.jobs.models import JOB_CONTRACT_VERSION, MIN_SUPPORTED_CONTRACT


class WorkerAlreadyRunningError(RuntimeError):
    """Another worker has a recent heartbeat, and only one may run."""


def register(
    conn: Connection[Any],
    code_sha: str,
    ec2_instance_id: str | None,
    stale_after_seconds: int,
) -> UUID:
    """Register this worker as the only one running.

    Uses a row lock instead of an advisory lock, because session features conflict with the
    connection pooler.

    Raises:
        WorkerAlreadyRunningError: if another worker has a recent heartbeat.
    """
    conn.execute(query("worker", "lock_control"))
    row = conn.execute(
        query("worker", "other_live_workers"), {"stale_after_seconds": stale_after_seconds}
    ).fetchone()
    assert row is not None  # noqa: S101
    if row["count"] > 0:
        raise WorkerAlreadyRunningError("Another worker has a recent heartbeat")

    conn.execute(query("worker", "stop_stale_instances"))
    instance = conn.execute(
        query("worker", "insert_instance"),
        {"ec2_instance_id": ec2_instance_id, "code_sha": code_sha},
    ).fetchone()
    assert instance is not None  # noqa: S101
    if ec2_instance_id:
        # A previous session may have ended without saying so, such as after a crash.
        conn.execute(query("worker", "end_session"), {"ec2_instance_id": ec2_instance_id})
        conn.execute(query("worker", "insert_session"), {"ec2_instance_id": ec2_instance_id})
    conn.execute(
        query("worker", "set_running"),
        {"min_contract": MIN_SUPPORTED_CONTRACT, "max_contract": JOB_CONTRACT_VERSION},
    )
    instance_id: UUID = instance["id"]
    return instance_id


def heartbeat(conn: Connection[Any], instance_id: UUID, ec2_instance_id: str | None) -> None:
    """Record that this worker is alive."""
    conn.execute(query("worker", "instance_heartbeat"), {"instance_id": instance_id})
    if ec2_instance_id:
        conn.execute(query("worker", "session_heartbeat"), {"ec2_instance_id": ec2_instance_id})


def is_paused(conn: Connection[Any]) -> bool:
    """Return whether a pause is active. An expired pause doesn't count."""
    row = conn.execute(query("worker", "pause_state")).fetchone()
    return bool(row and row["paused"])


def set_state(conn: Connection[Any], state: str) -> None:
    """Record the worker's state for the API and deploys to read."""
    conn.execute(query("worker", "set_state"), {"state": state})


def try_begin_shutdown(conn: Connection[Any], *, count_pending: bool = True) -> bool:
    """Decide whether the worker may shut down, and record it if so.

    Runs under a lock on worker_control. A job committed after this check isn't lost: the API
    sees 'shutting_down' or the instance stopping, and starts it again.

    Args:
        conn: an open connection, in the caller's transaction.
        count_pending: False when processing is disabled, so waiting jobs can't keep the
            machine running during an emergency stop.

    Returns:
        True if the worker should power off now.
    """
    control = conn.execute(query("worker", "lock_control")).fetchone()
    assert control is not None  # noqa: S101
    if control["paused"]:
        return False
    row = conn.execute(
        query("worker", "outstanding_jobs"),
        {
            "count_pending": count_pending,
            "min_contract": MIN_SUPPORTED_CONTRACT,
            "max_contract": JOB_CONTRACT_VERSION,
        },
    ).fetchone()
    assert row is not None  # noqa: S101
    if row["count"] > 0:
        return False
    set_state(conn, "shutting_down")
    return True


def mark_stopped(
    conn: Connection[Any],
    instance_id: UUID,
    ec2_instance_id: str | None,
    *,
    keep_shutting_down: bool,
) -> None:
    """Record that this worker has stopped."""
    conn.execute(query("worker", "stop_instance"), {"instance_id": instance_id})
    if ec2_instance_id:
        conn.execute(query("worker", "end_session"), {"ec2_instance_id": ec2_instance_id})
    if not keep_shutting_down:
        set_state(conn, "stopped")
