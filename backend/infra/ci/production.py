"""Production steps run by GitHub Actions: migrations, and the safe worker deploy.

Credentials come from the environment (AWS keys) and Parameter Store (database URL), and are
never printed. Run from backend/:

    python infra/ci/production.py migrate
    python infra/ci/production.py deploy-worker --sha <commit>
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

import boto3
import psycopg

DATABASE_URL_PARAMETER = "/catalog/prod/ci/database_url_direct"
DEPLOY_DOCUMENT = "catalog-worker-deploy"
PAUSE_MINUTES = 30
DRAIN_TIMEOUT_SECONDS = 20 * 60
ONLINE_TIMEOUT_SECONDS = 10 * 60
COMMAND_TIMEOUT_SECONDS = 15 * 60


def log(message: str) -> None:
    """Print a progress line for the Actions log."""
    print(f"{datetime.now(UTC):%H:%M:%S} {message}", flush=True)  # noqa: T201


def database_url(region: str) -> str:
    """Read the production database URL from Parameter Store."""
    response = boto3.client("ssm", region_name=region).get_parameter(
        Name=DATABASE_URL_PARAMETER, WithDecryption=True
    )
    url: str = response["Parameter"]["Value"].strip()
    return url


def migrate(region: str) -> None:
    """Apply any new migrations to production. Does nothing when there are none."""
    url = database_url(region)
    env = {**os.environ, "DATABASE_URL": url, "DATABASE_URL_DIRECT": url}
    for args in (["upgrade", "head"], ["current"]):
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "alembic", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        # Alembic's own output names revisions only, never the URL.
        output = (result.stdout + result.stderr).strip()
        log(f"alembic {' '.join(args)}: exit={result.returncode} {output[-500:]}")
        if result.returncode != 0:
            sys.exit(1)


def is_drained(state: str | None, running_jobs: int, fresh_heartbeats: int) -> bool:
    """Decide whether the worker can be deployed without interrupting a job.

    True when no job is running and the worker has either acknowledged the pause or isn't
    alive at all, such as straight after a boot.
    """
    if running_jobs > 0:
        return False
    return state == "paused" or fresh_heartbeats == 0


def _drain_status(conn: psycopg.Connection[Any]) -> tuple[str | None, int, int]:
    row = conn.execute(
        "SELECT (SELECT state FROM worker_control), "
        "(SELECT count(*) FROM jobs WHERE status = 'running'), "
        "(SELECT count(*) FROM worker_instances WHERE state = 'running' "
        " AND last_heartbeat_at > now() - interval '90 seconds')"
    ).fetchone()
    assert row is not None  # noqa: S101
    return row[0], row[1], row[2]


def _start_instance(ec2: Any, instance_id: str) -> datetime:
    """Start the instance if it's stopped, waiting out a stop in progress. Returns the time."""
    started_at = datetime.now(UTC)
    deadline = time.monotonic() + ONLINE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        response = ec2.describe_instances(InstanceIds=[instance_id])
        state = response["Reservations"][0]["Instances"][0]["State"]["Name"]
        if state in ("running", "pending"):
            log(f"Instance is {state}")
            return started_at
        if state == "stopped":
            ec2.start_instances(InstanceIds=[instance_id])
            log("Instance start requested")
            return datetime.now(UTC)
        log(f"Instance is {state}, waiting")
        time.sleep(10)
    raise TimeoutError("Instance didn't become startable")


def _wait_online(ssm: Any, instance_id: str, since: datetime) -> None:
    deadline = time.monotonic() + ONLINE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        info = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )["InstanceInformationList"]
        # A ping from before this boot doesn't count.
        if info and info[0]["PingStatus"] == "Online" and info[0]["LastPingDateTime"] >= since:
            log("Systems Manager agent is online")
            return
        time.sleep(10)
    raise TimeoutError("Instance didn't come online in Systems Manager")


def _wait_drained(conn: psycopg.Connection[Any]) -> None:
    deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        state, running, fresh = _drain_status(conn)
        if is_drained(state, running, fresh):
            log(f"Worker drained: state={state} running_jobs={running}")
            return
        log(f"Waiting for the worker to drain: state={state} running_jobs={running}")
        time.sleep(15)
    raise TimeoutError("Worker didn't drain in time. Nothing was changed, so re-run later.")


def _run_deploy(ssm: Any, instance_id: str, sha: str) -> bool:
    command = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName=DEPLOY_DOCUMENT,
        Parameters={"Sha": [sha]},
        Comment=f"Deploy {sha[:7]} from GitHub Actions",
    )["Command"]["CommandId"]
    log(f"Deploy command sent: command_id={command}")
    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(10)
        try:
            result = ssm.get_command_invocation(CommandId=command, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if result["Status"] in ("Pending", "InProgress", "Delayed"):
            continue
        log("Instance output:\n" + result.get("StandardOutputContent", "")[-4000:])
        if result.get("StandardErrorContent"):
            log("Instance errors:\n" + result["StandardErrorContent"][-2000:])
        return bool(result["Status"] == "Success")
    raise TimeoutError("Deploy command didn't finish in time")


def deploy_worker(region: str, instance_id: str, sha: str) -> None:
    """Pause the worker, wait for its current job, deploy the commit, and unpause.

    The worker stops EC2 by itself after it has been idle, so this never stops it.
    """
    ec2 = boto3.client("ec2", region_name=region)
    ssm = boto3.client("ssm", region_name=region)
    with psycopg.connect(database_url(region), autocommit=True, prepare_threshold=None) as conn:
        conn.execute(
            "UPDATE worker_control SET pause_reason = 'deploy', "
            "pause_expires_at = now() + make_interval(mins => %s) WHERE id = 1",
            (PAUSE_MINUTES,),
        )
        log("Worker paused for deploy")
        try:
            since = _start_instance(ec2, instance_id)
            _wait_online(ssm, instance_id, since)
            _wait_drained(conn)
            ok = _run_deploy(ssm, instance_id, sha)
        finally:
            conn.execute(
                "UPDATE worker_control SET pause_reason = NULL, pause_expires_at = NULL "
                "WHERE id = 1 AND pause_reason = 'deploy'"
            )
            log("Worker unpaused")
    if not ok:
        log("Deploy failed. The instance rolled back to the previous version.")
        sys.exit(1)
    log(f"Worker deployed: sha={sha}")


def main() -> None:
    """Run the requested step."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="step", required=True)
    sub.add_parser("migrate")
    deploy = sub.add_parser("deploy-worker")
    deploy.add_argument("--sha", required=True)
    args = parser.parse_args()
    region = os.environ["AWS_REGION"]
    if args.step == "migrate":
        migrate(region)
    else:
        deploy_worker(region, os.environ["WORKER_INSTANCE_ID"], args.sha)


if __name__ == "__main__":
    main()
