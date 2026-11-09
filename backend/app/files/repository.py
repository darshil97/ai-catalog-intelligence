"""Uploaded files and batch submission, always scoped to their owner.

Every function runs inside the caller's transaction and locks the batch row first, so
registering files, removing them and submitting the batch happen one at a time per batch.
Another user's batch or file behaves exactly like one that doesn't exist.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection

from app.catalog import NotFoundError
from app.config import get_pipeline_config, get_uploads_config
from app.db import query
from app.files import reasons
from app.files.names import clean_name, extension_of
from app.jobs import queue
from app.jobs.models import QueueSettings, StageSpec
from app.storage import ObjectStore, raw_key
from app.usage import effective_limits, record_submission
from app.utils.logger import get_logger

logger = get_logger(__name__)

Row = dict[str, Any]

INGEST_PIPELINE = "ingest"
# Files can be added, removed and submitted only in these states. A failed batch can be retried.
EDITABLE_STATUSES = frozenset({"draft", "failed"})
BUSY_STATUSES = frozenset({"queued", "processing"})


class BatchNotEditableError(Exception):
    """The batch is processing or already processed, so its files can't change."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class DuplicateFileError(Exception):
    """The batch already has a file with this content."""

    def __init__(self, existing_name: str) -> None:
        super().__init__(existing_name)
        self.existing_name = existing_name


class UploadMissingError(Exception):
    """Storage has no object for this file yet, so the upload didn't reach it."""


class FileNotPendingError(Exception):
    """The file isn't waiting for an upload, for example because its link expired."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class ArchiveEntryError(Exception):
    """A file found inside a ZIP can't be changed on its own."""


class NoFilesError(Exception):
    """The batch has no uploaded files to process."""


@dataclass(frozen=True)
class Registration:
    """A registered upload.

    Attributes:
        file: The file row.
        resumed: True when the same content was already waiting for its upload, and its link
            was renewed rather than a new file created.
    """

    file: Row
    resumed: bool


def _lock_batch(conn: Connection[Any], owner: str, batch_id: UUID) -> Row:
    row: Row | None = conn.execute(
        query("files", "lock_batch"), {"owner": owner, "batch_id": batch_id}
    ).fetchone()
    if row is None:
        raise NotFoundError(str(batch_id))
    return row


def _require_editable(status: str) -> None:
    if status not in EDITABLE_STATUSES:
        raise BatchNotEditableError(status)


def _expire_pending(conn: Connection[Any], batch_id: UUID, *, all_pending: bool) -> None:
    conn.execute(
        query("files", "expire_pending"),
        {
            "batch_id": batch_id,
            "all_pending": all_pending,
            "message": reasons.upload_not_completed().message,
        },
    )


def _totals(conn: Connection[Any], batch_id: UUID) -> Row:
    row: Row | None = conn.execute(
        query("files", "batch_totals"), {"batch_id": batch_id}
    ).fetchone()
    assert row is not None  # noqa: S101 - an aggregate always returns one row
    return row


def register_upload(
    conn: Connection[Any], owner: str, batch_id: UUID, name: str, size_bytes: int, sha256: str
) -> Registration:
    """Record a file the browser is about to upload, after checking every limit.

    Raises:
        NotFoundError: if the owner has no such batch.
        BatchNotEditableError: if the batch is processing or processed.
        reasons.FileRejectedError: if the type, size, file count or batch total is not allowed.
        DuplicateFileError: if the batch already has this content.
    """
    uploads = get_uploads_config()
    batch = _lock_batch(conn, owner, batch_id)
    _require_editable(batch["status"])
    limits = effective_limits(batch["overrides"])
    _expire_pending(conn, batch_id, all_pending=False)

    original_name = clean_name(name, uploads["max_original_name_length"])
    extension = extension_of(original_name)
    spec = uploads["types"].get(extension)
    if spec is None:
        _refuse(reasons.unsupported_type(extension))
    if size_bytes <= 0:
        _refuse(reasons.empty_file())
    max_file = limits["max_file_bytes"]
    if max_file is not None and size_bytes > max_file:
        _refuse(reasons.file_too_large(size_bytes, max_file))

    existing: Row | None = conn.execute(
        query("files", "find_by_sha256"), {"batch_id": batch_id, "sha256": sha256}
    ).fetchone()
    if existing is not None:
        if existing["status"] == "pending_upload" and existing["parent_file_id"] is None:
            renewed: Row | None = conn.execute(
                query("files", "renew_upload"),
                {
                    "id": existing["id"],
                    "original_name": original_name,
                    "expires_seconds": uploads["upload_url_seconds"],
                },
            ).fetchone()
            assert renewed is not None  # noqa: S101
            return Registration(file=renewed, resumed=True)
        raise DuplicateFileError(existing["original_name"])

    totals = _totals(conn, batch_id)
    max_files = limits["files_per_submission"]
    if max_files is not None and totals["files"] >= max_files:
        _refuse(reasons.too_many_files(max_files))
    max_total = limits["max_submission_bytes"]
    if max_total is not None and totals["bytes"] + size_bytes > max_total:
        _refuse(reasons.submission_too_large(totals["bytes"] + size_bytes, max_total))

    file_id = uuid4()
    row: Row | None = conn.execute(
        query("files", "insert_upload"),
        {
            "id": file_id,
            "batch_id": batch_id,
            "owner": owner,
            "s3_key": raw_key(owner, batch_id, file_id),
            "original_name": original_name,
            "extension": extension,
            "kind": spec["kind"],
            "content_type": spec["content_type"],
            "size_bytes": size_bytes,
            "sha256": sha256,
            "expires_seconds": uploads["upload_url_seconds"],
        },
    ).fetchone()
    assert row is not None  # noqa: S101
    logger.info(
        "Upload registered: file_id=%s batch_id=%s kind=%s size_bytes=%d",
        file_id,
        batch_id,
        spec["kind"],
        size_bytes,
    )
    return Registration(file=row, resumed=False)


def _refuse(rejection: reasons.Rejection) -> Any:
    raise reasons.FileRejectedError(rejection.code, rejection.message)


def _lock_file(conn: Connection[Any], owner: str, file_id: UUID) -> Row:
    row: Row | None = conn.execute(
        query("files", "lock_file"), {"owner": owner, "file_id": file_id}
    ).fetchone()
    if row is None:
        raise NotFoundError(str(file_id))
    if row["parent_file_id"] is not None:
        raise ArchiveEntryError(str(file_id))
    return row


def complete_upload(
    conn: Connection[Any], owner: str, file_id: UUID, store: ObjectStore
) -> tuple[Row, list[str]]:
    """Check the uploaded object against what was registered, and accept or reject it.

    Returns:
        The file, and keys to delete once the transaction commits (a rejected upload's object).

    Raises:
        NotFoundError: if the owner has no such file.
        ArchiveEntryError: if it's a file found inside a ZIP.
        BatchNotEditableError: if the batch is processing or processed.
        FileNotPendingError: if the file isn't waiting for an upload.
        UploadMissingError: if storage has no object yet. The file stays pending.
        app.storage.StorageError: if storage can't be reached.
    """
    current = _lock_file(conn, owner, file_id)
    _require_editable(current["batch_status"])
    if current["status"] == "uploaded":
        row: Row | None = conn.execute(
            query("files", "mark_uploaded"), {"file_id": file_id}
        ).fetchone()
        assert row is not None  # noqa: S101
        return row, []
    if current["status"] != "pending_upload":
        raise FileNotPendingError(current["status"])

    info = store.head(current["s3_key"])
    if info is None:
        raise UploadMissingError(str(file_id))
    # S3 enforces both through the upload policy. Checking again catches a misconfigured
    # bucket or policy, rather than trusting it silently.
    if info.size_bytes != current["size_bytes"] or (
        info.sha256 is not None and info.sha256 != current["sha256"]
    ):
        rejection = reasons.upload_mismatch()
        row = conn.execute(
            query("files", "reject"),
            {"file_id": file_id, "reason": rejection.code, "message": rejection.message},
        ).fetchone()
        assert row is not None  # noqa: S101
        logger.warning("Upload rejected: file_id=%s reason=%s", file_id, rejection.code)
        return row, [current["s3_key"]]

    row = conn.execute(query("files", "mark_uploaded"), {"file_id": file_id}).fetchone()
    assert row is not None  # noqa: S101
    logger.info(
        "Upload completed: file_id=%s checksum_verified=%s", file_id, info.sha256 is not None
    )
    return row, []


def delete_file(conn: Connection[Any], owner: str, file_id: UUID) -> list[str]:
    """Remove an uploaded file and anything extracted from it.

    Returns:
        Storage keys to delete once the transaction commits.

    Raises:
        NotFoundError: if the owner has no such file.
        ArchiveEntryError: if it's a file found inside a ZIP.
        BatchNotEditableError: if the batch is processing or processed.
    """
    current = _lock_file(conn, owner, file_id)
    _require_editable(current["batch_status"])
    row: Row | None = conn.execute(
        query("files", "delete_file"), {"owner": owner, "file_id": file_id}
    ).fetchone()
    assert row is not None  # noqa: S101 - the row is locked
    logger.info("File deleted: file_id=%s", file_id)
    return list(row["keys"])


def list_files(conn: Connection[Any], owner: str, batch_id: UUID, limit: int) -> list[Row]:
    """Return a batch's files in upload order, each followed by what was found inside it."""
    return conn.execute(
        query("files", "list_files"),
        {
            "owner": owner,
            "batch_id": batch_id,
            "limit": limit,
            "expired_message": reasons.upload_not_completed().message,
        },
    ).fetchall()


def submit_batch(
    conn: Connection[Any],
    owner: str,
    batch_id: UUID,
    request_id: str | None,
    settings: QueueSettings,
) -> UUID:
    """Charge one submission and queue the batch's ingest run, all or nothing.

    Unfinished uploads are given up, because the browser only submits once its uploads end.

    Returns:
        The new run's ID.

    Raises:
        NotFoundError: if the owner has no such batch.
        BatchNotEditableError: if it's processing or already processed.
        NoFilesError: if it has no uploaded files.
        reasons.FileRejectedError: if a file count or total size limit is exceeded.
        app.usage.LimitExceededError: if a submission limit is reached.
        psycopg.errors.UniqueViolation: if the owner already has an active run.
    """
    batch = _lock_batch(conn, owner, batch_id)
    _require_editable(batch["status"])
    limits = effective_limits(batch["overrides"])
    _expire_pending(conn, batch_id, all_pending=True)
    conn.execute(query("files", "reset_for_processing"), {"batch_id": batch_id})

    totals = _totals(conn, batch_id)
    if totals["files"] == 0:
        raise NoFilesError(str(batch_id))
    max_files = limits["files_per_submission"]
    if max_files is not None and totals["files"] > max_files:
        _refuse(reasons.too_many_files(max_files))
    max_total = limits["max_submission_bytes"]
    if max_total is not None and totals["bytes"] > max_total:
        _refuse(reasons.submission_too_large(totals["bytes"], max_total))

    payload = {"batch_id": str(batch_id)}
    stages = [
        StageSpec(stage=stage["stage"], job_type=stage["job_type"], payload=payload)
        for stage in get_pipeline_config(INGEST_PIPELINE)
    ]
    run_id = queue.create_run(
        conn, INGEST_PIPELINE, stages, owner, request_id, settings, batch_id=batch_id
    )
    record_submission(conn, owner, run_id)
    conn.execute(query("files", "set_batch_status"), {"batch_id": batch_id, "status": "queued"})
    logger.info(
        "Batch submitted: batch_id=%s run_id=%s files=%d bytes=%d",
        batch_id,
        run_id,
        totals["files"],
        totals["bytes"],
    )
    return run_id


def latest_run(conn: Connection[Any], owner: str, batch_id: UUID) -> Row | None:
    """Return the batch's most recent run with stage progress and errors, or None."""
    row: Row | None = conn.execute(
        query("files", "latest_run"), {"owner": owner, "batch_id": batch_id}
    ).fetchone()
    return row
