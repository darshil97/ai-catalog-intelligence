"""Does nothing, because locally you start worker.py yourself."""

from app.compute.base import RUNNING, register_worker_controller


class NoopWorkerController:
    """Assumes a worker is already running."""

    name = "noop"

    def ensure_running(self) -> str:
        """Do nothing, and report the worker as running."""
        return RUNNING


register_worker_controller(NoopWorkerController())
