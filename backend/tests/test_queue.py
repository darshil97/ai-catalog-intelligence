"""Covers the job queue against real Postgres, because locking can't be tested with stubs."""

import threading
from collections import Counter
from typing import Any
from uuid import UUID

import psycopg
import pytest

from app.db import get_pool
from app.jobs import queue
from app.jobs.errors import LeaseLostError, TransientJobError
from app.jobs.models import JOB_CONTRACT_VERSION, ChunkSpec, Job, QueueSettings, StageSpec

pytestmark = pytest.mark.usefixtures("db")

SETTINGS = QueueSettings(
    lease_seconds=60, max_attempts=3, backoff_base_seconds=0, backoff_max_seconds=0
)


def _new_run(owner: str = "alice", stages: list[StageSpec] | None = None) -> UUID:
    stages = stages or [StageSpec(stage="one", job_type="noop")]
    with get_pool().connection() as conn:
        return queue.create_run(conn, "test", stages, owner, "req-1", SETTINGS)


def _claim(worker: str = "w1", settings: QueueSettings = SETTINGS) -> Job | None:
    with get_pool().connection() as conn:
        return queue.claim_next(conn, worker, "sha", settings)


def _sql(statement: Any, **params: Any) -> list[dict[str, Any]]:
    with get_pool().connection() as conn:
        cursor = conn.execute(statement, params)
        return cursor.fetchall() if cursor.description else []


def _job_status(job_id: UUID) -> str:
    return _sql("SELECT status FROM jobs WHERE id = %(id)s", id=job_id)[0]["status"]


def _run_status(run_id: UUID) -> str:
    return _sql("SELECT status FROM pipeline_runs WHERE id = %(id)s", id=run_id)[0]["status"]


def _expire_leases() -> None:
    _sql("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE status = 'running'")


def test_concurrent_workers_never_claim_the_same_job() -> None:
    # Many workers racing over few jobs is exactly when a double claim would appear.
    job_total = 30
    for index in range(job_total):
        _new_run(owner=f"owner-{index}")

    claimed: list[UUID] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def worker(name: str) -> None:
        start.wait()
        while (job := _claim(name)) is not None:
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed) == job_total
    assert max(Counter(claimed).values()) == 1


def test_claim_counts_the_attempt_and_starts_the_run() -> None:
    run_id = _new_run()
    job = _claim()
    assert job is not None
    assert job.attempts == 1
    assert job.request_id == "req-1"
    assert _run_status(run_id) == "running"


def test_stale_worker_cannot_save_after_its_lease_moved() -> None:
    _new_run()
    stale = _claim("stale")
    assert stale is not None
    _expire_leases()
    with get_pool().connection() as conn:
        queue.reap_expired(conn, SETTINGS)
    fresh = _claim("fresh")
    assert fresh is not None
    assert fresh.id == stale.id

    with get_pool().connection() as conn:
        with pytest.raises(LeaseLostError):
            queue.complete(conn, stale, [], SETTINGS)
    # The rightful owner can still finish.
    with get_pool().connection() as conn:
        queue.complete(conn, fresh, [], SETTINGS)
    assert _job_status(fresh.id) == "succeeded"


def test_stale_worker_changes_nothing_when_rejected() -> None:
    # Chunks are written after the check, so a rejected save must leave none behind.
    _new_run()
    stale = _claim("stale")
    assert stale is not None
    _sql("UPDATE jobs SET lease_token = gen_random_uuid()")
    with pytest.raises(LeaseLostError):
        with get_pool().connection() as conn:
            queue.complete(conn, stale, [ChunkSpec("c", "noop")], SETTINGS)
    assert _sql("SELECT count(*) AS n FROM jobs")[0]["n"] == 1


def test_lease_renewal_fails_once_lost() -> None:
    _new_run()
    job = _claim()
    assert job is not None
    with get_pool().connection() as conn:
        assert queue.renew_lease(conn, job, SETTINGS)
    _sql("UPDATE jobs SET lease_token = gen_random_uuid()")
    with get_pool().connection() as conn:
        assert not queue.renew_lease(conn, job, SETTINGS)


def test_transient_error_retries_then_fails_at_max_attempts() -> None:
    run_id = _new_run()
    error = TransientJobError("throttled")
    for attempt in (1, 2):
        job = _claim()
        assert job is not None
        assert job.attempts == attempt
        with get_pool().connection() as conn:
            assert queue.retry_or_fail(conn, job, error, SETTINGS) == "pending"
    job = _claim()
    assert job is not None
    assert job.attempts == 3
    with get_pool().connection() as conn:
        assert queue.retry_or_fail(conn, job, error, SETTINGS) == "failed"
    assert _run_status(run_id) == "failed"
    errors = _sql("SELECT error_code FROM processing_errors")
    assert errors == [{"error_code": "TransientJobError"}]


def test_backoff_delays_the_retry() -> None:
    _new_run()
    settings = QueueSettings(60, 3, backoff_base_seconds=3600, backoff_max_seconds=7200)
    job = _claim()
    assert job is not None
    with get_pool().connection() as conn:
        queue.retry_or_fail(conn, job, TransientJobError("x"), settings)
    assert _claim() is None


def test_backoff_doubles_up_to_the_maximum() -> None:
    settings = QueueSettings(60, 9, backoff_base_seconds=10, backoff_max_seconds=120)
    delays = [settings.backoff_seconds(attempt) for attempt in (1, 2, 3, 4, 5, 6)]
    assert delays == [10, 20, 40, 80, 120, 120]


def test_reaper_recovers_an_abandoned_job() -> None:
    # This is what happens after EC2 disappears mid-job.
    _new_run()
    job = _claim()
    assert job is not None
    _expire_leases()
    with get_pool().connection() as conn:
        assert queue.reap_expired(conn, SETTINGS) == 1
    again = _claim()
    assert again is not None
    assert again.id == job.id
    assert again.attempts == 2


def test_reaper_leaves_live_leases_alone() -> None:
    _new_run()
    job = _claim()
    assert job is not None
    with get_pool().connection() as conn:
        assert queue.reap_expired(conn, SETTINGS) == 0
    assert _job_status(job.id) == "running"


def test_reaper_fails_a_poison_job_at_max_attempts() -> None:
    # A file that crashes the process each time must stop being retried eventually.
    run_id = _new_run()
    for _ in range(3):
        assert _claim() is not None
        _expire_leases()
        with get_pool().connection() as conn:
            queue.reap_expired(conn, SETTINGS)
    assert _claim() is None
    assert _run_status(run_id) == "failed"


def test_release_returns_a_job_with_no_attempt_counted() -> None:
    _new_run()
    job = _claim()
    assert job is not None
    with get_pool().connection() as conn:
        queue.release(conn, job)
    again = _claim()
    assert again is not None
    assert again.attempts == 1


def test_chunks_fan_out_and_finish_the_run_only_when_all_are_done() -> None:
    run_id = _new_run()
    planner = _claim()
    assert planner is not None
    chunks = [ChunkSpec(f"c{i}", "noop") for i in range(3)]
    with get_pool().connection() as conn:
        queue.complete(conn, planner, chunks, SETTINGS)

    for remaining in (2, 1, 0):
        assert _run_status(run_id) == "running"
        job = _claim()
        assert job is not None
        with get_pool().connection() as conn:
            queue.complete(conn, job, [], SETTINGS)
        left = _sql("SELECT count(*) AS n FROM jobs WHERE status = 'pending'")[0]["n"]
        assert left == remaining
    assert _run_status(run_id) == "succeeded"


def test_duplicate_chunks_are_ignored() -> None:
    # A planner can run twice after a crash, and mustn't create its chunks twice.
    _new_run()
    planner = _claim()
    assert planner is not None
    duplicate = [ChunkSpec("same", "noop"), ChunkSpec("same", "noop")]
    with get_pool().connection() as conn:
        queue.complete(conn, planner, duplicate, SETTINGS)
    assert _sql("SELECT total_chunks FROM pipeline_stage_runs")[0]["total_chunks"] == 2


def test_next_stage_starts_only_after_the_previous_one_finishes() -> None:
    stages = [StageSpec("first", "noop"), StageSpec("second", "noop", {"x": 1})]
    run_id = _new_run(stages=stages)
    job = _claim()
    assert job is not None
    assert _claim() is None
    with get_pool().connection() as conn:
        queue.complete(conn, job, [], SETTINGS)
    second = _claim()
    assert second is not None
    assert second.payload == {"x": 1}
    with get_pool().connection() as conn:
        queue.complete(conn, second, [], SETTINGS)
    assert _run_status(run_id) == "succeeded"


def test_permanent_failure_cancels_the_rest_of_the_run() -> None:
    stages = [StageSpec("first", "noop"), StageSpec("second", "noop")]
    run_id = _new_run(stages=stages)
    planner = _claim()
    assert planner is not None
    with get_pool().connection() as conn:
        queue.complete(conn, planner, [ChunkSpec("a", "noop"), ChunkSpec("b", "noop")], SETTINGS)
    job = _claim()
    assert job is not None
    with get_pool().connection() as conn:
        queue.fail(conn, job, ValueError("corrupt"), SETTINGS)
    assert _run_status(run_id) == "failed"
    assert _claim() is None
    statuses = [row["status"] for row in _sql("SELECT status FROM pipeline_stage_runs")]
    assert sorted(statuses) == ["cancelled", "failed"]


def test_one_active_run_per_owner() -> None:
    _new_run("alice")
    _new_run("bob")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _new_run("alice")


def test_owner_can_start_again_after_a_run_ends() -> None:
    _new_run("alice")
    job = _claim()
    assert job is not None
    with get_pool().connection() as conn:
        queue.complete(conn, job, [], SETTINGS)
    assert _new_run("alice")


def test_job_too_new_for_this_worker_waits_instead_of_failing() -> None:
    _new_run()
    _sql("UPDATE jobs SET contract_version = %(v)s", v=JOB_CONTRACT_VERSION + 1)
    assert _claim() is None
    assert _sql("SELECT status FROM jobs")[0]["status"] == "pending"


def test_run_status_reports_progress() -> None:
    run_id = _new_run()
    with get_pool().connection() as conn:
        status = queue.get_run_status(conn, run_id)
        missing = queue.get_run_status(conn, UUID(int=0))
    assert status is not None
    assert status.job_counts == {"pending": 1}
    assert status.stages[0]["total_chunks"] == 1
    assert missing is None
