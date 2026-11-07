"""Does nothing, so a local worker can never shut down your laptop."""

from app.worker.power.base import register_power_controller


class NoopPowerController:
    """Keeps polling when idle, and never powers anything off."""

    name = "noop"
    stops_machine = False

    def power_off(self) -> None:
        """Do nothing."""


register_power_controller(NoopPowerController())
