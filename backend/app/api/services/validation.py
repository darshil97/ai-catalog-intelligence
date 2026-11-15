"""Catalog checks: reading them, choosing a batch's profile, and setting warnings aside (D38)."""

from typing import Any
from uuid import UUID

from app import validation
from app.api.services.common import database, http_error, not_found
from app.auth import Profile
from app.config import get_validation_config
from app.utils.logger import get_logger

logger = get_logger(__name__)


def product_summary(
    report: validation.BatchReport, item: validation.ProductReport
) -> dict[str, Any]:
    """A product's checks for API responses."""
    return {
        "profile": report.profile,
        "profile_name": report.profile_name,
        "can_approve": item.can_approve,
        "errors": item.count(validation.ERROR),
        "warnings": item.count(validation.WARNING),
        "info": item.count(validation.INFO),
        "issues": [issue.as_dict() for issue in item.issues],
    }


def _batch(report: validation.BatchReport, batch_id: UUID) -> dict[str, Any]:
    profiles = get_validation_config()["profiles"]
    return {
        "batch_id": batch_id,
        "profile": report.profile,
        "profile_name": report.profile_name,
        "profiles": [{"code": code, "name": p["name"]} for code, p in profiles.items()],
        "rules_version": report.rules_version,
        "ready": sum(1 for item in report.products if item.can_approve),
        "items": [
            {
                "product_id": item.product_id,
                "product_key": item.product_key,
                **product_summary(report, item),
            }
            for item in report.products
        ],  # fmt: skip
    }


def batch_validation(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return every product's checks, or 404."""
    try:
        with database("batch_validation") as conn:
            report = validation.batch_report(conn, profile.user_id, batch_id)
    except validation.NotFoundError:
        not_found()
    return _batch(report, batch_id)


def set_profile(profile: Profile, batch_id: UUID, code: str) -> dict[str, Any]:
    """Change a batch's marketplace profile, then return its checks.

    Raises:
        HTTPException: 404, or 422 unknown_profile.
    """
    if code not in get_validation_config()["profiles"]:
        raise http_error(422, "unknown_profile", "That isn't a marketplace profile.")
    try:
        with database("set_validation_profile") as conn:
            validation.set_profile(conn, profile.user_id, batch_id, code)
            report = validation.batch_report(conn, profile.user_id, batch_id)
    except validation.NotFoundError:
        not_found()
    logger.info("Validation profile set: batch_id=%s profile=%s", batch_id, code)
    return _batch(report, batch_id)


def product_validation(
    conn: Any, owner: str, product_id: UUID, batch_id: UUID
) -> dict[str, Any] | None:
    """A product's checks, for the product page."""
    report = validation.batch_report(conn, owner, batch_id)
    item = report.product(product_id)
    return product_summary(report, item) if item else None


def set_aside(profile: Profile, product_id: UUID, request: Any) -> dict[str, Any]:
    """Set a warning aside, then return the product's checks.

    Raises:
        HTTPException: 404, or 422 not_a_warning.
    """
    try:
        with database("set_aside_warning") as conn:
            validation.set_aside(
                conn, profile.user_id, product_id, request.rule, request.subject,
                request.reason.strip(),
            )  # fmt: skip
            batch_id = validation.repository.product_batch(conn, profile.user_id, product_id)
            result = product_validation(conn, profile.user_id, product_id, batch_id)
    except validation.NotFoundError:
        not_found()
    except validation.NotAWarningError as exc:
        raise http_error(
            422, "not_a_warning", "Only a current warning can be set aside; fix errors instead."
        ) from exc
    logger.info("Warning set aside: product_id=%s rule=%s", product_id, request.rule)
    assert result is not None  # noqa: S101
    return result


def undo_set_aside(profile: Profile, product_id: UUID, rule: str, subject: str) -> dict[str, Any]:
    """Bring a set-aside warning back, then return the product's checks.

    Raises:
        HTTPException: 404, or 409 not_set_aside.
    """
    try:
        with database("undo_set_aside") as conn:
            if not validation.undo_set_aside(conn, profile.user_id, product_id, rule, subject):
                raise http_error(409, "not_set_aside", "That warning isn't set aside.")
            batch_id = validation.repository.product_batch(conn, profile.user_id, product_id)
            result = product_validation(conn, profile.user_id, product_id, batch_id)
    except validation.NotFoundError:
        not_found()
    assert result is not None  # noqa: S101
    return result
