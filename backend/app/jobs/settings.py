"""Builds queue settings from config, and checks they are safe together."""

from app.config import get_queue_config, get_worker_config
from app.jobs.models import QueueSettings


def get_queue_settings() -> QueueSettings:
    """Return the queue settings.

    Raises:
        ValueError: if a retry could wait longer than the worker stays awake when idle, which
            would strand the job on a stopped machine.
    """
    config = get_queue_config()
    idle_timeout = float(get_worker_config()["idle_timeout_seconds"])
    if config["backoff_max_seconds"] >= idle_timeout:
        raise ValueError(
            f"queue.backoff_max_seconds ({config['backoff_max_seconds']}) must be below "
            f"worker.idle_timeout_seconds ({idle_timeout})"
        )
    return QueueSettings(
        lease_seconds=config["lease_seconds"],
        max_attempts=config["max_attempts"],
        backoff_base_seconds=config["backoff_base_seconds"],
        backoff_max_seconds=config["backoff_max_seconds"],
    )
