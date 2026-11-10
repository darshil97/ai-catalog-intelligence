"""Image jobs: a record, thumbnail, hash, colour signature and embedding for each kept image,
then near-duplicate groups across the batch (D20, D34).

images_plan    payload {"batch_id"}: clear the batch's image records, and plan one
    images_process chunk per few images, in file order.
images_process payload {"batch_id", "file_ids"}: process each image of the chunk.
images_group   payload {"batch_id"}: group near-duplicates, which needs every image done.

A duplicate group is evidence for matching, not a decision: nothing is deleted or left out.
Two images are near-duplicates only if their embeddings are similar and their colours close,
because embeddings alone treat a product in another colour as the same picture.

Every job can run again safely. Image and thumbnail IDs come from the source file, an image is
embedded once per model, and groups are rebuilt from scratch. An image that can't be read or
embedded is reported, and the chunk carries on.
"""

import hashlib
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.config import get_embeddings_config, get_images_config
from app.db import get_pool, query
from app.images import similar_pairs
from app.jobs import queue
from app.jobs.base import JobContext, register_handler
from app.jobs.errors import PermanentJobError, TransientJobError
from app.jobs.models import ChunkSpec, Job, JobOutcome
from app.llm import LlmBudgetError, check_budget
from app.storage import ObjectTooLargeError, StorageError, derived_key, get_object_store
from app.utils.logger import get_logger
from app.vision import (
    EmbeddingUnavailableError,
    ImageUnreadableError,
    colour_distance,
    colour_signature,
    for_embedding,
    get_embedder,
    load_rgb,
    phash,
    thumbnail,
)

logger = get_logger(__name__)

Row = dict[str, Any]

EMBEDDING_TASK = "image_embedding"
SKIP_UNREADABLE = "unreadable"
NOTE_TOO_SMALL = "too_small"
NOTE_UNAVAILABLE = "unavailable"
NOTE_BUDGET = "budget"
_NOTE_MESSAGES = {
    NOTE_TOO_SMALL: "Too small to compare by content.",
    NOTE_UNAVAILABLE: "The embedding model couldn't be reached, so this image can't be "
    "compared by content.",
    NOTE_BUDGET: "This month's LLM budget has been used, so this image wasn't compared by content.",
}


def image_id(file_id: UUID) -> UUID:
    """The same source file always gets the same image ID."""
    return uuid.uuid5(file_id, "image")


def _thumbnail_id(file_id: UUID) -> UUID:
    return uuid.uuid5(file_id, "thumbnail")


def _model_id() -> UUID:
    config = get_embeddings_config()
    with get_pool().connection() as conn:
        row = conn.execute(
            query("images", "embedding_model"),
            {
                "provider": config["provider"],
                "name": config["model_id"],
                "version": config["model_version"],
            },
        ).fetchone()
    if row is None or row["dimension"] != config["dimension"]:
        raise PermanentJobError(
            f"No embedding model {config['model_id']} v{config['model_version']} with dimension "
            f"{config['dimension']}: add it with a migration"
        )
    return UUID(str(row["id"]))


class ImagesPlanHandler:
    """Clears the batch's image records and plans the processing chunks."""

    name = "images_plan"

    def run(self, context: JobContext) -> JobOutcome:
        """Return one images_process chunk per few images, in file order."""
        job = context.job
        batch_id = job.payload["batch_id"]
        chunk_size = get_images_config()["chunk_size"]
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("images", "clear_batch_groups"), {"batch_id": batch_id})
            conn.execute(query("images", "clear_batch_images"), {"batch_id": batch_id})
            files = conn.execute(
                query("images", "images_to_process"), {"batch_id": batch_id}
            ).fetchall()
        ids = [str(file["id"]) for file in files]
        logger.info("Images planned: batch_id=%s images=%d", batch_id, len(ids))
        return JobOutcome(
            chunks=tuple(
                ChunkSpec(
                    key=f"images-{start}",
                    job_type="images_process",
                    payload={"batch_id": batch_id, "file_ids": ids[start : start + chunk_size]},
                )
                for start in range(0, len(ids), chunk_size)
            )
        )


class ImagesProcessHandler:
    """Processes a chunk of images."""

    name = "images_process"

    def run(self, context: JobContext) -> JobOutcome:
        """Record, hash, thumbnail and embed each image of the chunk.

        Raises:
            TransientJobError: if storage or the database can't be reached.
        """
        job = context.job
        settings = get_images_config()
        model_id = _model_id()
        for file_id in job.payload["file_ids"]:
            context.check_interrupted()
            with get_pool().connection() as conn:
                file: Row | None = conn.execute(
                    query("images", "get_file"),
                    {"file_id": file_id, "batch_id": job.payload["batch_id"]},
                ).fetchone()
            if file is None or file["status"] != "ingested":
                continue
            self._process(job, file, settings, model_id)
        return JobOutcome()

    def _process(self, job: Job, file: Row, settings: Row, model_id: UUID) -> None:
        timings: dict[str, int] = {}

        def timed(step: str, started: float) -> None:
            timings[step] = int((time.monotonic() - started) * 1000)

        record: Row = {
            "id": image_id(file["id"]),
            "batch_id": file["batch_id"],
            "owner": file["owner_user_id"],
            "file_id": file["id"],
            "status": "processed",
            "skip_reason": None,
            "skip_message": None,
            "sha256": file["sha256"],
            "phash": None,
            "colour": None,
            "width": None,
            "height": None,
            "thumbnail": None,
            "embedding_note": None,
        }
        embedding: list[float] | None = None
        model_run: Row | None = None
        with tempfile.TemporaryDirectory(prefix="images-") as workdir:
            local = Path(workdir) / "source"
            started = time.monotonic()
            try:
                get_object_store().download(file["s3_key"], local, file["size_bytes"])
            except ObjectTooLargeError as exc:
                raise PermanentJobError(f"Image {file['id']} changed after it was checked") from exc
            except StorageError as exc:
                raise TransientJobError(f"Download failed for image {file['id']}: {exc}") from exc
            timed("download", started)
            try:
                started = time.monotonic()
                image = load_rgb(local)
                timed("load", started)
            except ImageUnreadableError as exc:
                logger.warning("Image unreadable: file_id=%s error=%s", file["id"], exc)
                record.update(
                    status="skipped",
                    skip_reason=SKIP_UNREADABLE,
                    skip_message="This image couldn't be read.",
                )
                self._store(job, record, timings, None, None, model_id)
                return
            record.update(width=image.width, height=image.height)
            started = time.monotonic()
            record["phash"] = phash(image)
            record["colour"] = Jsonb(colour_signature(image))
            timed("features", started)

            started = time.monotonic()
            key = derived_key(file["owner_user_id"], file["batch_id"], _thumbnail_id(file["id"]))
            thumb_path = Path(workdir) / "thumbnail.jpg"
            thumb_path.write_bytes(
                thumbnail(image, settings["thumbnail_side"], settings["thumbnail_quality"])
            )
            try:
                get_object_store().upload(
                    key,
                    thumb_path,
                    "image/jpeg",
                    hashlib.sha256(thumb_path.read_bytes()).hexdigest(),
                )
            except StorageError as exc:
                raise TransientJobError(f"Thumbnail upload failed: {exc}") from exc
            record["thumbnail"] = key
            timed("thumbnail", started)

            started = time.monotonic()
            embedding, model_run, note = self._embed(job, record, image, settings, model_id)
            record["embedding_note"] = note
            timed("embed", started)
        self._store(job, record, timings, embedding, model_run, model_id)

    def _embed(
        self, job: Job, record: Row, image: Any, settings: Row, model_id: UUID
    ) -> tuple[list[float] | None, Row | None, str | None]:
        """Return the embedding and the record of its call, or a note saying why there's none."""
        if min(image.width, image.height) < settings["embed_min_side"]:
            return None, None, _NOTE_MESSAGES[NOTE_TOO_SMALL]
        with get_pool().connection() as conn:
            done = conn.execute(
                query("images", "existing_embedding"),
                {"image_id": record["id"], "model_id": model_id},
            ).fetchone()
        if done:
            # Embedded on an earlier attempt: no second call, and no second charge.
            return None, None, None
        try:
            check_budget()
        except LlmBudgetError:
            return None, None, _NOTE_MESSAGES[NOTE_BUDGET]
        config = get_embeddings_config()
        jpeg = for_embedding(image, settings["embed_max_side"])
        started = time.monotonic()
        try:
            vector = get_embedder().embed(jpeg)
        except EmbeddingUnavailableError as exc:
            logger.warning("Image not embedded: image_id=%s error=%s", record["id"], exc)
            return (
                None,
                self._run_row(job, record, config, started, "failed", str(exc)),
                (_NOTE_MESSAGES[NOTE_UNAVAILABLE]),
            )
        return vector, self._run_row(job, record, config, started, "succeeded", None), None

    def _run_row(
        self, job: Job, record: Row, config: Row, started: float, status: str, error: str | None
    ) -> Row:
        """The model_runs record of one embedding call, so its cost counts toward the cap."""
        digest = hashlib.sha256(
            f"{config['model_id']}:{config['dimension']}:{record['sha256']}".encode()
        ).hexdigest()
        return {
            "owner": record["owner"],
            "job_id": job.id,
            "task": EMBEDDING_TASK,
            "provider": config["provider"],
            "model_id": config["model_id"],
            "prompt_version": None,
            "input_hash": digest,
            "status": status,
            "response": None,
            "input_tokens": None,
            "output_tokens": None,
            "cost_usd": config["price_per_image"] if status == "succeeded" else None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": error[:1000] if error else None,
        }

    def _store(
        self,
        job: Job,
        record: Row,
        timings: dict[str, int],
        embedding: list[float] | None,
        model_run: Row | None,
        model_id: UUID,
    ) -> None:
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("images", "upsert_image"), {**record, "timings": Jsonb(timings)})
            run_id = None
            if model_run is not None:
                row = conn.execute(query("llm", "insert_run"), model_run).fetchone()
                run_id = row["id"] if row else None
            if embedding is not None:
                conn.execute(
                    query("images", "insert_embedding"),
                    {
                        "image_id": record["id"],
                        "model_id": model_id,
                        # pgvector reads this text form, so no extra driver is needed.
                        "embedding": "[" + ",".join(f"{value:.7g}" for value in embedding) + "]",
                        "model_run_id": run_id,
                    },
                )
        logger.info(
            "Image processed: image_id=%s status=%s embedded=%s timings=%s",
            record["id"],
            record["status"],
            embedding is not None,
            timings,
        )


class ImagesGroupHandler:
    """Groups the batch's near-duplicate images."""

    name = "images_group"

    def run(self, context: JobContext) -> JobOutcome:
        """Find near-duplicates and store the groups, each with its largest image first."""
        job = context.job
        batch_id = UUID(job.payload["batch_id"])
        settings = get_images_config()
        model_id = _model_id()
        with get_pool().connection() as conn:
            images = {
                row["id"]: row
                for row in conn.execute(
                    query("images", "batch_images"),
                    {"batch_id": batch_id, "owner": job.owner_user_id},
                ).fetchall()
                if row["status"] == "processed"
            }
            pairs = similar_pairs(
                conn,
                job.owner_user_id,
                batch_id,
                model_id,
                settings["duplicate_min_similarity"],
            )

        parent = {image: image for image in images}

        def root(image: UUID) -> UUID:
            while parent[image] != image:
                parent[image] = parent[parent[image]]
                image = parent[image]
            return image

        exact: set[UUID] = set()
        by_sha: dict[str, UUID] = {}
        for image, row in images.items():
            if row["sha256"] in by_sha:
                # The same pixels found twice, such as one picture inside two PDFs.
                parent[root(image)] = root(by_sha[row["sha256"]])
                exact.update({image, by_sha[row["sha256"]]})
            else:
                by_sha[row["sha256"]] = image
        for pair in pairs:
            a, b = pair["a"], pair["b"]
            if a not in images or b not in images:
                continue
            distance = colour_distance(images[a]["colour_signature"], images[b]["colour_signature"])
            if distance <= settings["duplicate_max_colour_distance"]:
                parent[root(a)] = root(b)

        groups: dict[UUID, list[UUID]] = {}
        for image in images:
            groups.setdefault(root(image), []).append(image)
        multi = [members for members in groups.values() if len(members) > 1]

        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("images", "clear_batch_groups"), {"batch_id": batch_id})
            for members in multi:
                kind = "exact" if all(member in exact for member in members) else "near"
                group = conn.execute(
                    query("images", "insert_group"), {"batch_id": batch_id, "kind": kind}
                ).fetchone()
                assert group is not None  # noqa: S101
                representative = max(
                    members,
                    key=lambda member: (
                        (images[member]["width"] or 0) * (images[member]["height"] or 0),
                        -images[member]["position"],
                        -images[member]["member_index"],
                    ),
                )
                conn.execute(
                    query("images", "set_group"),
                    {"group_id": group["id"], "representative": representative, "members": members},
                )
            conn.execute(
                query("images", "record_group_count"),
                {"run_id": job.pipeline_run_id, "groups": len(multi)},
            )
        logger.info(
            "Images grouped: batch_id=%s images=%d similar_pairs=%d groups=%d",
            batch_id,
            len(images),
            len(pairs),
            len(multi),
        )
        return JobOutcome()


register_handler(ImagesPlanHandler())
register_handler(ImagesProcessHandler())
register_handler(ImagesGroupHandler())
