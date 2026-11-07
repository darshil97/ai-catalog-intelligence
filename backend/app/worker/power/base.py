"""The PowerController contract and registry: what happens when the worker has been idle."""

from typing import Protocol

from app.utils.logger import get_logger

logger = get_logger(__name__)


class PowerController(Protocol):
    """Turns the machine off when the worker has no more work."""

    name: str
    # False means the worker keeps polling when idle, and never powers anything off.
    stops_machine: bool

    def power_off(self) -> None:
        """Turn the machine off."""
        ...


_REGISTRY: dict[str, PowerController] = {}


def register_power_controller(controller: PowerController) -> PowerController:
    """Add a controller to the registry under its name."""
    _REGISTRY[controller.name] = controller
    logger.info("Registered power controller: name=%s", controller.name)
    return controller


def get_power_controller(name: str) -> PowerController:
    """Return a controller by name.

    Raises:
        ValueError: if no controller has that name, naming the ones registered.
    """
    if name not in _REGISTRY:
        registered = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"Unknown power controller: {name!r}. Registered: {registered}")
    return _REGISTRY[name]
