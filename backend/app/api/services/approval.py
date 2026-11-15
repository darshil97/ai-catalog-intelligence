"""Human review (D40): value and copy edits, approval, duplicates, history and the review list."""

from collections.abc import Callable
from typing import Any, NoReturn
from uuid import UUID

from app import approval
from app.api.services.common import database, http_error, not_found
from app.api.services.enrichment import start_after_review
from app.auth import Profile
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _refuse(exc: Exception) -> NoReturn:
    """The error response for a refused write."""
    if isinstance(exc, approval.NotFoundError):
        not_found()
    if isinstance(exc, approval.StaleError):
        raise http_error(
            409,
            "changed_elsewhere",
            "This product changed since you opened it. Check it and try again.",
        ) from exc
    if isinstance(exc, approval.NotReadyError):
        raise http_error(422, "not_ready", " ".join(exc.reasons)) from exc
    raise http_error(422, "invalid", str(exc)) from exc


_REFUSED = (
    approval.NotFoundError,
    approval.StaleError,
    approval.NotReadyError,
    approval.InvalidError,
)


def _write(label: str, work: Callable[[Any], Any]) -> Any:
    try:
        with database(label) as conn:
            return work(conn)
    except _REFUSED as exc:
        _refuse(exc)


def set_value(profile: Profile, product_id: UUID, code: str, request: Any) -> dict[str, Any]:
    """Set or clear a value, then rewrite copy if the batch's review is done."""
    result = _write(
        "set_value",
        lambda conn: approval.set_value(
            conn, profile.user_id, product_id, code, request.value, request.note,
            request.revision,
        ),
    )  # fmt: skip
    logger.info("Value edited: product_id=%s attribute=%s cleared=%s",
                product_id, code, request.value is None)  # fmt: skip
    return {
        "revision": result["revision"],
        "copy_writing": start_after_review(profile, result["batch_id"]),
    }


def edit_copy(profile: Profile, product_id: UUID, request: Any) -> dict[str, Any]:
    """Save copy a person wrote."""
    result = _write(
        "edit_copy",
        lambda conn: approval.edit_copy(
            conn, profile.user_id, product_id, request.title, request.description,
            request.bullets, request.revision,
        ),
    )  # fmt: skip
    logger.info("Copy edited: product_id=%s version=%s", product_id, result["version"])
    return {"revision": result["revision"]}


def approve(profile: Profile, product_id: UUID, revision: int) -> dict[str, Any]:
    """Approve a product."""
    new = _write("approve", lambda c: approval.approve(c, profile.user_id, product_id, revision))
    logger.info("Product approved: product_id=%s", product_id)
    return {"revision": new}


def reject(profile: Profile, product_id: UUID, reason: str, revision: int) -> dict[str, Any]:
    """Reject a product."""
    new = _write(
        "reject", lambda c: approval.reject(c, profile.user_id, product_id, reason, revision)
    )
    logger.info("Product rejected: product_id=%s", product_id)
    return {"revision": new}


def restore(profile: Profile, product_id: UUID, revision: int) -> dict[str, Any]:
    """Put a product back to draft."""
    new = _write("restore", lambda c: approval.restore(c, profile.user_id, product_id, revision))
    logger.info("Product restored: product_id=%s", product_id)
    return {"revision": new}


def duplicate(profile: Profile, product_id: UUID, request: Any) -> dict[str, Any]:
    """Settle a duplicate pair."""
    result = _write(
        "mark_duplicate",
        lambda c: approval.mark_duplicate(
            c, profile.user_id, product_id, request.other_id, request.keep, request.revision
        ),
    )
    logger.info("Duplicate settled: product_id=%s keep=%s", product_id, request.keep)
    return {"revision": result["revision"]}


def batch_approval(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Every product's approval in a batch, or 404."""
    try:
        with database("batch_approval") as conn:
            batch = approval.load(conn, profile.user_id, batch_id)
    except approval.NotFoundError:
        not_found()
    items = [approval.view(batch, product) for product in batch.ctx.products]
    return {
        "batch_id": batch_id,
        "approved": sum(1 for item in items if item["state"] == approval.APPROVED),
        "ready": sum(1 for item in items if item["can_approve"]),
        "items": items,
    }


def product_approval(
    conn: Any, owner: str, product_id: UUID, batch_id: UUID
) -> dict[str, Any] | None:
    """A product's approval, for the product page."""
    batch = approval.load(conn, owner, batch_id)
    product = batch.product(product_id)
    return approval.view(batch, product) if product else None


def approve_batch(
    profile: Profile, batch_id: UUID, product_ids: list[UUID] | None
) -> dict[str, Any]:
    """Approve every ready product of a batch, or those listed."""
    result = _write(
        "approve_batch",
        lambda c: approval.approve_batch(c, profile.user_id, batch_id, product_ids),
    )
    logger.info("Batch approved: batch_id=%s approved=%d skipped=%d",
                batch_id, len(result["approved"]), len(result["skipped"]))  # fmt: skip
    return dict(result)


def reject_batch(
    profile: Profile, batch_id: UUID, product_ids: list[UUID], reason: str
) -> dict[str, Any]:
    """Reject the listed products of a batch."""
    rejected = _write(
        "reject_batch",
        lambda c: approval.reject_batch(c, profile.user_id, batch_id, product_ids, reason),
    )
    logger.info("Batch products rejected: batch_id=%s count=%d", batch_id, len(rejected))
    return {"approved": [], "skipped": [], "rejected": rejected}


def history(profile: Profile, product_id: UUID) -> dict[str, Any]:
    """A product's history, or 404."""
    with database("product_history") as conn:
        product = approval.product_row(conn, profile.user_id, product_id)
        if product is None:
            not_found()
        items = approval.product_history(conn, profile.user_id, product)
    return {"product_id": product_id, "items": items}


def review_summary(profile: Profile) -> dict[str, Any]:
    """Open review items across all of the user's batches."""
    with database("review_summary") as conn:
        return dict(approval.review_summary(conn, profile.user_id))
