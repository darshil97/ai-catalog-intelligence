"""Worker entry point. The same file runs on a laptop and on EC2."""

import argparse
import os
import signal
import sys
import types
from urllib.parse import urlparse

from dotenv import dotenv_values

import app.jobs.handlers  # noqa: F401
from app.config import (
    get_app_config,
    get_database_config,
    get_processing_config,
    get_worker_config,
)
from app.db import close_pool
from app.jobs.settings import get_queue_settings
from app.utils.logger import configure_logging, get_logger
from app.worker.loop import Worker, WorkerSettings
from app.worker.power import get_power_controller
from app.worker.selfcheck import self_check, wait_for_version
from app.worker.state import WorkerAlreadyRunningError

logger = get_logger(__name__)


def _refuse_production_database_locally() -> None:
    """Stop a local worker from ever processing the production queue.

    Raises:
        SystemExit: if a local worker points at the production database.
    """
    if get_app_config()["environment"] != "local":
        return
    host = urlparse(get_database_config()["url"]).hostname or ""
    endpoint = get_worker_config()["production_db_endpoint"]
    if endpoint and endpoint in host:
        logger.error("Refusing to start: a local worker can't use the production database")
        sys.exit(2)


def _build_worker() -> Worker:
    config = get_worker_config()
    settings = WorkerSettings(
        code_sha=config["code_sha"],
        ec2_instance_id=config["ec2_instance_id"] or None,
        heartbeat_seconds=float(config["heartbeat_seconds"]),
        poll_busy_seconds=float(config["poll_busy_seconds"]),
        poll_idle_seconds=float(config["poll_idle_seconds"]),
        busy_window_seconds=float(config["busy_window_seconds"]),
        idle_timeout_seconds=float(config["idle_timeout_seconds"]),
        stop_grace_seconds=float(config["stop_grace_seconds"]),
        reaper_interval_seconds=float(config["reaper_interval_seconds"]),
        processing_enabled=get_processing_config()["enabled"],
        heartbeat_file=config["heartbeat_file"] or None,
        max_job_seconds=float(config["max_job_seconds"]),
    )
    return Worker(settings, get_queue_settings(), get_power_controller(config["power_controller"]))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Catalog processing worker")
    parser.add_argument("--env-file", help="Load environment variables from this file first.")
    parser.add_argument(
        "--self-check", action="store_true", help="Check this code can run, then exit."
    )
    parser.add_argument(
        "--wait-for-version", metavar="SHA", help="Wait for a running worker on this commit."
    )
    parser.add_argument("--timeout", type=float, default=180, help="Seconds to wait.")
    return parser.parse_args()


def _load_env_file(path: str) -> None:
    """Load KEY=value lines without shell parsing, because URLs contain '&'."""
    for key, value in dotenv_values(path).items():
        if value is not None:
            os.environ[key] = value


def main() -> None:
    """Start the worker, and stop it gracefully on SIGTERM or SIGINT.

    With --self-check or --wait-for-version, run that deploy check and exit instead.
    """
    args = _parse_args()
    if args.env_file:
        _load_env_file(args.env_file)
    configure_logging()
    if args.self_check:
        problems = self_check()
        for problem in problems:
            logger.error("Self-check failed: problem=%s", problem)
        logger.info("Self-check finished: problems=%d", len(problems))
        sys.exit(1 if problems else 0)
    if args.wait_for_version:
        found = wait_for_version(args.wait_for_version, args.timeout)
        logger.info("Version check finished: sha=%s running=%s", args.wait_for_version, found)
        sys.exit(0 if found else 1)

    _refuse_production_database_locally()
    worker = _build_worker()

    def _handle_signal(signum: int, _frame: types.FrameType | None) -> None:
        logger.info("Signal received: signal=%s", signal.Signals(signum).name)
        worker.request_stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        worker.run()
    except WorkerAlreadyRunningError as exc:
        logger.error("Worker not started: reason=%s", exc)
        sys.exit(3)
    finally:
        close_pool()
    logger.info("Worker stopped")


if __name__ == "__main__":
    main()
