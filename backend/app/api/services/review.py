"""Image matches, review decisions, and reading values from accepted photos."""

from typing import Any
from uuid import UUID

import psycopg

from app import catalog, review
from app.api.services.common import database, http_error, not_found
from app.auth import Profile
from app.compute.starter import request_worker_start
from app.config import get_catalog_config, get_images_config, get_processing_config
from app.jobs.settings import get_queue_settings
from app.storage import StorageError, get_object_store
from app.utils.logger import get_logger
from app.utils.request_id import get_request_id

logger = get_logger(__name__)


def _evidence(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"signal": s["signal"], "points": s["points"], "text": s["text"], "detail": s["detail"]}
        for s in signals
    ]


def _candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "product_key": row["product_key"],
        "score": float(row["score"]),
        "decision": row["decision"],
        "decided_by": row["decided_by"],
        "evidence": _evidence(row["signals"]),
    }


def thumbnail_url(key: str | None) -> str | None:
    """Sign a thumbnail link. A failure is logged, and the link left out."""
    if not key:
        return None
    try:
        return get_object_store().presign_download(
            key, get_images_config()["thumbnail_url_seconds"]
        )
    except StorageError as exc:
        logger.warning("Thumbnail link not signed: error=%s", exc)
        return None


def list_matches(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return every candidate of a batch, or 404."""
    limit = get_catalog_config()["max_list_items"]
    try:
        with database("list_matches") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            rows = review.list_matches(conn, profile.user_id, batch_id, limit)
    except catalog.NotFoundError:
        not_found()
    items = [
        {
            **_candidate(row),
            "image_id": row["image_id"],
            "source_file_id": row["source_file_id"],
            "image_name": row["image_name"],
            "decided_at": row["decided_at"],
            "values_read_at": row["values_read_at"],
        }
        for row in rows
    ]
    return {"batch_id": batch_id, "items": items}


def list_tasks(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return a batch's review tasks, or 404."""
    limit = get_catalog_config()["max_list_items"]
    try:
        with database("list_review_tasks") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            tasks = review.list_tasks(conn, profile.user_id, batch_id, limit)
            pending = review.pending_photo_values(conn, profile.user_id, batch_id)
    except catalog.NotFoundError:
        not_found()
    items = [
        {
            "id": task["id"],
            "status": task["status"],
            "image_id": task["image_id"],
            "source_file_id": task["source_file_id"],
            "image_name": task["image_name"],
            "thumbnail_url": thumbnail_url(task["thumbnail_s3_key"]),
            "candidates": [_candidate(c) for c in task["candidates"]],
            "created_at": task["created_at"],
            "resolved_at": task["resolved_at"],
        }
        for task in tasks
    ]
    return {"batch_id": batch_id, "pending_photo_values": pending, "items": items}


def _queue_photo_values(profile: Profile, batch_id: UUID) -> tuple[str, int]:
    """Start reading accepted photos, if any wait and no other run is active."""
    with database("pending_photo_values") as conn:
        pending = review.pending_photo_values(conn, profile.user_id, batch_id)
    if pending == 0:
        return "not_needed", 0
    if not get_processing_config()["enabled"]:
        return "waiting", pending
    try:
        with database("start_photo_values") as conn:
            run_id = review.start_photo_values(
                conn, profile.user_id, batch_id, get_request_id(), get_queue_settings()
            )
    except psycopg.errors.UniqueViolation:
        logger.info("Photo values waiting: reason=active_run owner=%s", profile.user_id)
        return "waiting", pending
    logger.info("Photo values queued: batch_id=%s run_id=%s photos=%d", batch_id, run_id, pending)
    request_worker_start()
    return "queued", pending


def decide(
    profile: Profile, task_id: UUID, decision: str, product_id: UUID | None
) -> dict[str, Any]:
    """Record a decision, then start reading the accepted photo's values.

    Raises:
        HTTPException: 404, 409 (already decided), 422 (not a candidate), or 503.
    """
    try:
        with database("decide_review_task") as conn:
            result = review.decide(conn, profile.user_id, task_id, decision, product_id)
    except review.TaskNotFoundError:
        not_found()
    except review.TaskResolvedError as exc:
        raise http_error(409, "already_decided", "This photo has already been decided.") from exc
    except review.NotACandidateError as exc:
        raise http_error(
            422, "not_a_candidate", "That product isn't one of this photo's candidates."
        ) from exc
    logger.info(
        "Review decided: task_id=%s decision=%s product_id=%s owner=%s",
        task_id,
        decision,
        product_id,
        profile.user_id,
    )
    status = "not_needed"
    if result["accepted_product_id"] is not None:
        status, _ = _queue_photo_values(profile, result["batch_id"])
    return {
        "task_id": task_id,
        "accepted_product_id": result["accepted_product_id"],
        "photo_values": status,
    }


def read_photo_values(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Start reading values from the batch's accepted photos, or 404."""
    try:
        with database("read_photo_values") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
    except catalog.NotFoundError:
        not_found()
    status, pending = _queue_photo_values(profile, batch_id)
    return {"photo_values": status, "pending": pending}
