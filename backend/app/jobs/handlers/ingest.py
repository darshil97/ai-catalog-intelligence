"""Ingest jobs: check every uploaded file, expand ZIPs, then apply the image limit.

ingest_plan     payload {"batch_id"}: one ingest_file chunk per uploaded file.
ingest_file     payload {"batch_id", "file_id"}: download, verify, check, expand ZIPs, and keep
                the images inside PDFs as files of their own.
ingest_finalize payload {"batch_id"}: keep the first images up to the limit, and count results.

A bad file is rejected with a reason, and its job still succeeds: only failures on our side
fail a job, and so the run, which gives the submission back. Every job can run again safely:
ZIP entries get IDs derived from the archive and member, and are replaced, not duplicated.
"""

import tempfile
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.config import get_ingest_config, get_uploads_config
from app.db import get_pool, query
from app.files import reasons
from app.files.reasons import FileRejectedError, Rejection
from app.ingest.archive import ArchiveContents, ArchiveEntry, ArchiveLimits, expand
from app.ingest.documents.pdf import EmbeddedImage, PdfUnreadableError, embedded_images
from app.ingest.kinds.base import CheckLimits, get_kind
from app.jobs import queue
from app.jobs.base import JobContext, register_handler
from app.jobs.errors import TransientJobError
from app.jobs.models import ChunkSpec, Job, JobOutcome
from app.storage import ObjectTooLargeError, StorageError, derived_key, get_object_store
from app.usage import effective_limits
from app.utils.logger import get_logger

logger = get_logger(__name__)

Row = dict[str, Any]


def _user_limits(conn: Connection[Any], owner: str) -> dict[str, int | None]:
    row = conn.execute(
        "SELECT overrides FROM user_limits WHERE user_id = %(owner)s", {"owner": owner}
    ).fetchone()
    return effective_limits(row["overrides"] if row else None)


def _check_limits(limits: dict[str, int | None]) -> CheckLimits:
    config = get_ingest_config()
    return CheckLimits(
        max_pdf_pages=limits["max_pdf_pages"],
        max_image_pixels=config["max_image_pixels"],
        csv_sniff_bytes=config["csv_sniff_bytes"],
        zip_max_total_bytes=config["zip_max_total_bytes"],
    )


def _archive_limits() -> ArchiveLimits:
    config = get_ingest_config()
    return ArchiveLimits(
        max_entries=config["zip_max_entries"],
        max_total_bytes=config["zip_max_total_bytes"],
        max_entry_bytes=config["zip_max_entry_bytes"],
        max_ratio=config["zip_max_ratio"],
        ratio_min_bytes=config["zip_ratio_min_bytes"],
    )


_IMAGE_TYPES = {"jpg": "image/jpeg", "png": "image/png"}


def _pdf_image_id(parent_id: UUID, image: EmbeddedImage) -> UUID:
    """The same image in the same PDF always gets the same ID, so a rerun replaces it."""
    return uuid.uuid5(parent_id, f"pdf:{image.page}:{image.index}")


def _child_id(parent_id: UUID, entry: ArchiveEntry) -> UUID:
    """The same archive entry always gets the same ID, so a rerun replaces it."""
    return uuid.uuid5(parent_id, f"{entry.index}:{entry.member}")


class IngestPlanHandler:
    """Creates one chunk per uploaded file in the batch."""

    name = "ingest_plan"

    def run(self, context: JobContext) -> JobOutcome:
        """Return an ingest_file chunk for each file."""
        batch_id = context.job.payload["batch_id"]
        with get_pool().connection() as conn:
            rows = conn.execute(
                query("files", "files_to_ingest"), {"batch_id": batch_id}
            ).fetchall()
        logger.info("Ingest planned: batch_id=%s files=%d", batch_id, len(rows))
        return JobOutcome(
            chunks=tuple(
                ChunkSpec(
                    key=f"file-{row['id']}",
                    job_type="ingest_file",
                    payload={"batch_id": batch_id, "file_id": str(row["id"])},
                )
                for row in rows
            )
        )


class IngestFileHandler:
    """Checks one uploaded file. ZIPs are expanded, and each entry is checked and stored."""

    name = "ingest_file"

    def run(self, context: JobContext) -> JobOutcome:
        """Check the file and record the result.

        Raises:
            TransientJobError: if storage or the database can't be reached.
        """
        job = context.job
        file_id = UUID(job.payload["file_id"])
        with get_pool().connection() as conn:
            row: Row | None = conn.execute(
                query("files", "get_file"), {"file_id": file_id}
            ).fetchone()
            limits = _user_limits(conn, job.owner_user_id)
        if row is None or row["status"] not in ("uploaded", "ingested"):
            # Removed or already rejected: nothing left to check.
            logger.info("Ingest skipped: file_id=%s", file_id)
            return JobOutcome()

        with tempfile.TemporaryDirectory(prefix="ingest-") as workdir:
            local = Path(workdir) / "source"
            rejection = self._download(row, local)
            if rejection is not None:
                self._reject(job, file_id, rejection, {})
                return JobOutcome()
            context.check_interrupted()
            if row["kind"] == "zip":
                self._ingest_archive(context, row, local, Path(workdir), limits)
            else:
                self._ingest_single(job, row, local, limits)
        return JobOutcome()

    def _download(self, row: Row, local: Path) -> Rejection | None:
        """Fetch the upload and confirm it is exactly what was registered."""
        try:
            sha256 = get_object_store().download(row["s3_key"], local, row["size_bytes"])
        except ObjectTooLargeError:
            return reasons.upload_mismatch()
        except StorageError as exc:
            raise TransientJobError(f"Download failed for file {row['id']}: {exc}") from exc
        if sha256 != row["sha256"] or local.stat().st_size != row["size_bytes"]:
            return reasons.upload_mismatch()
        return None

    def _ingest_single(
        self, job: Job, row: Row, local: Path, limits: dict[str, int | None]
    ) -> None:
        try:
            details = get_kind(row["extension"]).check(
                local, row["extension"], _check_limits(limits)
            )
        except FileRejectedError as exc:
            self._reject(job, row["id"], exc.rejection, {})
            return
        images: list[EmbeddedImage] = []
        if row["kind"] == "pdf":
            images, details = self._pdf_images(row, local, details)
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            if row["kind"] == "pdf":
                self._record_pdf_images(conn, row, images)
            conn.execute(
                query("files", "mark_ingested"), {"file_id": row["id"], "details": Jsonb(details)}
            )
        logger.info(
            "File ingested: file_id=%s kind=%s pdf_images=%d", row["id"], row["kind"], len(images)
        )

    def _pdf_images(
        self, row: Row, local: Path, details: dict[str, Any]
    ) -> tuple[list[EmbeddedImage], dict[str, Any]]:
        """Extract a PDF's images and store each one, before any row is written.

        A PDF whose images can't be read keeps its text: images are an extra, not the file.
        """
        config = get_ingest_config()
        try:
            images, over = embedded_images(
                local, config["pdf_image_min_side"], config["pdf_max_images_per_file"]
            )
        except PdfUnreadableError as exc:
            logger.warning("PDF images unreadable: file_id=%s error=%s", row["id"], exc)
            return [], {**details, "images": 0, "images_unreadable": True}
        store = get_object_store()
        for image in images:
            child_id = _pdf_image_id(row["id"], image)
            # The job's temporary folder holds the PDF, so the image goes beside it.
            image_path = local.parent / f"{child_id}.{image.extension}"
            image_path.write_bytes(image.data)
            try:
                store.upload(
                    derived_key(row["owner_user_id"], row["batch_id"], child_id),
                    image_path,
                    _IMAGE_TYPES[image.extension],
                    image.sha256,
                )
            except StorageError as exc:
                raise TransientJobError(f"Upload failed for PDF image {image.name}: {exc}") from exc
        return images, {**details, "images": len(images), "images_over_limit": over}

    def _record_pdf_images(
        self, conn: Connection[Any], row: Row, images: list[EmbeddedImage]
    ) -> None:
        """Replace the PDF's image entries. An image the batch already has is a duplicate."""
        conn.execute(query("files", "delete_children"), {"file_id": row["id"]})
        for order, image in enumerate(images, start=1):
            child_id = _pdf_image_id(row["id"], image)
            base = {
                "id": child_id,
                "batch_id": row["batch_id"],
                "owner": row["owner_user_id"],
                "parent_id": row["id"],
                "member": image.name,
                "position": row["position"],
                "member_index": order,
                "original_name": image.name,
                "extension": image.extension,
                "kind": "image",
                "content_type": _IMAGE_TYPES[image.extension],
                "size_bytes": len(image.data),
                "sha256": image.sha256,
            }
            inserted = conn.execute(
                query("files", "insert_child"),
                {**base, "s3_key": derived_key(row["owner_user_id"], row["batch_id"], child_id)},
            ).fetchone()
            if inserted is None:
                existing = conn.execute(
                    query("files", "find_by_sha256"),
                    {"batch_id": row["batch_id"], "sha256": image.sha256},
                ).fetchone()
                rejection = reasons.duplicate_file(
                    existing["original_name"] if existing else "another file"
                )
                conn.execute(
                    query("files", "insert_rejected_child"),
                    {**base, "reason": rejection.code, "message": rejection.message},
                )

    def _ingest_archive(
        self,
        context: JobContext,
        row: Row,
        local: Path,
        workdir: Path,
        limits: dict[str, int | None],
    ) -> None:
        job = context.job
        uploads = get_uploads_config()
        extract_dir = workdir / "entries"
        extract_dir.mkdir()
        try:
            contents = expand(
                local,
                extract_dir,
                uploads["types"],
                _archive_limits(),
                _check_limits(limits),
                uploads["max_original_name_length"],
            )
        except FileRejectedError as exc:
            # A rerun may follow an earlier attempt that stored entries; drop them too.
            self._reject(job, row["id"], exc.rejection, {}, drop_children=True)
            return

        duplicates = self._find_duplicates(row, contents)
        store = get_object_store()
        for entry in contents.entries:
            if entry.path is None or entry.sha256 in duplicates:
                continue
            context.check_interrupted()
            child_id = _child_id(row["id"], entry)
            try:
                store.upload(
                    derived_key(row["owner_user_id"], row["batch_id"], child_id),
                    entry.path,
                    uploads["types"][entry.extension]["content_type"],
                    entry.sha256 or "",
                )
            except StorageError as exc:
                raise TransientJobError(f"Upload failed for entry {entry.index}: {exc}") from exc
        self._record_archive(job, row, contents, duplicates)

    def _find_duplicates(self, row: Row, contents: ArchiveContents) -> dict[str, str]:
        """Return the hashes of kept entries the batch already has, with the existing name.

        Covers both files elsewhere in the batch and repeats inside this archive.
        """
        duplicates: dict[str, str] = {}
        seen: dict[str, str] = {}
        with get_pool().connection() as conn:
            for entry in contents.entries:
                if entry.path is None or entry.sha256 is None:
                    continue
                if entry.sha256 in seen:
                    continue
                existing = conn.execute(
                    query("files", "find_by_sha256"),
                    {"batch_id": row["batch_id"], "sha256": entry.sha256},
                ).fetchone()
                # This archive's own entries from an earlier attempt are about to be replaced.
                if existing is not None and existing["parent_file_id"] != row["id"]:
                    duplicates[entry.sha256] = existing["original_name"]
                seen[entry.sha256] = entry.name
        return duplicates

    def _record_archive(
        self, job: Job, row: Row, contents: ArchiveContents, duplicates: dict[str, str]
    ) -> None:
        """Replace the archive's entries in one transaction, and mark the archive ingested."""
        uploads = get_uploads_config()
        kept = rejected = 0
        stored_hashes: dict[str, str] = {}
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("files", "delete_children"), {"file_id": row["id"]})
            for entry in contents.entries:
                spec = uploads["types"].get(entry.extension)
                base = {
                    "id": _child_id(row["id"], entry),
                    "batch_id": row["batch_id"],
                    "owner": row["owner_user_id"],
                    "parent_id": row["id"],
                    "member": entry.member,
                    "position": row["position"],
                    "member_index": entry.index,
                    "original_name": entry.name,
                    "extension": entry.extension,
                    "kind": spec["kind"] if spec else "other",
                    "content_type": spec["content_type"] if spec else "application/octet-stream",
                    "size_bytes": entry.size_bytes,
                    "sha256": entry.sha256,
                }
                rejection = entry.rejection
                if rejection is None and entry.sha256 in duplicates:
                    rejection = reasons.duplicate_file(duplicates[entry.sha256])
                if rejection is None and entry.sha256 in stored_hashes:
                    rejection = reasons.duplicate_file(stored_hashes[entry.sha256])
                if rejection is None:
                    inserted = conn.execute(
                        query("files", "insert_child"),
                        {
                            **base,
                            "s3_key": derived_key(
                                row["owner_user_id"], row["batch_id"], base["id"]
                            ),
                        },
                    ).fetchone()
                    if inserted is not None:
                        assert entry.sha256 is not None  # noqa: S101
                        stored_hashes[entry.sha256] = entry.name
                        kept += 1
                        continue
                    rejection = reasons.duplicate_file("another file")
                conn.execute(
                    query("files", "insert_rejected_child"),
                    {**base, "reason": rejection.code, "message": rejection.message},
                )
                rejected += 1
            details = {
                "entries": len(contents.entries),
                "kept": kept,
                "rejected": rejected,
                "skipped_system_files": contents.skipped_system_files,
            }
            conn.execute(
                query("files", "mark_ingested"), {"file_id": row["id"], "details": Jsonb(details)}
            )
        logger.info(
            "Archive ingested: file_id=%s entries=%d kept=%d rejected=%d skipped=%d",
            row["id"],
            len(contents.entries),
            kept,
            rejected,
            contents.skipped_system_files,
        )

    def _reject(
        self,
        job: Job,
        file_id: UUID,
        rejection: Rejection,
        details: dict[str, Any],
        *,
        drop_children: bool = False,
    ) -> None:
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            if drop_children:
                conn.execute(query("files", "delete_children"), {"file_id": file_id})
            conn.execute(
                query("files", "reject_by_worker"),
                {
                    "file_id": file_id,
                    "reason": rejection.code,
                    "message": rejection.message,
                    "details": Jsonb(details),
                },
            )
        logger.info("File rejected: file_id=%s reason=%s", file_id, rejection.code)


class IngestFinalizeHandler:
    """Applies the per-batch image limit across all files, then records the run's counts."""

    name = "ingest_finalize"

    def run(self, context: JobContext) -> JobOutcome:
        """Leave out images past the limit, and store what was found, kept and left out."""
        job = context.job
        batch_id = job.payload["batch_id"]
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            max_images = _user_limits(conn, job.owner_user_id)["max_images_per_submission"]
            left_out = conn.execute(
                query("files", "apply_image_limit"),
                {
                    "batch_id": batch_id,
                    "max_images": max_images,
                    "message": reasons.image_limit(max_images or 0).message,
                },
            ).fetchall()
            counts = conn.execute(
                query("files", "record_run_counts"),
                {"batch_id": batch_id, "run_id": job.pipeline_run_id},
            ).fetchone()
        logger.info(
            "Ingest finalized: batch_id=%s images_left_out=%d counts=%s",
            batch_id,
            len(left_out),
            dict(counts) if counts else {},
        )
        return JobOutcome()


register_handler(IngestPlanHandler())
register_handler(IngestFileHandler())
register_handler(IngestFinalizeHandler())
