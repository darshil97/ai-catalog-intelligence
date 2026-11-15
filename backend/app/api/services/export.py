"""Exports and variant decisions (D41). The API builds files on request; see app.export."""

from typing import Any
from uuid import UUID

from app import catalog, export
from app.api.services.common import database, http_error, not_found
from app.auth import Profile
from app.storage import StorageError
from app.utils.logger import get_logger

logger = get_logger(__name__)


def create(profile: Profile, batch_id: UUID, fmt: str) -> dict[str, Any]:
    """Build an export of the batch's approved products.

    Raises:
        HTTPException: 404; 422 nothing_approved; 503 storage_unavailable.
    """
    try:
        with database("create_export") as conn:
            made = export.create(conn, profile.user_id, batch_id, fmt)
    except export.NotFoundError:
        not_found()
    except export.NothingApprovedError as exc:
        raise http_error(
            422,
            "nothing_approved",
            "No product is approved yet, or every approved one changed since. Approve "
            "products first.",
        ) from exc
    except StorageError as exc:
        logger.error("Export not stored: batch_id=%s error=%s", batch_id, exc)
        raise http_error(503, "storage_unavailable", "Couldn't save the file. Try again.") from exc
    logger.info("Export made: batch_id=%s format=%s products=%d left_out=%d",
                batch_id, fmt, made["product_count"], made["left_out_count"])  # fmt: skip
    return made


def list_exports(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """A batch's exports, or 404."""
    try:
        with database("list_exports") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            items = export.list_exports(conn, profile.user_id, batch_id)
    except catalog.NotFoundError:
        not_found()
    return {"batch_id": batch_id, "items": items}


def download(profile: Profile, export_id: UUID) -> dict[str, Any]:
    """A fresh link to an export, or 404."""
    try:
        with database("export_download") as conn:
            url = export.download_url(conn, profile.user_id, export_id)
    except export.NotFoundError:
        not_found()
    except StorageError as exc:
        raise http_error(503, "storage_unavailable", "Couldn't sign the link.") from exc
    return {"url": url}


def decide_variant(profile: Profile, variant_id: UUID, decision: str) -> dict[str, Any]:
    """Confirm or dismiss a variant pair, or 404."""
    try:
        with database("decide_variant") as conn:
            result = export.decide_variant(conn, profile.user_id, variant_id, decision)
    except export.NotFoundError:
        not_found()
    logger.info("Variant pair decided: variant_id=%s decision=%s", variant_id, decision)
    return result
