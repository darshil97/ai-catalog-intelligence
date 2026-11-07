"""The WorkerController contract and registry."""

from typing import Protocol

from app.utils.logger import get_logger

logger = get_logger(__name__)


class WorkerController(Protocol):
    """Makes sure a worker is running to pick up queued jobs."""

    name: str

    def ensure_running(self) -> None:
        """Start the worker if needed. Failing here must not lose queued work."""
        ...


_REGISTRY: dict[str, WorkerController] = {}


def register_worker_controller(controller: WorkerController) -> WorkerController:
    """Add a controller to the registry under its name."""
    _REGISTRY[controller.name] = controller
    logger.info("Registered worker controller: name=%s", controller.name)
    return controller


def get_worker_controller(name: str) -> WorkerController:
    """Return a controller by name.

    Raises:
        ValueError: if no controller has that name, naming the ones registered.
    """
    if name not in _REGISTRY:
        registered = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"Unknown worker controller: {name!r}. Registered: {registered}")
    return _REGISTRY[name]
