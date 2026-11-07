"""The JobHandler contract and registry."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.jobs.errors import JobInterruptedError
from app.jobs.models import Job, JobOutcome
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class JobContext:
    """What a handler can use while running."""

    job: Job
    wait: Callable[[float], None]
    should_stop: Callable[[], bool]

    def check_interrupted(self) -> None:
        """Stop here if the job has to end. Handlers call this between units of work.

        Raises:
            JobInterruptedError: if the worker is stopping or lost its lease.
        """
        if self.should_stop():
            raise JobInterruptedError(f"Job {self.job.id} interrupted")


class JobHandler(Protocol):
    """Performs one type of job. Must be idempotent, because a job can run more than once."""

    name: str

    def run(self, context: JobContext) -> JobOutcome:
        """Do the work.

        Raises:
            TransientJobError: for failures worth retrying.
            PermanentJobError: for failures retrying can't fix.
            JobInterruptedError: from context.check_interrupted or context.wait.
        """
        ...


_REGISTRY: dict[str, JobHandler] = {}


def register_handler(handler: JobHandler) -> JobHandler:
    """Add a handler to the registry under its name."""
    _REGISTRY[handler.name] = handler
    logger.info("Registered job handler: name=%s", handler.name)
    return handler


def get_registered_handler_names() -> set[str]:
    """Return the names of all registered handlers."""
    return set(_REGISTRY)


def get_handler(name: str) -> JobHandler:
    """Return a handler by name.

    Raises:
        ValueError: if no handler has that name, naming the ones registered.
    """
    if name not in _REGISTRY:
        registered = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"Unknown job type: {name!r}. Registered: {registered}")
    return _REGISTRY[name]
