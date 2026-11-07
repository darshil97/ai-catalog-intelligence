"""Logging setup. The request ID comes second, so one grep replays a request."""

import logging
import sys

from app.config import get_logging_config
from app.utils.request_id import get_request_id

__all__ = ["RequestIdFilter", "configure_logging", "get_logger"]

_LOG_FORMAT = "%(asctime)s | %(request_id)s | %(levelname)s | %(name)s | %(message)s"


class RequestIdFilter(logging.Filter):
    """Add the current request ID to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach request_id, using N/A outside a request."""
        record.request_id = get_request_id() or "N/A"
        return True


def configure_logging() -> None:
    """Send logs to stdout in the project format. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(get_logging_config()["level"])


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name."""
    return logging.getLogger(name)
