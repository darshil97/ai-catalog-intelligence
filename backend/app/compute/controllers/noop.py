"""Does nothing, because locally you start worker.py yourself."""

from app.compute.base import register_worker_controller


class NoopWorkerController:
    """Assumes a worker is already running."""

    name = "noop"

    def ensure_running(self) -> None:
        """Do nothing."""


register_worker_controller(NoopWorkerController())
