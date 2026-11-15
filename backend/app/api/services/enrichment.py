"""Generated titles, descriptions and bullets: starting, resuming and reading them (D37).

The API never calls the LLM. It queues runs, and reads what the worker stored.
"""

from typing import Any
from uuid import UUID

import psycopg

from app import catalog
from app.api.services.common import database, http_error, not_found
from app.auth import Profile
from app.compute.starter import request_worker_start
from app.config import get_enrichment_config, get_processing_config
from app.enrichment import repository
from app.jobs import queue
from app.jobs.settings import get_queue_settings
from app.usage import llm_budget_reached
from app.utils.logger import get_logger
from app.utils.request_id import get_request_id

logger = get_logger(__name__)

_BUDGET_MESSAGE = (
    "This month's LLM budget has been used, so no text can be written until it's raised or "
    "the month ends."
)


def _active_run_error() -> Exception:
    return http_error(
        409, "active_run", "Another of your runs is in progress. Try again when it finishes."
    )


def _require_processing() -> None:
    if not get_processing_config()["enabled"]:
        raise http_error(503, "processing_disabled", "Processing is turned off for now.")


def batch_enrichment(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return every product's text state and the batch's latest enrich run, or 404."""
    max_versions = get_enrichment_config()["max_versions_per_product"]
    try:
        with database("batch_enrichment") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            found = repository.product_facts(conn, profile.user_id, batch_id)
            run = repository.latest_run(conn, profile.user_id, batch_id)
            reviews = repository.open_reviews(conn, profile.user_id, batch_id)
            budget = llm_budget_reached(conn)
    except catalog.NotFoundError:
        not_found()
    items = [
        {
            "product_id": item.product["id"],
            "product_key": item.product["product_key"],
            "state": item.state,
            "message": item.product["enrichment_message"]
            if item.state in (repository.SKIPPED, repository.FAILED)
            else None,
            "version": item.product["version"],
            "title": item.product["title"],
            "facts": len(item.sheet.facts),
            "left_out": len(item.sheet.left_out),
            "can_generate": item.needs_generation(max_versions) and not item.edited,
            "edited": item.edited,
            "open_reviews": item.open_reviews,
            "reasons": item.reasons,
        }
        for item in found
    ]
    return {
        "batch_id": batch_id,
        "open_reviews": reviews,
        "can_generate": sum(1 for item in items if item["can_generate"]),
        "budget_reached": budget,
        "run": run,
        "items": items,
    }


def start(profile: Profile, batch_id: UUID, product_ids: list[UUID] | None) -> dict[str, Any]:
    """Start an enrich run. Uses no submission.

    Raises:
        HTTPException: 404; 409 batch_not_ready, llm_budget_reached or active_run; 422
            nothing_to_generate or not_in_batch; 503.
    """
    _require_processing()
    max_versions = get_enrichment_config()["max_versions_per_product"]
    try:
        with database("start_enrichment") as conn:
            batch = catalog.get_batch(conn, profile.user_id, batch_id)
            if batch["status"] != "completed":
                raise http_error(
                    409, "batch_not_ready", "Text can be written once the batch has been processed."
                )
            if llm_budget_reached(conn):
                raise http_error(409, "llm_budget_reached", _BUDGET_MESSAGE)
            found = repository.product_facts(conn, profile.user_id, batch_id)
            in_batch = {item.product["id"] for item in found}
            if product_ids and not set(product_ids) <= in_batch:
                raise http_error(
                    422, "not_in_batch", "Some of those products aren't in this batch."
                )
            wanted = set(product_ids or in_batch)
            chosen = [
                item.product["id"]
                for item in found
                if item.product["id"] in wanted
                and item.needs_generation(max_versions)
                # Copy a person wrote is replaced only when that product is asked for by name.
                and (product_ids or not item.edited)
            ]
            if not chosen:
                raise http_error(
                    422,
                    "nothing_to_generate",
                    "Every chosen product's text is up to date, or there isn't enough settled "
                    "information to write it.",
                )
            run_id = repository.start_enrichment(
                conn, profile.user_id, batch_id, chosen, get_request_id(), get_queue_settings()
            )
    except catalog.NotFoundError:
        not_found()
    except psycopg.errors.UniqueViolation as exc:
        raise _active_run_error() from exc
    logger.info(
        "Enrichment queued: batch_id=%s run_id=%s products=%d", batch_id, run_id, len(chosen)
    )
    request_worker_start()
    return {"run_id": run_id, "products": len(chosen)}


def resume(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Resume the batch's paused enrich run once the budget allows.

    Raises:
        HTTPException: 404; 409 not_paused, llm_budget_reached or active_run; 503.
    """
    _require_processing()
    try:
        with database("resume_enrichment") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            run = repository.latest_run(conn, profile.user_id, batch_id)
            if run is None or run["status"] != "paused":
                raise http_error(409, "not_paused", "There's no paused run to resume.")
            if llm_budget_reached(conn):
                raise http_error(409, "llm_budget_reached", _BUDGET_MESSAGE)
            count = queue.resume_run(conn, run["id"])
            if count is None:
                raise http_error(409, "not_paused", "There's no paused run to resume.")
    except catalog.NotFoundError:
        not_found()
    except psycopg.errors.UniqueViolation as exc:
        raise _active_run_error() from exc
    logger.info("Enrichment resumed: batch_id=%s run_id=%s jobs=%d", batch_id, run["id"], count)
    request_worker_start()
    return {"run_id": run["id"], "products": count}


def product_content(conn: Any, profile: Profile, product: dict[str, Any]) -> dict[str, Any]:
    """A product's current text, its state and what was left out, for the product page."""
    max_versions = get_enrichment_config()["max_versions_per_product"]
    found = repository.product_facts(conn, profile.user_id, product["batch_id"], product["id"])
    content = repository.current_content(conn, profile.user_id, product["id"])
    item = found[0] if found else None
    out: dict[str, Any] = {
        "version": None,
        "state": item.state if item else repository.NONE,
        "message": item.product["enrichment_message"]
        if item and item.state in (repository.SKIPPED, repository.FAILED)
        else None,
        "left_out": [
            {"code": x.code, "name": x.name, "state": x.state, "reason": x.reason}
            for x in (item.sheet.left_out if item else ())
        ],
        "can_generate": item.needs_generation(max_versions) if item else False,
        "edited": item.edited if item else False,
        "open_reviews": item.open_reviews if item else 0,
        "reasons": item.reasons if item else [],
    }
    if content:
        out.update(
            {
                "version": content["version"],
                "title": content["title"],
                "description": content["description"],
                "bullets": content["bullets"],
                "claims": content["claims"],
                "status": content["status"],
                "model_id": content["model_id"],
                "prompt_version": content["prompt_version"],
                "rules_version": content["rules_version"],
                "created_at": content["created_at"],
            }
        )
    return out


def start_after_review(profile: Profile, batch_id: UUID) -> str:
    """Write copy once a batch's review is done (D37, amended). Never raises.

    Returns:
        queued, waiting (another run is active, or the budget is used up), or not_needed
        (review items remain, automatic copy is off, or no product needs copy).
    """
    if not get_enrichment_config()["auto"] or not get_processing_config()["enabled"]:
        return "not_needed"
    max_versions = get_enrichment_config()["max_versions_per_product"]
    try:
        with database("copy_after_review") as conn:
            found = repository.product_facts(conn, profile.user_id, batch_id)
            if any(item.open_reviews for item in found):
                return "not_needed"
            chosen = [
                item.product["id"]
                for item in found
                if item.needs_generation(max_versions) and not item.edited
            ]
            if not chosen:
                return "not_needed"
            if llm_budget_reached(conn):
                return "waiting"
            run_id = repository.start_enrichment(
                conn, profile.user_id, batch_id, chosen, get_request_id(), get_queue_settings()
            )
    except psycopg.errors.UniqueViolation:
        logger.info("Copy after review waiting: reason=active_run batch_id=%s", batch_id)
        return "waiting"
    except Exception:  # noqa: BLE001 - the decision is saved; copy can be generated by hand
        logger.error("Copy after review not queued: batch_id=%s", batch_id, exc_info=True)
        return "waiting"
    logger.info(
        "Copy queued after review: batch_id=%s run_id=%s products=%d",
        batch_id,
        run_id,
        len(chosen),
    )
    request_worker_start()
    return "queued"
