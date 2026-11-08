"""Covers the worker loop against real Postgres, running the real loop in a thread.

Only the PowerController is stubbed, so nothing can actually shut down.
"""

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID

import pytest

import app.jobs.handlers  # noqa: F401
from app.db import get_pool
from app.jobs import queue
from app.jobs.models import JOB_CONTRACT_VERSION, QueueSettings, StageSpec
from app.jobs.settings import get_queue_settings
from app.worker import state
from app.worker.loop import Worker, WorkerSettings
from app.worker.power import get_power_controller
from app.worker.state import WorkerAlreadyRunningError
from conftest import ensure_user

pytestmark = pytest.mark.usefixtures("db")

QUEUE = QueueSettings(
    lease_seconds=6, max_attempts=3, backoff_base_seconds=0, backoff_max_seconds=0
)


# Generous, because from a laptop each query takes about 286 ms to reach Neon in Sydney.
WAIT_SECONDS = 60


class FakePower:
    """Records power-off calls. Never touches the machine."""

    name = "fake"
    stops_machine = True

    def __init__(self) -> None:
        self.powered_off = threading.Event()

    def power_off(self) -> None:
        self.powered_off.set()


def _settings(**overrides: Any) -> WorkerSettings:
    values: dict[str, Any] = {
        "code_sha": "test-sha",
        "ec2_instance_id": None,
        "heartbeat_seconds": 0.1,
        "poll_busy_seconds": 0.02,
        "poll_idle_seconds": 0.02,
        "busy_window_seconds": 1,
        "idle_timeout_seconds": 0.2,
        "stop_grace_seconds": 0,
        "reaper_interval_seconds": 0.1,
        "processing_enabled": True,
    }
    return WorkerSettings(**{**values, **overrides})


def _sql(statement: Any, **params: Any) -> list[dict[str, Any]]:
    with get_pool().connection() as conn:
        cursor = conn.execute(statement, params)
        return cursor.fetchall() if cursor.description else []


def _submit(owner: str = "alice", **payload: Any) -> UUID:
    stage = StageSpec("noop", "noop_plan", {"chunks": 1, "sleep_seconds": 0, **payload})
    ensure_user(owner)
    with get_pool().connection() as conn:
        return queue.create_run(conn, "noop", [stage], owner, "req", QUEUE)


def _run_status(run_id: UUID) -> str:
    return _sql("SELECT status FROM pipeline_runs WHERE id = %(id)s", id=run_id)[0]["status"]


def _wait_until(check: Callable[[], bool], timeout: float = WAIT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    pytest.fail("Condition not reached in time")


@pytest.fixture
def start_worker() -> Iterator[Callable[..., tuple[Worker, threading.Thread]]]:
    """Run workers in threads, and stop them all after the test."""
    running: list[tuple[Worker, threading.Thread]] = []

    def start(power: Any = None, **overrides: Any) -> tuple[Worker, threading.Thread]:
        worker = Worker(_settings(**overrides), QUEUE, power or get_power_controller("noop"))
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        running.append((worker, thread))
        return worker, thread

    yield start
    for worker, thread in running:
        worker.request_stop()
        thread.join(timeout=WAIT_SECONDS)


def test_processes_a_run_with_chunks_to_completion(start_worker: Any) -> None:
    run_id = _submit(chunks=3)
    start_worker()
    _wait_until(lambda: _run_status(run_id) == "succeeded")
    counts = _sql("SELECT status, count(*) AS n FROM jobs GROUP BY status")
    assert counts == [{"status": "succeeded", "n": 4}]  # The planner, plus 3 chunks.


def test_records_code_version_on_the_job(start_worker: Any) -> None:
    run_id = _submit()
    start_worker()
    _wait_until(lambda: _run_status(run_id) == "succeeded")
    assert {row["code_sha"] for row in _sql("SELECT code_sha FROM jobs")} == {"test-sha"}


def test_retries_a_transient_failure_then_succeeds(start_worker: Any) -> None:
    run_id = _submit(fail="transient_first_attempt")
    start_worker()
    _wait_until(lambda: _run_status(run_id) == "succeeded")
    attempts = _sql("SELECT attempts FROM jobs WHERE job_type = 'noop'")
    assert attempts == [{"attempts": 2}]


def test_repeated_transient_failure_fails_the_run(start_worker: Any) -> None:
    run_id = _submit(fail="transient_always")
    start_worker()
    _wait_until(lambda: _run_status(run_id) == "failed")
    assert _sql("SELECT attempts FROM jobs WHERE job_type = 'noop'") == [{"attempts": 3}]


def test_permanent_failure_is_not_retried(start_worker: Any) -> None:
    run_id = _submit(fail="permanent")
    start_worker()
    _wait_until(lambda: _run_status(run_id) == "failed")
    assert _sql("SELECT attempts FROM jobs WHERE job_type = 'noop'") == [{"attempts": 1}]


def test_unknown_job_type_fails_visibly(start_worker: Any) -> None:
    # Bad data must fail the run, not crash or stall the worker.
    ensure_user("a")
    with get_pool().connection() as conn:
        run_id = queue.create_run(conn, "x", [StageSpec("s", "no_such_job")], "a", None, QUEUE)
    start_worker()
    _wait_until(lambda: _run_status(run_id) == "failed")
    assert _sql("SELECT error_code FROM processing_errors") == [{"error_code": "ValueError"}]


def test_heartbeat_keeps_a_long_job_alive(start_worker: Any) -> None:
    # A job outlasting its 6-second lease must still hold a valid one, or a second worker or a
    # restart would take it away.
    run_id = _submit(sleep_seconds=14)
    start_worker()
    _wait_until(
        lambda: bool(_sql("SELECT 1 FROM jobs WHERE job_type = 'noop' AND status = 'running'"))
    )
    time.sleep(7)
    still_leased = _sql(
        "SELECT lease_expires_at > now() AS valid FROM jobs WHERE job_type = 'noop'"
    )
    assert still_leased == [{"valid": True}]
    _wait_until(lambda: _run_status(run_id) == "succeeded")
    assert _sql("SELECT attempts FROM jobs WHERE job_type = 'noop'") == [{"attempts": 1}]


def test_graceful_stop_releases_the_job_with_no_attempt_counted(start_worker: Any) -> None:
    run_id = _submit(sleep_seconds=60)
    worker, thread = start_worker()
    _wait_until(
        lambda: bool(_sql("SELECT 1 FROM jobs WHERE job_type = 'noop' AND status = 'running'"))
    )
    worker.request_stop()
    thread.join(timeout=WAIT_SECONDS)
    assert not thread.is_alive()
    job = _sql("SELECT status, attempts FROM jobs WHERE job_type = 'noop'")[0]
    assert job == {"status": "pending", "attempts": 0}
    assert _run_status(run_id) == "running"


def test_job_finishes_if_it_fits_in_the_grace_period(start_worker: Any) -> None:
    run_id = _submit(sleep_seconds=6)
    worker, thread = start_worker(stop_grace_seconds=WAIT_SECONDS)
    _wait_until(
        lambda: bool(_sql("SELECT 1 FROM jobs WHERE job_type = 'noop' AND status = 'running'"))
    )
    worker.request_stop()
    thread.join(timeout=WAIT_SECONDS)
    assert _sql("SELECT status FROM jobs WHERE job_type = 'noop'") == [{"status": "succeeded"}]
    assert _run_status(run_id) == "succeeded"


def test_lost_lease_abandons_the_job_without_saving(start_worker: Any) -> None:
    # Simulates a network split: another worker has taken this job over.
    run_id = _submit(sleep_seconds=60)
    start_worker()
    _wait_until(
        lambda: bool(_sql("SELECT 1 FROM jobs WHERE job_type = 'noop' AND status = 'running'"))
    )
    other = "00000000-0000-0000-0000-000000000001"
    _sql("UPDATE jobs SET lease_token = %(t)s WHERE job_type = 'noop'", t=other)
    time.sleep(3)
    job = _sql("SELECT status, lease_token FROM jobs WHERE job_type = 'noop'")[0]
    assert job["status"] == "running"
    assert str(job["lease_token"]) == other
    assert _run_status(run_id) == "running"


def test_recovers_a_job_abandoned_by_a_dead_worker(start_worker: Any) -> None:
    # This is the EC2-died-mid-job case: a running job whose lease has run out.
    run_id = _submit()
    _sql(
        "UPDATE jobs SET status = 'running', attempts = 1, lease_token = gen_random_uuid(), "
        "lease_expires_at = now() - interval '1 second'"
    )
    start_worker()
    _wait_until(lambda: _run_status(run_id) == "succeeded")


def test_idle_worker_powers_off_when_nothing_is_left(start_worker: Any) -> None:
    power = FakePower()
    _, thread = start_worker(power=power)
    assert power.powered_off.wait(WAIT_SECONDS)
    thread.join(timeout=WAIT_SECONDS)
    assert _sql("SELECT state FROM worker_control") == [{"state": "shutting_down"}]


def test_noop_power_never_shuts_down(start_worker: Any) -> None:
    # A local worker must keep polling, and can never power off a laptop.
    _, thread = start_worker()
    time.sleep(0.6)
    assert thread.is_alive()
    assert _sql("SELECT state FROM worker_control") == [{"state": "running"}]


def test_does_not_power_off_while_a_job_is_running(start_worker: Any) -> None:
    power = FakePower()
    run_id = _submit(sleep_seconds=6)
    start_worker(power=power)
    _wait_until(
        lambda: bool(_sql("SELECT 1 FROM jobs WHERE job_type = 'noop' AND status = 'running'"))
    )
    assert not power.powered_off.wait(0.4)
    assert _run_status(run_id) == "running"
    assert power.powered_off.wait(WAIT_SECONDS)
    assert _run_status(run_id) == "succeeded"


def test_new_job_while_idle_is_picked_up_instead_of_shutting_down(start_worker: Any) -> None:
    start_worker(power=(power := FakePower()), idle_timeout_seconds=WAIT_SECONDS)
    time.sleep(0.2)
    run_id = _submit()
    _wait_until(lambda: _run_status(run_id) == "succeeded")
    assert not power.powered_off.is_set()


def test_shutdown_check_refuses_when_work_is_outstanding() -> None:
    _submit()
    with get_pool().connection() as conn:
        assert not state.try_begin_shutdown(conn)
    assert _sql("SELECT state FROM worker_control") == [{"state": "stopped"}]


def test_job_arriving_during_shutdown_is_kept_for_the_next_worker(start_worker: Any) -> None:
    # The shutdown race: a job commits right after the check said nothing was left.
    with get_pool().connection() as conn:
        assert state.try_begin_shutdown(conn)
    run_id = _submit()
    assert _sql("SELECT state FROM worker_control") == [{"state": "shutting_down"}]
    start_worker()
    _wait_until(lambda: _run_status(run_id) == "succeeded")


def test_job_too_new_for_this_worker_cannot_keep_the_machine_on(start_worker: Any) -> None:
    # Otherwise EC2 would run forever, waiting for a job it can't process.
    run_id = _submit()
    _sql("UPDATE jobs SET contract_version = %(v)s", v=JOB_CONTRACT_VERSION + 1)
    power = FakePower()
    start_worker(power=power)
    assert power.powered_off.wait(WAIT_SECONDS)
    assert _run_status(run_id) == "queued"


def test_pause_stops_claims_and_shutdown(start_worker: Any) -> None:
    _sql(
        "UPDATE worker_control SET pause_reason = 'deploy', "
        "pause_expires_at = now() + interval '1 hour'"
    )
    run_id = _submit()
    power = FakePower()
    start_worker(power=power)
    _wait_until(lambda: _sql("SELECT state FROM worker_control") == [{"state": "paused"}])
    assert not power.powered_off.wait(0.5)
    assert _run_status(run_id) == "queued"


def test_clearing_a_pause_resumes_work(start_worker: Any) -> None:
    _sql(
        "UPDATE worker_control SET pause_reason = 'deploy', "
        "pause_expires_at = now() + interval '1 hour'"
    )
    run_id = _submit()
    start_worker()
    _wait_until(lambda: _sql("SELECT state FROM worker_control") == [{"state": "paused"}])
    _sql("UPDATE worker_control SET pause_reason = NULL, pause_expires_at = NULL")
    _wait_until(lambda: _run_status(run_id) == "succeeded")


def test_expired_pause_cannot_strand_the_worker(start_worker: Any) -> None:
    # A crashed deploy workflow leaves its pause behind, and it must stop counting.
    _sql(
        "UPDATE worker_control SET pause_reason = 'deploy', "
        "pause_expires_at = now() - interval '1 second'"
    )
    run_id = _submit()
    start_worker()
    _wait_until(lambda: _run_status(run_id) == "succeeded")


def test_processing_disabled_stops_claims_but_allows_shutdown(start_worker: Any) -> None:
    # An emergency stop must not leave EC2 running because jobs are waiting.
    run_id = _submit()
    power = FakePower()
    start_worker(power=power, processing_enabled=False)
    assert power.powered_off.wait(WAIT_SECONDS)
    assert _run_status(run_id) == "queued"


def test_only_one_worker_can_register() -> None:
    with get_pool().connection() as conn:
        state.register(conn, "sha", None, stale_after_seconds=60)
    with pytest.raises(WorkerAlreadyRunningError):
        with get_pool().connection() as conn:
            state.register(conn, "sha", None, stale_after_seconds=60)


def test_a_dead_workers_old_heartbeat_does_not_block_a_new_one() -> None:
    with get_pool().connection() as conn:
        state.register(conn, "old", None, stale_after_seconds=60)
    _sql("UPDATE worker_instances SET last_heartbeat_at = now() - interval '1 hour'")
    with get_pool().connection() as conn:
        state.register(conn, "new", None, stale_after_seconds=60)
    states = _sql("SELECT code_sha, state FROM worker_instances ORDER BY code_sha")
    assert states == [
        {"code_sha": "new", "state": "running"},
        {"code_sha": "old", "state": "stopped"},
    ]


def test_ec2_sessions_are_recorded_for_the_hour_cap() -> None:
    with get_pool().connection() as conn:
        state.register(conn, "sha", "i-abc", stale_after_seconds=60)
    rows = _sql("SELECT ec2_instance_id, ended_at FROM worker_sessions")
    assert rows == [{"ec2_instance_id": "i-abc", "ended_at": None}]


def test_local_runs_create_no_sessions() -> None:
    with get_pool().connection() as conn:
        state.register(conn, "sha", None, stale_after_seconds=60)
    assert _sql("SELECT * FROM worker_sessions") == []


def test_clean_stop_records_stopped_state(start_worker: Any) -> None:
    worker, thread = start_worker()
    _wait_until(lambda: _sql("SELECT state FROM worker_control") == [{"state": "running"}])
    worker.request_stop()
    thread.join(timeout=WAIT_SECONDS)
    assert _sql("SELECT state FROM worker_control") == [{"state": "stopped"}]
    assert _sql("SELECT state FROM worker_instances") == [{"state": "stopped"}]


def test_unsafe_backoff_config_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_config

    get_config()["queue"]["backoff_max_seconds"] = 300
    try:
        with pytest.raises(ValueError, match="backoff_max_seconds"):
            get_queue_settings()
    finally:
        get_config.cache_clear()
