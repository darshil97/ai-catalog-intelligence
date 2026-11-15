"""Enrichment jobs (M7, D37).

enrich_plan     payload {"batch_id", "product_ids"}: one enrich_product chunk per listed
    product whose text could change. Without "product_ids" it's the last stage of processing
    (ingest or photo_values): every product, and only when enrichment.auto is on.
enrich_product  payload {"batch_id", "product_id"}: write the product's title, description and
    bullets from its settled facts, check every claim, and store a new version.

Reaching the monthly LLM spend cap pauses a run someone started (QuotaBlockedError): nothing is
lost, and a person resumes it later. During processing it doesn't pause, which would leave the
batch processing for the rest of the month: the product says so, and can be generated later.
A stored reply is reused before the cap is checked, so a resumed or
retried job never pays twice. Every job can run again safely.
"""

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.config import get_enrichment_config, get_normalization_config
from app.db import get_pool, query
from app.enrichment import repository
from app.enrichment.facts import Fact
from app.enrichment.grounding import Checked, Grounding
from app.enrichment.models import EnrichmentReply
from app.jobs import queue
from app.jobs.base import JobContext, register_handler
from app.jobs.errors import QuotaBlockedError
from app.jobs.models import ChunkSpec, JobOutcome
from app.llm import LlmBudgetError, LlmTaskError, run_task
from app.utils.logger import get_logger

logger = get_logger(__name__)

ENRICHMENT_TASK = "enrichment"
SPEND_CAP = "llm_spend_cap"
_MAX_MESSAGE = 1000


class EnrichPlanHandler:
    """Chooses which of the batch's products to write text for."""

    name = "enrich_plan"

    def run(self, context: JobContext) -> JobOutcome:
        """One chunk per product whose text is missing, failed or out of date."""
        job = context.job
        batch_id = UUID(job.payload["batch_id"])
        auto = "product_ids" not in job.payload
        wanted = {UUID(p) for p in job.payload.get("product_ids") or []}
        settings = get_enrichment_config()
        if auto and not settings["auto"]:
            return JobOutcome()
        with get_pool().connection() as conn:
            found = repository.product_facts(conn, job.owner_user_id, batch_id)
        chosen = [
            item.product["id"]
            for item in found
            if (not wanted or item.product["id"] in wanted)
            and item.needs_generation(settings["max_versions_per_product"])
        ]
        logger.info(
            "Enrichment planned: batch_id=%s products=%d chosen=%d auto=%s",
            batch_id,
            len(found),
            len(chosen),
            auto,
        )
        return JobOutcome(
            chunks=tuple(
                ChunkSpec(
                    key=f"product-{product_id}",
                    job_type="enrich_product",
                    payload={
                        "batch_id": str(batch_id),
                        "product_id": str(product_id),
                        "auto": auto,
                    },
                )
                for product_id in chosen
            )
        )


def _parser(grounding: Grounding, facts: tuple[Fact, ...]) -> Any:
    def parse(data: dict[str, Any]) -> tuple[EnrichmentReply, Checked]:
        reply = EnrichmentReply.model_validate(data)
        return reply, grounding.check(reply, facts)

    return parse


def _failure_message(exc: LlmTaskError) -> str:
    text = str(exc)
    if text.startswith("LlmUnavailableError"):
        return "The LLM couldn't be reached. Regenerate to try again."
    if text.startswith("ValueError: "):
        text = text[len("ValueError: ") :]
    return f"The text didn't pass the checks, so nothing was kept: {text}"[:_MAX_MESSAGE]


class EnrichProductHandler:
    """Writes one product's text and stores it if every claim checks out."""

    name = "enrich_product"

    def run(self, context: JobContext) -> JobOutcome:
        """Generate, check and store one version, or record why not."""
        job = context.job
        batch_id = UUID(job.payload["batch_id"])
        product_id = UUID(job.payload["product_id"])
        settings = get_enrichment_config()
        with get_pool().connection() as conn:
            found = repository.product_facts(conn, job.owner_user_id, batch_id, product_id)
        if not found:
            logger.info("Enrichment skipped: product_id=%s reason=product_gone", product_id)
            return JobOutcome()
        item = found[0]
        sheet = item.sheet
        if item.state == repository.GENERATED:
            # Already written from these facts, for example before a pause or a lost lease.
            logger.info("Enrichment skipped: product_id=%s reason=up_to_date", product_id)
            return JobOutcome()
        if item.product["versions"] >= settings["max_versions_per_product"]:
            self._set_state(
                job,
                product_id,
                repository.FAILED,
                f"This product has reached the limit of {settings['max_versions_per_product']} "
                "versions.",
            )
            return JobOutcome()
        if not sheet.enough:
            self._set_state(
                job,
                product_id,
                repository.SKIPPED,
                "Not enough verified information: it needs a settled name, or a brand and a "
                "category.",
            )
            return JobOutcome()

        context.check_interrupted()
        grounding = Grounding(settings, get_normalization_config())
        try:
            result = run_task(
                ENRICHMENT_TASK,
                owner=job.owner_user_id,
                job_id=job.id,
                variables={
                    "product_key": item.product["product_key"],
                    "facts": [
                        {"id": fact.id, "name": fact.name, "display": fact.display}
                        for fact in sheet.facts
                    ],
                    "title_only": [fact.id for fact in sheet.facts if fact.title_only],
                    **{
                        key: settings[key]
                        for key in (
                            "title_max_chars",
                            "bullets_min",
                            "bullets_max",
                            "bullet_max_chars",
                            "sentences_min",
                            "sentences_max",
                            "description_max_chars",
                        )
                    },
                },
                parse=_parser(grounding, sheet.facts),
                to_record=lambda value: value[0].model_dump(),
            )
        except LlmBudgetError as exc:
            if job.payload.get("auto"):
                self._set_state(
                    job,
                    product_id,
                    repository.FAILED,
                    "Not written yet: this month's LLM budget is used up. Generate it once "
                    "there's budget again.",
                )
                return JobOutcome()
            raise QuotaBlockedError(
                SPEND_CAP, f"This month's LLM budget is used up: {exc}. Resume once it's raised."
            ) from exc
        except LlmTaskError as exc:
            logger.warning("Enrichment failed: product_id=%s error=%s", product_id, exc)
            self._set_state(job, product_id, repository.FAILED, _failure_message(exc))
            return JobOutcome()

        checked = result.value[1]
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("enrichment", "lock_product"), {"product_id": product_id})
            conn.execute(query("enrichment", "clear_current"), {"product_id": product_id})
            stored = conn.execute(
                query("enrichment", "insert_content"),
                {
                    "product_id": product_id,
                    "batch_id": batch_id,
                    "owner": job.owner_user_id,
                    "title": checked.title,
                    "description": checked.description,
                    "bullets": list(checked.bullets),
                    "claims": Jsonb(list(checked.claims)),
                    "cited": list(checked.cited_attribute_ids),
                    "fingerprint": sheet.fingerprint,
                    "rules_version": settings["rules_version"],
                    "model_run_id": result.model_run_id,
                },
            ).fetchone()
            conn.execute(
                query("enrichment", "set_state"),
                {"product_id": product_id, "state": repository.GENERATED, "message": None},
            )
        logger.info(
            "Product enriched: product_id=%s version=%s facts=%d claims=%d calls=%d",
            product_id,
            stored["version"] if stored else None,
            len(sheet.facts),
            len(checked.claims),
            result.calls,
        )
        return JobOutcome()

    def _set_state(self, job: Any, product_id: UUID, state: str, message: str) -> None:
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(
                query("enrichment", "set_state"),
                {"product_id": product_id, "state": state, "message": message[:_MAX_MESSAGE]},
            )
        logger.info("Enrichment state set: product_id=%s state=%s", product_id, state)


register_handler(EnrichPlanHandler())
register_handler(EnrichProductHandler())
