"""Catalog checks (M8, D38): deterministic rules run whenever a batch or product is read.

Shared by the API and worker. Rules report; nothing here changes a product.
"""

from app.validation.base import ERROR, INFO, WARNING
from app.validation.engine import BatchReport, ProductReport, check_settings, describe_rules
from app.validation.repository import (
    NotAWarningError,
    NotFoundError,
    batch_report,
    set_aside,
    set_profile,
    undo_set_aside,
)

__all__ = [
    "ERROR",
    "INFO",
    "WARNING",
    "BatchReport",
    "NotAWarningError",
    "NotFoundError",
    "ProductReport",
    "batch_report",
    "check_settings",
    "describe_rules",
    "set_aside",
    "set_profile",
    "undo_set_aside",
]
