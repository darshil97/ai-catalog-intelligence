"""Association jobs: which product each image shows, and values from photos accepted for one.

associate_images  payload {"batch_id"}: score every image against every product, auto-accept
    unambiguous code matches, open review tasks for the rest, and queue photo_value chunks.
photo_values_plan payload {"batch_id"}: queue photo_value chunks for accepted photos not yet
    read. Run on its own after a person accepts photos, outside the batch's processing run.
photo_value       payload {"batch_id", "candidate_id"}: read one accepted photo's values for
    its product, with M4's quote check.

A person's decision always stands: association recomputes scores, evidence and the system's
own decisions, never theirs. Every job can run again safely.
"""

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.config import get_documents_config, get_extraction_config, get_matching_config
from app.db import get_pool, query
from app.images import similar_pairs
from app.ingest.documents.values import DocumentAnswer, check_link, parse_answer
from app.jobs import queue
from app.jobs.base import JobContext, register_handler
from app.jobs.handlers.documents import page_from_raw
from app.jobs.handlers.images import embedding_model_id
from app.jobs.models import ChunkSpec, Job, JobOutcome
from app.llm import LlmBudgetError, LlmTaskError, run_task
from app.matching import Context, ImageFacts, ProductFacts, associate
from app.utils.logger import get_logger

logger = get_logger(__name__)

Row = dict[str, Any]

VALUES_TASK = "document_attributes"


def _photo_chunks(batch_id: str, candidate_ids: list[UUID]) -> tuple[ChunkSpec, ...]:
    return tuple(
        ChunkSpec(
            key=f"photo-{candidate_id}",
            job_type="photo_value",
            payload={"batch_id": batch_id, "candidate_id": str(candidate_id)},
        )
        for candidate_id in candidate_ids
    )


class AssociateImagesHandler:
    """Decides, for one batch, which product each image shows."""

    name = "associate_images"

    def run(self, context: JobContext) -> JobOutcome:
        """Store candidates, accepted links and review tasks, then queue photo values."""
        job = context.job
        batch_id = UUID(job.payload["batch_id"])
        settings = get_matching_config()
        identifiers = get_extraction_config()["identifier_attributes"]
        model_id = embedding_model_id()
        with get_pool().connection() as conn:
            image_rows = conn.execute(
                query("matching", "images_for_matching"), {"batch_id": batch_id}
            ).fetchall()
            product_rows = conn.execute(
                query("matching", "products_for_matching"),
                {"batch_id": batch_id, "identifiers": identifiers},
            ).fetchall()
            human = {
                (row["image_id"], row["product_id"]): row["decision"]
                for row in conn.execute(
                    query("matching", "human_decisions"), {"batch_id": batch_id}
                ).fetchall()
            }
            pairs = similar_pairs(
                conn, job.owner_user_id, batch_id, model_id, settings["visual_min_similarity"]
            )

        images = [
            ImageFacts(
                id=row["id"],
                name=row["name"],
                path=row["path"],
                text=row["text"],
                page_products=frozenset(row["page_products"]),
                duplicate_group=row["duplicate_group_id"],
            )
            for row in image_rows
        ]
        products = [
            ProductFacts(
                id=row["id"],
                key=row["product_key"],
                codes=tuple(sorted({row["product_key"], *row["codes"]})),
                words=tuple(row["words"]),
            )
            for row in product_rows
        ]
        match_context = Context(
            settings=settings,
            product_count=len(products),
            similarity={
                frozenset({pair["a"], pair["b"]}): float(pair["similarity"]) for pair in pairs
            },
            groups={image.id: image.duplicate_group for image in images},
            names={image.id: image.name for image in images},
        )
        candidates = associate(images, products, match_context, human) if products else []

        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("matching", "clear_system_candidates"), {"batch_id": batch_id})
            if candidates:
                conn.execute(
                    query("matching", "upsert_candidates"),
                    {
                        "batch_id": batch_id,
                        "candidates": Jsonb(
                            [
                                {
                                    "image_id": str(c.image_id),
                                    "product_id": str(c.product_id),
                                    "score": c.score,
                                    "signals": c.signals_json(),
                                    "decision": c.decision,
                                }
                                for c in candidates
                            ]
                        ),
                    },
                )
            conn.execute(query("matching", "clear_product_images"), {"batch_id": batch_id})
            conn.execute(query("matching", "rebuild_product_images"), {"batch_id": batch_id})
            conn.execute(
                query("matching", "open_review_tasks"),
                {"batch_id": batch_id, "owner": job.owner_user_id},
            )
            conn.execute(query("matching", "close_stale_tasks"), {"batch_id": batch_id})
            conn.execute(
                query("matching", "record_match_counts"),
                {"batch_id": batch_id, "run_id": job.pipeline_run_id},
            )
            pending = [
                row["id"]
                for row in conn.execute(
                    query("matching", "photos_needing_values"), {"batch_id": batch_id}
                ).fetchall()
            ]
        decisions: dict[str, int] = {}
        for candidate in candidates:
            decisions[candidate.decision] = decisions.get(candidate.decision, 0) + 1
        logger.info(
            "Images associated: batch_id=%s images=%d products=%d decisions=%s photo_values=%d",
            batch_id,
            len(images),
            len(products),
            decisions,
            len(pending),
        )
        return JobOutcome(chunks=_photo_chunks(str(batch_id), pending))


class PhotoValuesPlanHandler:
    """Queues values for photos accepted since processing."""

    name = "photo_values_plan"

    def run(self, context: JobContext) -> JobOutcome:
        """Return a photo_value chunk for each accepted photo not yet read."""
        batch_id = context.job.payload["batch_id"]
        with get_pool().connection() as conn:
            pending = [
                row["id"]
                for row in conn.execute(
                    query("matching", "photos_needing_values"), {"batch_id": batch_id}
                ).fetchall()
            ]
        logger.info("Photo values planned: batch_id=%s photos=%d", batch_id, len(pending))
        return JobOutcome(chunks=_photo_chunks(batch_id, pending))


class PhotoValueHandler:
    """Reads one accepted photo's values for its product."""

    name = "photo_value"

    def run(self, context: JobContext) -> JobOutcome:
        """Read the photo's OCR text with the LLM, keeping only values quoted from it."""
        job = context.job
        settings = get_documents_config()
        identifiers = get_extraction_config()["identifier_attributes"]
        with get_pool().connection() as conn:
            photo: Row | None = conn.execute(
                query("matching", "photo_for_values"),
                {"candidate_id": job.payload["candidate_id"], "batch_id": job.payload["batch_id"]},
            ).fetchone()
            product_codes = (
                conn.execute(
                    query("matching", "products_for_matching"),
                    {"batch_id": job.payload["batch_id"], "identifiers": identifiers},
                ).fetchall()
                if photo
                else []
            )
            attributes = conn.execute(query("extraction", "attributes")).fetchall()
        if photo is None or photo["decision"] not in ("auto_accepted", "accepted"):
            return JobOutcome()
        if photo["values_read_at"] is not None:
            return JobOutcome()
        if photo["already_read"] or photo["record_id"] is None:
            # Its values were read when processing made the product from it, or it has no
            # text: there is nothing more to read.
            self._mark_read(job, photo)
            return JobOutcome()

        page = page_from_raw(None, photo["raw_data"])
        codes = next(
            (sorted({row["product_key"], *row["codes"]}) for row in product_codes
             if row["id"] == photo["product_id"]),
            [photo["product_key"]],
        )  # fmt: skip
        variables = {
            "attributes": [
                {
                    "code": row["code"],
                    "data_type": row["data_type"],
                    "description": row["description"],
                }
                for row in attributes
            ],
            "mode": "link",
            "products": [photo["product_key"]],
            "source": f"The photo {photo['original_name']}, read by OCR. A person confirmed it "
            f"shows the product {photo['product_key']}.",
            "truncated": len(page.text) > settings["max_llm_chars"],
            "text": page.text[: settings["max_llm_chars"]],
        }
        try:
            result = run_task(
                VALUES_TASK,
                owner=job.owner_user_id,
                job_id=job.id,
                variables=variables,
                parse=parse_answer,
                to_record=DocumentAnswer.model_dump,
            )
        except LlmBudgetError:
            logger.warning("Photo values not read: candidate_id=%s reason=budget", photo["id"])
            return JobOutcome()
        except LlmTaskError as exc:
            logger.warning("Photo values not read: candidate_id=%s error=%s", photo["id"], exc)
            return JobOutcome()
        checked = check_link(
            page,
            result.value,
            {str(photo["product_id"]): codes},
            {row["code"] for row in attributes},
            settings["min_key_chars_for_loose_match"],
        )
        attribute_ids = {row["code"]: str(row["id"]) for row in attributes}
        observations = [
            {
                "product_id": str(photo["product_id"]),
                "attribute_id": attribute_ids[kept.attribute],
                "raw_value": kept.found.raw_value,
                "source_file_id": str(photo["source_file_id"]),
                "source_record_id": str(photo["record_id"]),
                "location": {
                    "page": None,
                    "method": page.method,
                    "quote": kept.found.quote,
                    "bbox": [round(v, 1) for v in kept.found.box] if kept.found.box else None,
                    "page_width": round(page.width, 1),
                    "page_height": round(page.height, 1),
                },
                "extractor": settings["extractor_ocr"],
                "model_run_id": str(result.model_run_id),
                "confidence": round(page.confidence, 3) if page.confidence is not None else None,
            }
            for kept in checked.kept
        ]
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            if observations:
                conn.execute(
                    query("documents", "insert_observations"),
                    {"observations": Jsonb(observations)},
                )
            conn.execute(query("matching", "mark_values_read"), {"candidate_id": photo["id"]})
        logger.info(
            "Photo values read: candidate_id=%s kept=%d dropped=%d",
            photo["id"],
            len(checked.kept),
            len(checked.dropped),
        )
        return JobOutcome()

    def _mark_read(self, job: Job, photo: Row) -> None:
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("matching", "mark_values_read"), {"candidate_id": photo["id"]})


register_handler(AssociateImagesHandler())
register_handler(PhotoValuesPlanHandler())
register_handler(PhotoValueHandler())
