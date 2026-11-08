"""Powers off EC2 by creating a file that a root-owned systemd unit watches.

The worker runs unprivileged and without the ability to gain privileges, because it parses
files uploaded by the public. So it asks for a power-off rather than performing one, and the
instance role needs no EC2 permissions. The instance's shutdown behavior is set to stop.
"""

from pathlib import Path

from app.config import get_worker_config
from app.utils.logger import get_logger
from app.worker.power.base import register_power_controller

logger = get_logger(__name__)


class OsShutdownPowerController:
    """Asks systemd to power off the machine."""

    name = "os_shutdown"
    stops_machine = True

    def power_off(self) -> None:
        """Create the request file that triggers the power-off.

        Raises:
            OSError: if the file can't be created. The watchdog then powers off instead.
        """
        path = Path(get_worker_config()["power_request_file"])
        path.touch()
        logger.info("Power-off requested: path=%s", path)


register_power_controller(OsShutdownPowerController())
