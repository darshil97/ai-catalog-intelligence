"""Power controllers. Importing this package registers them."""

import app.worker.power.noop  # noqa: F401
import app.worker.power.os_shutdown  # noqa: F401
from app.worker.power.base import PowerController, get_power_controller

__all__ = ["PowerController", "get_power_controller"]
