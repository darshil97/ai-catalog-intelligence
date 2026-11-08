"""Decides when to start the worker, keeping AWS calls and costs to a minimum.

There's no always-on scheduler, so a short-lived background thread keeps checking until the
worker is up. Submitting a job, reading its status, and starting the API each trigger it.
"""

import threading
import time

import psycopg
import psycopg_pool

from app.compute.base import RUNNING, STARTING, UNAVAILABLE, WorkerController, get_worker_controller
from app.config import get_compute_config, get_processing_config
from app.db import DatabaseNotConfiguredError, get_pool, query
from app.utils.logger import get_logger

logger = get_logger(__name__)

NO_WORK = "no_work"
CAPPED = "capped"
WAITING = "waiting"
# After these, further checks in this thread can't change anything.
_SETTLED = {NO_WORK, RUNNING, CAPPED, UNAVAILABLE}

_thread_lock = threading.Lock()
_thread: threading.Thread | None = None


def check_and_start(controller: WorkerController) -> str:
    """Start the worker if there's work and nothing is already handling it.

    Each check costs little: AWS is only called with claimable work, no fresh heartbeat, hours
    left in the month, and the cooldown free, so concurrent submissions make one call.

    Returns:
        NO_WORK, RUNNING, CAPPED, WAITING, or the controller's status.
    """
    config = get_compute_config()
    if not get_processing_config()["enabled"]:
        return NO_WORK
    with get_pool().connection() as conn:
        pending = conn.execute(query("worker", "claimable_pending")).fetchone()
        if not pending or pending["count"] == 0:
            return NO_WORK
        live = conn.execute(
            query("worker", "other_live_workers"),
            {"stale_after_seconds": int(config["heartbeat_fresh_seconds"])},
        ).fetchone()
        if live and live["count"] > 0:
            return RUNNING
        used = conn.execute(query("worker", "ec2_seconds_this_month")).fetchone()
        cap_seconds = float(config["max_ec2_hours_per_month"]) * 3600
        if used and float(used["seconds"]) >= cap_seconds:
            logger.warning(
                "Worker start refused: reason=monthly_cap used_seconds=%.0f cap_seconds=%.0f",
                used["seconds"],
                cap_seconds,
            )
            return CAPPED
        taken = conn.execute(
            query("worker", "take_start_cooldown"),
            {"cooldown_seconds": int(config["start_cooldown_seconds"])},
        ).fetchone()
        if taken is None:
            return WAITING

    status = controller.ensure_running()
    if status in (STARTING, UNAVAILABLE):
        state = "starting" if status == STARTING else "unavailable"
        with get_pool().connection() as conn:
            conn.execute(query("worker", "set_state"), {"state": state})
    return status


def _keep_checking(controller: WorkerController) -> None:
    config = get_compute_config()
    for _ in range(int(config["start_retry_max_attempts"])):
        try:
            outcome = check_and_start(controller)
        except (DatabaseNotConfiguredError, psycopg.Error, psycopg_pool.PoolTimeout) as exc:
            logger.warning("Worker check failed: error=%s", type(exc).__name__)
            outcome = WAITING
        if outcome in _SETTLED:
            logger.info("Worker check finished: outcome=%s", outcome)
            return
        time.sleep(float(config["start_retry_seconds"]))
    logger.warning("Worker check gave up: reason=attempts_used")


def request_worker_start() -> None:
    """Make sure a worker gets started if needed, without blocking the caller.

    Locally the noop controller needs nothing, so no thread or database access happens.
    """
    global _thread
    controller = get_worker_controller(get_compute_config()["worker_controller"])
    if controller.name == "noop":
        return
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(
            target=_keep_checking, args=(controller,), name="worker-start", daemon=True
        )
        _thread.start()
