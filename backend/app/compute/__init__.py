"""Making sure a worker is available. Importing this package registers the controllers."""

import app.compute.controllers  # noqa: F401
from app.compute.base import WorkerController, get_worker_controller

__all__ = ["WorkerController", "get_worker_controller"]
