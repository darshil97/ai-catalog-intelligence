"""Uploading files to a batch, and submitting it for processing.

The API never touches file contents: the browser uploads straight to storage, and the worker
checks what arrived.
"""

from collections.abc import Sequence
from typing import Any, NoReturn
from uuid import UUID

import psycopg
from fastapi import HTTPException

from app import catalog
from app.api.services.common import database, http_error, not_found
from app.auth import Profile
from app.compute.starter import request_worker_start
from app.config import get_catalog_config, get_processing_config, get_uploads_config
from app.files import repository as files
from app.files.reasons import FileRejectedError
from app.jobs.settings import get_queue_settings
from app.storage import StorageError, get_object_store
from app.usage import LimitExceededError
from app.utils.logger import get_logger
from app.utils.request_id import get_request_id

logger = get_logger(__name__)

_LIMIT_MESSAGES = {
    "submissions_lifetime": "You've used all {allowed} of your batch submissions.",
    "submissions_per_24_hours": (
        "You've used your {allowed} batch submission for the last 24 hours. Try again later."
    ),
}


def _not_editable(status: str) -> HTTPException:
    if status in files.BUSY_STATUSES:
        message = "This batch is being processed, so its files can't change."
    else:
        message = "This batch has already been processed, so its files can't change."
    return http_error(409, "batch_not_editable", message)


def _storage_unavailable(operation: str, exc: StorageError) -> HTTPException:
    logger.error("Storage unavailable: operation=%s error=%s", operation, exc)
    return http_error(503, "storage_unavailable", "File storage is unavailable. Try again.")


def _delete_objects(keys: Sequence[str], operation: str) -> None:
    """Delete objects after their rows are gone. A failure is logged and left for the bucket's
    expiry rule, because the user's request already succeeded."""
    if not keys:
        return
    try:
        get_object_store().delete(keys)
    except StorageError:
        logger.warning(
            "Stored objects left for expiry: operation=%s count=%d", operation, len(keys)
        )


def _reraise_common(exc: Exception) -> NoReturn:
    if isinstance(exc, catalog.NotFoundError):
        not_found()
    if isinstance(exc, files.BatchNotEditableError):
        raise _not_editable(exc.status) from exc
    if isinstance(exc, files.ArchiveEntryError):
        raise http_error(
            409, "archive_entry", "Files found inside a ZIP can't be changed on their own."
        ) from exc
    if isinstance(exc, FileRejectedError):
        raise http_error(422, exc.rejection.code, exc.rejection.message) from exc
    raise exc


def register_upload(
    profile: Profile, batch_id: UUID, name: str, size_bytes: int, sha256: str
) -> dict[str, Any]:
    """Register a file and return its upload link.

    Raises:
        HTTPException: 404, 409 (duplicate or not editable), 422 (refused), or 503.
    """
    try:
        with database("register_upload") as conn:
            registration = files.register_upload(
                conn, profile.user_id, batch_id, name, size_bytes, sha256
            )
    except files.DuplicateFileError as exc:
        raise http_error(
            409,
            "duplicate_file",
            f"This batch already has this file, as {exc.existing_name}.",
        ) from exc
    except (
        catalog.NotFoundError,
        files.BatchNotEditableError,
        FileRejectedError,
    ) as exc:
        _reraise_common(exc)

    row = registration.file
    # Signing happens after the commit. If it fails, registering again renews this file.
    try:
        upload = get_object_store().presign_upload(
            row["s3_key"],
            row["content_type"],
            row["size_bytes"],
            row["sha256"],
            get_uploads_config()["upload_url_seconds"],
        )
    except StorageError as exc:
        raise _storage_unavailable("presign_upload", exc) from exc
    return {
        "file": row,
        "upload": {
            "url": upload.url,
            "fields": upload.fields,
            "expires_at": row["upload_expires_at"],
        },
        "resumed": registration.resumed,
    }


def complete_upload(profile: Profile, file_id: UUID) -> dict[str, Any]:
    """Confirm an upload reached storage intact.

    Raises:
        HTTPException: 404, 409 (missing upload, not pending, not editable), or 503.
    """
    try:
        with database("complete_upload") as conn:
            row, delete_keys = files.complete_upload(
                conn, profile.user_id, file_id, get_object_store()
            )
    except files.UploadMissingError as exc:
        raise http_error(
            409, "upload_missing", "The upload hasn't reached storage. Upload the file again."
        ) from exc
    except files.FileNotPendingError as exc:
        raise http_error(
            409, "file_not_pending", f"This file isn't waiting for an upload ({exc.status})."
        ) from exc
    except StorageError as exc:
        raise _storage_unavailable("complete_upload", exc) from exc
    except (catalog.NotFoundError, files.BatchNotEditableError, files.ArchiveEntryError) as exc:
        _reraise_common(exc)
    _delete_objects(delete_keys, "complete_upload")
    return dict(row)


def delete_file(profile: Profile, file_id: UUID) -> None:
    """Remove a file from a batch that hasn't been processed.

    Raises:
        HTTPException: 404, 409 (not editable or an archive entry), or 503.
    """
    try:
        with database("delete_file") as conn:
            keys = files.delete_file(conn, profile.user_id, file_id)
    except (catalog.NotFoundError, files.BatchNotEditableError, files.ArchiveEntryError) as exc:
        _reraise_common(exc)
    _delete_objects(keys, "delete_file")


def list_files(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return a batch's files, or 404."""
    limit = get_catalog_config()["max_list_items"]
    try:
        with database("list_files") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            items = files.list_files(conn, profile.user_id, batch_id, limit)
    except catalog.NotFoundError:
        not_found()
    return {"batch_id": batch_id, "items": items}


def submit_batch(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Charge a submission and queue the batch, then make sure a worker is starting.

    Raises:
        HTTPException: 503 if processing is off or the database is down, 404, 409 (not
            editable or an active run), 422 (no files or over a limit), or 429 (submission
            limit reached).
    """
    if not get_processing_config()["enabled"]:
        logger.warning("Submission refused: reason=processing_disabled owner=%s", profile.user_id)
        raise http_error(
            503, "processing_disabled", "Processing is paused right now. Try again later."
        )
    try:
        with database("submit_batch") as conn:
            files.submit_batch(
                conn, profile.user_id, batch_id, get_request_id(), get_queue_settings()
            )
            run = files.latest_run(conn, profile.user_id, batch_id)
    except files.NoFilesError as exc:
        raise http_error(422, "no_files", "Upload at least one file first.") from exc
    except LimitExceededError as exc:
        template = _LIMIT_MESSAGES.get(exc.limit, "You've reached your limit ({limit}).")
        raise http_error(
            429, "limit_reached", template.format(allowed=exc.allowed, limit=exc.limit)
        ) from exc
    except psycopg.errors.UniqueViolation as exc:
        logger.info("Submission refused: reason=active_run owner=%s", profile.user_id)
        raise http_error(
            409, "active_run", "Another of your batches is processing. Wait for it to finish."
        ) from exc
    except (catalog.NotFoundError, files.BatchNotEditableError, FileRejectedError) as exc:
        _reraise_common(exc)
    # The run is committed first, so a failed worker start can't lose it.
    request_worker_start()
    assert run is not None  # noqa: S101
    return _run_response(run)


def get_latest_run(profile: Profile, batch_id: UUID) -> dict[str, Any] | None:
    """Return the batch's latest run, or None if it was never submitted.

    Raises:
        HTTPException: 404 if the caller has no such batch.
    """
    try:
        with database("get_latest_run") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            run = files.latest_run(conn, profile.user_id, batch_id)
    except catalog.NotFoundError:
        not_found()
    if run is None:
        return None
    if run["finished_at"] is None:
        request_worker_start()
    return _run_response(run)


def _run_response(run: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": run["id"], **{key: value for key, value in run.items() if key != "id"}}
