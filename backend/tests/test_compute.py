"""Covers starting the worker. AWS is stubbed. The database tests need TEST_DATABASE_URL."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.compute import controllers, get_worker_controller
from app.compute.base import RETRY, RUNNING, STARTING, UNAVAILABLE
from app.compute.controllers.ec2 import Ec2WorkerController
from app.compute.starter import CAPPED, NO_WORK, WAITING, check_and_start, request_worker_start
from app.db import get_pool
from app.jobs import queue
from app.jobs.models import JOB_CONTRACT_VERSION, QueueSettings, StageSpec
from app.worker.heartbeat import Heartbeat
from app.worker.power import get_power_controller

QUEUE = QueueSettings(60, 3, 0, 0)


def _controller(state: str = "stopped") -> tuple[Ec2WorkerController, MagicMock]:
    controller = Ec2WorkerController()
    client = MagicMock()
    client.describe_instances.return_value = {
        "Reservations": [{"Instances": [{"State": {"Name": state}}]}]
    }
    controller._client = client
    return controller, client


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "StartInstances")


@pytest.fixture
def instance_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_INSTANCE_ID", "i-test")


@pytest.mark.usefixtures("instance_id")
def test_stopped_instance_is_started() -> None:
    controller, client = _controller("stopped")
    assert controller.ensure_running() == STARTING
    client.start_instances.assert_called_once_with(InstanceIds=["i-test"])


@pytest.mark.usefixtures("instance_id")
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("pending", STARTING),
        ("running", STARTING),
        # It can't start until it has fully stopped, which is the shutdown race.
        ("stopping", RETRY),
        ("shutting-down", UNAVAILABLE),
        ("terminated", UNAVAILABLE),
    ],
)
def test_other_states_do_not_call_start(state: str, expected: str) -> None:
    controller, client = _controller(state)
    assert controller.ensure_running() == expected
    client.start_instances.assert_not_called()


@pytest.mark.usefixtures("instance_id")
@pytest.mark.parametrize("code", ["InsufficientInstanceCapacity", "IncorrectInstanceState"])
def test_temporary_start_failures_retry(code: str) -> None:
    controller, client = _controller("stopped")
    client.start_instances.side_effect = _client_error(code)
    assert controller.ensure_running() == RETRY


@pytest.mark.usefixtures("instance_id")
def test_missing_instance_is_unavailable() -> None:
    controller, client = _controller()
    client.describe_instances.side_effect = _client_error("InvalidInstanceID.NotFound")
    assert controller.ensure_running() == UNAVAILABLE


@pytest.mark.usefixtures("instance_id")
def test_network_failure_retries() -> None:
    controller, client = _controller()
    client.describe_instances.side_effect = EndpointConnectionError(endpoint_url="x")
    assert controller.ensure_running() == RETRY


def test_unconfigured_instance_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_INSTANCE_ID", "")
    controller, client = _controller()
    assert controller.ensure_running() == UNAVAILABLE
    client.describe_instances.assert_not_called()


def test_controller_cannot_create_instances() -> None:
    # Duplicate instances must be impossible: this code only ever starts a known instance.
    source = Path(controllers.ec2.__file__ or "").read_text(encoding="utf-8")
    assert "run_instances" not in source


def test_noop_controller_starts_no_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_CONTROLLER", "noop")
    monkeypatch.setattr("threading.Thread.start", MagicMock(side_effect=AssertionError))
    request_worker_start()


def test_os_shutdown_requests_power_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request_file = tmp_path / "poweroff"
    monkeypatch.setenv("WORKER_POWER_REQUEST_FILE", str(request_file))
    controller = get_power_controller("os_shutdown")
    assert controller.stops_machine
    controller.power_off()
    assert request_file.exists()


def test_controllers_are_registered() -> None:
    assert get_worker_controller("ec2").name == "ec2"
    with pytest.raises(ValueError, match="Registered: ec2, noop"):
        get_worker_controller("nope")


def _sql(statement: Any, **params: Any) -> list[dict[str, Any]]:
    with get_pool().connection() as conn:
        cursor = conn.execute(statement, params)
        return cursor.fetchall() if cursor.description else []


def _submit(owner: str = "alice") -> None:
    with get_pool().connection() as conn:
        queue.create_run(conn, "noop", [StageSpec("s", "noop")], owner, None, QUEUE)


def _fake_controller() -> MagicMock:
    fake = MagicMock()
    fake.ensure_running.return_value = STARTING
    return fake


@pytest.mark.usefixtures("db")
def test_no_jobs_means_no_aws_call() -> None:
    fake = _fake_controller()
    assert check_and_start(fake) == NO_WORK
    fake.ensure_running.assert_not_called()


@pytest.mark.usefixtures("db")
def test_pending_job_starts_the_worker_and_records_it() -> None:
    _submit()
    fake = _fake_controller()
    assert check_and_start(fake) == STARTING
    fake.ensure_running.assert_called_once()
    assert _sql("SELECT state FROM worker_control") == [{"state": "starting"}]


@pytest.mark.usefixtures("db")
def test_rapid_submissions_make_one_aws_call() -> None:
    _submit("a")
    _submit("b")
    fake = _fake_controller()
    outcomes = [check_and_start(fake) for _ in range(3)]
    assert outcomes == [STARTING, WAITING, WAITING]
    fake.ensure_running.assert_called_once()


@pytest.mark.usefixtures("db")
def test_fresh_heartbeat_means_no_aws_call() -> None:
    _submit()
    _sql("INSERT INTO worker_instances (state) VALUES ('running')")
    fake = _fake_controller()
    assert check_and_start(fake) == RUNNING
    fake.ensure_running.assert_not_called()


@pytest.mark.usefixtures("db")
def test_monthly_cap_stops_new_starts() -> None:
    _submit()
    _sql(
        "INSERT INTO worker_sessions (ec2_instance_id, started_at, last_seen_at) "
        "VALUES ('i-x', now() - interval '31 hours', now())"
    )
    fake = _fake_controller()
    assert check_and_start(fake) == CAPPED
    fake.ensure_running.assert_not_called()


@pytest.mark.usefixtures("db")
def test_last_month_does_not_count_towards_the_cap() -> None:
    _submit()
    _sql(
        "INSERT INTO worker_sessions (ec2_instance_id, started_at, last_seen_at, ended_at) "
        "VALUES ('i-x', date_trunc('month', now()) - interval '40 hours', "
        "date_trunc('month', now()) - interval '1 hour', "
        "date_trunc('month', now()) - interval '1 hour')"
    )
    assert check_and_start(_fake_controller()) == STARTING


@pytest.mark.usefixtures("db")
def test_job_too_new_for_the_worker_cannot_cause_boot_loops() -> None:
    # Starting a worker that can't run the job would stop and start EC2 repeatedly.
    _submit()
    _sql(
        "UPDATE worker_control SET min_contract = 1, max_contract = %(v)s",
        v=JOB_CONTRACT_VERSION,
    )
    _sql("UPDATE jobs SET contract_version = %(v)s", v=JOB_CONTRACT_VERSION + 1)
    fake = _fake_controller()
    assert check_and_start(fake) == NO_WORK
    fake.ensure_running.assert_not_called()


@pytest.mark.usefixtures("db")
def test_processing_disabled_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESSING_ENABLED", "false")
    _submit()
    fake = _fake_controller()
    assert check_and_start(fake) == NO_WORK
    fake.ensure_running.assert_not_called()


@pytest.mark.usefixtures("db")
def test_stuck_job_ends_the_process() -> None:
    # A hung handler keeps sending heartbeats, so this limit is what stops EC2 running forever.
    stuck = MagicMock()
    _submit()
    with get_pool().connection() as conn:
        job = queue.claim_next(conn, "w", "sha", QUEUE)
        instance = conn.execute(
            "INSERT INTO worker_instances (state) VALUES ('running') RETURNING id"
        ).fetchone()
    assert job is not None
    assert instance is not None
    heartbeat = Heartbeat(instance["id"], None, 30, QUEUE, None, 0, stuck)
    heartbeat.track(job)
    heartbeat.beat()
    stuck.assert_called_once()
