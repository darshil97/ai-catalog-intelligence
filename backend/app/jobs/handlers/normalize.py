"""Normalization jobs (M6).

normalize_batch    payload {"batch_id"}: standard values for every observation, the value each
    product shows (or a conflict for review), and possible variants. Then one
    categorize_product chunk per product a person hasn't categorized.
categorize_product payload {"batch_id", "product_id"}: the LLM picks a category from the fixed
    tree; anything unsure, outside the tree, or "other" goes to review.

Originals are never changed: standard values are added beside them, under a rule version. A
person's decision always stands. Every job can run again safely.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.config import get_normalization_config
from app.db import get_pool, query
from app.jobs import queue
from app.jobs.base import JobContext, register_handler
from app.jobs.handlers.images import embedding_model_id
from app.jobs.models import ChunkSpec, JobOutcome
from app.llm import LlmBudgetError, LlmTaskError, run_task
from app.normalization import ProductSummary, Rules, choose, find_variants, normalize
from app.utils.logger import get_logger

logger = get_logger(__name__)

Row = dict[str, Any]

CATEGORY_TASK = "category"
OTHER = "other"
ROOT = "home-kitchen"


def _number(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


class NormalizeBatchHandler:
    """Normalizes a batch's values and chooses what each product shows."""

    name = "normalize_batch"

    def run(self, context: JobContext) -> JobOutcome:
        """Store standard values, product attributes, conflicts and variants."""
        job = context.job
        batch_id = UUID(job.payload["batch_id"])
        settings = get_normalization_config()
        rules = Rules(settings)
        version = settings["rule_version"]
        with get_pool().connection() as conn:
            attributes = {
                row["code"]: row
                for row in conn.execute(query("normalization", "attributes")).fetchall()
            }
            observations = conn.execute(
                query("normalization", "observations"), {"batch_id": batch_id}
            ).fetchall()
            products = conn.execute(
                query("normalization", "batch_products"), {"batch_id": batch_id}
            ).fetchall()

        currencies = self._product_currencies(rules, attributes, observations)
        ranks = settings["source_ranks"]
        last_rank = max(ranks.values(), default=0) + 1
        values = []
        for row in observations:
            context.check_interrupted()
            for normal in normalize(
                rules,
                attributes[row["code"]],
                row["raw_value"],
                header=row["header"],
                product_currency=currencies.get(row["product_id"]),
            ):
                values.append(
                    {
                        "observation_id": str(row["id"]),
                        "product_id": str(row["product_id"]),
                        "attribute_id": str(attributes[normal.attribute]["id"]),
                        "value_text": normal.value_text,
                        "value_number": _number(normal.value_number),
                        "unit": normal.unit,
                        "comparable": normal.comparable,
                        "display": normal.display,
                        "flags": list(normal.flags),
                        "source_rank": ranks.get(row["extractor"], last_rank),
                    }
                )

        order = {row["id"]: row["position"] for row in observations}
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            if values:
                conn.execute(
                    query("normalization", "insert_values"),
                    {"values": Jsonb(values), "rule_version": version},
                )
            stored = conn.execute(
                query("normalization", "batch_values"),
                {"batch_id": batch_id, "rule_version": version},
            ).fetchall()
            decided = {
                (row["product_id"], row["attribute_id"])
                for row in conn.execute(
                    query("normalization", "person_decided"), {"batch_id": batch_id}
                ).fetchall()
            }
            by_pair: dict[tuple[UUID, UUID], list[Row]] = defaultdict(list)
            for value in stored:
                by_pair[(value["product_id"], value["attribute_id"])].append(
                    {**value, "order": order.get(value["observation_id"], 0)}
                )
            rows = []
            states: dict[str, int] = defaultdict(int)
            for product in products:
                for attribute in attributes.values():
                    pair = (product["id"], attribute["id"])
                    if pair in decided:
                        continue
                    choice = choose(by_pair.get(pair, []), settings["agreement_tolerance"])
                    states[choice.state] += 1
                    rows.append(
                        {
                            "product_id": str(product["id"]),
                            "attribute_id": str(attribute["id"]),
                            "selected_value_id": str(choice.selected_id)
                            if choice.selected_id
                            else None,
                            "state": choice.state,
                        }
                    )
            if rows:
                conn.execute(
                    query("normalization", "upsert_attributes"), {"attributes": Jsonb(rows)}
                )
            conn.execute(
                query("normalization", "open_conflict_tasks"),
                {"batch_id": batch_id, "owner": job.owner_user_id},
            )
            conn.execute(query("normalization", "close_stale_conflicts"), {"batch_id": batch_id})
            pairs = self._variants(conn, batch_id, settings)
            conn.execute(query("normalization", "clear_flagged_variants"), {"batch_id": batch_id})
            if pairs:
                conn.execute(
                    query("normalization", "insert_variants"),
                    {"batch_id": batch_id, "pairs": Jsonb(pairs)},
                )
        logger.info(
            "Batch normalized: batch_id=%s observations=%d values=%d states=%s variants=%d",
            batch_id,
            len(observations),
            len(values),
            dict(states),
            len(pairs),
        )
        return JobOutcome(
            chunks=tuple(
                ChunkSpec(
                    key=f"category-{product['id']}",
                    job_type="categorize_product",
                    payload={"batch_id": str(batch_id), "product_id": str(product["id"])},
                )
                for product in products
                if settings["category"]["enabled"] and product["category_source"] != "person"
            )
        )

    def _product_currencies(
        self, rules: Rules, attributes: dict[str, Row], observations: list[Row]
    ) -> dict[UUID, str]:
        """Each product's currency, when its currency values agree on one."""
        found: dict[UUID, set[str]] = defaultdict(set)
        for row in observations:
            if row["code"] == "currency":
                for normal in normalize(rules, attributes["currency"], row["raw_value"]):
                    if normal.usable and normal.value_text:
                        found[row["product_id"]].add(normal.value_text)
        return {product: codes.pop() for product, codes in found.items() if len(codes) == 1}

    def _variants(self, conn: Any, batch_id: UUID, settings: dict[str, Any]) -> list[Row]:
        summaries = [
            ProductSummary(
                id=row["id"],
                key=row["product_key"],
                names=tuple(row["names"]),
                brands=tuple(row["brands"]),
            )
            for row in conn.execute(
                query("normalization", "product_summaries"), {"batch_id": batch_id}
            ).fetchall()
        ]
        similarity = {
            frozenset({row["a"], row["b"]}): float(row["similarity"])
            for row in conn.execute(
                query("normalization", "product_photo_similarity"),
                {"batch_id": batch_id, "model_id": embedding_model_id()},
            ).fetchall()
        }
        return [
            {"a": str(pair.a), "b": str(pair.b), "reason": pair.reasons}
            for pair in find_variants(summaries, settings["variants"], similarity)
        ]


class CategoryAnswer(BaseModel):
    """The LLM's choice of category."""

    category: str = Field(..., min_length=1, max_length=100, description="A category code.")
    confidence: float = Field(..., ge=0, le=1, description="How sure it is.")
    reason: str = Field("", max_length=500, description="A few words on why.")


def _category_parser(codes: set[str]) -> Any:
    def parse(data: dict[str, Any]) -> CategoryAnswer:
        answer = CategoryAnswer.model_validate(data)
        if answer.category not in codes:
            raise ValueError(
                f"{answer.category!r} is not a category code. Use one of the codes listed."
            )
        return answer

    return parse


def category_paths(categories: list[Row]) -> list[Row]:
    """Each choosable category with its full path, such as "Dining › Drinkware › Mugs"."""
    by_id = {row["id"]: row for row in categories}

    def path(row: Row) -> str:
        names = []
        current: Row | None = row
        while current is not None:
            if current["code"] != ROOT:
                names.append(current["name"])
            current = by_id.get(current["parent_id"])
        return " › ".join(reversed(names))

    return [
        {"id": row["id"], "code": row["code"], "path": path(row)}
        for row in categories
        if row["code"] != ROOT
    ]


class CategorizeProductHandler:
    """Chooses one product's category."""

    name = "categorize_product"

    def run(self, context: JobContext) -> JobOutcome:
        """Ask the LLM, and accept only a confident choice inside the tree."""
        job = context.job
        settings = get_normalization_config()
        with get_pool().connection() as conn:
            product: Row | None = conn.execute(
                query("normalization", "product_for_category"),
                {"product_id": job.payload["product_id"], "batch_id": job.payload["batch_id"]},
            ).fetchone()
            texts = {
                row["code"]: row["values"]
                for row in conn.execute(
                    query("normalization", "product_texts"),
                    {"product_id": job.payload["product_id"]},
                ).fetchall()
            }
            categories = category_paths(
                conn.execute(query("normalization", "categories")).fetchall()
            )
        if product is None or product["category_source"] == "person":
            return JobOutcome()
        labels = {
            "product_name": "Name",
            "brand": "Brand",
            "supplier_category": "Supplier's category",
            "material": "Material",
            "description": "Description",
        }
        details = [(labels[code], values[:3]) for code, values in texts.items() if code in labels]
        shown = {
            code: display
            for code, display in product["shown"].items()
            if code in ("capacity", "net_weight", "length", "width", "height", "color")
        }
        details += [
            (code.replace("_", " ").capitalize(), [display]) for code, display in shown.items()
        ]
        by_code = {row["code"]: row for row in categories}
        try:
            result = run_task(
                CATEGORY_TASK,
                owner=job.owner_user_id,
                job_id=job.id,
                variables={
                    "categories": categories,
                    "product_key": product["product_key"],
                    "details": details,
                },
                parse=_category_parser(set(by_code)),
                to_record=CategoryAnswer.model_dump,
            )
            answer: CategoryAnswer | None = result.value
            reason = answer.reason if answer else ""
        except LlmBudgetError:
            answer, reason = (
                None,
                "This month's LLM budget has been used, so no category was chosen.",
            )
        except LlmTaskError as exc:
            logger.warning("Category not chosen: product_id=%s error=%s", product["id"], exc)
            answer, reason = None, "The LLM couldn't be used, so no category was chosen."

        sure = (
            answer is not None
            and answer.category != OTHER
            and answer.confidence >= settings["category"]["min_confidence"]
        )
        suggested = by_code[answer.category]["id"] if answer else None
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(
                query("normalization", "set_category"),
                {
                    "product_id": product["id"],
                    "category_id": suggested if sure else None,
                    "suggested_id": suggested,
                    "state": "assigned" if sure else "needs_review",
                    "source": "llm",
                    "confidence": answer.confidence if answer else None,
                    "reason": reason[:500] or None,
                },
            )
            if not sure:
                conn.execute(
                    query("normalization", "open_category_task"),
                    {
                        "owner": job.owner_user_id,
                        "batch_id": product["batch_id"],
                        "product_id": product["id"],
                    },
                )
        logger.info(
            "Product categorized: product_id=%s category=%s confidence=%s sure=%s",
            product["id"],
            answer.category if answer else None,
            answer.confidence if answer else None,
            sure,
        )
        return JobOutcome()


register_handler(NormalizeBatchHandler())
register_handler(CategorizeProductHandler())
