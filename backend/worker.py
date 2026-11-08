"""Worker entry point. The same file runs on a laptop and on EC2."""

import signal
import sys
import types
from urllib.parse import urlparse

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
    if get_worker_config()["production_db_endpoint"] in host:
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


def main() -> None:
    """Start the worker, and stop it gracefully on SIGTERM or SIGINT."""
    configure_logging()
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
