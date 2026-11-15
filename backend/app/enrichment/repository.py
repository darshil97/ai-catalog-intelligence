"""Reading products' facts and generated text, shared by the API and the worker."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection

from app.config import get_enrichment_config, get_pipeline_config
from app.db import query
from app.enrichment.facts import FactSheet, build_facts
from app.jobs import queue
from app.jobs.models import QueueSettings, StageSpec

Row = dict[str, Any]

ENRICH_PIPELINE = "enrich"
ROOT_CATEGORY = "home-kitchen"

# What a product's text is now.
GENERATED = "generated"
OUT_OF_DATE = "out_of_date"
SKIPPED = "skipped"
FAILED = "failed"
NONE = "none"


@dataclass(frozen=True)
class ProductFacts:
    """A product, its current text's summary, and its facts now.

    Attributes:
        open_reviews: Review items about it still waiting for a decision. Copy written now
            would leave those values out, so automatic writing waits for them.
        changes: A person's decisions since its copy was written, in words, newest last.
    """

    product: Row
    sheet: FactSheet
    open_reviews: int = 0
    changes: tuple[str, ...] = ()

    @property
    def state(self) -> str:
        """generated, out_of_date, skipped, failed or none."""
        stored = self.product["enrichment_state"]
        if stored in (SKIPPED, FAILED):
            return str(stored)
        if self.product["input_fingerprint"] is None:
            return NONE
        if self.product["input_fingerprint"] != self.sheet.fingerprint:
            return OUT_OF_DATE
        return GENERATED

    @property
    def reasons(self) -> list[str]:
        """Why its copy is missing or needs rewriting, for people."""
        state = self.state
        if state == OUT_OF_DATE:
            if self.changes:
                return [f"Since the copy was written, {change}." for change in self.changes]
            return ["Values it uses have changed since the copy was written."]
        if state == NONE and self.open_reviews:
            return [
                f"Waiting for {self.open_reviews} review "
                f"{'item' if self.open_reviews == 1 else 'items'}: copy is written once "
                "they're decided, so it includes the values you settle."
            ]
        if state == NONE:
            return ["No copy has been written yet."]
        return []

    def needs_generation(self, max_versions: int) -> bool:
        """Whether generating now could change anything."""
        if self.product["versions"] >= max_versions:
            return False
        state = self.state
        if state == GENERATED:
            return False
        if state == SKIPPED:
            # Worth another look only if there's now enough to write about.
            return self.sheet.enough
        return True


def category_paths(conn: Connection[Any]) -> dict[UUID, str]:
    """Each category's full path, such as "Dining › Drinkware › Mugs and cups"."""
    rows = conn.execute(query("enrichment", "categories")).fetchall()
    by_id = {row["id"]: row for row in rows}

    def path(row: Row) -> str:
        names: list[str] = []
        current: Row | None = row
        while current is not None:
            if current["code"] != ROOT_CATEGORY:
                names.append(current["name"])
            current = by_id.get(current["parent_id"])
        return " › ".join(reversed(names))

    return {row["id"]: path(row) for row in rows}


def product_facts(
    conn: Connection[Any], owner: str, batch_id: UUID, product_id: UUID | None = None
) -> list[ProductFacts]:
    """A batch's products (or one), each with its facts as they are now. Six queries."""
    settings = get_enrichment_config()
    params = {"owner": owner, "batch_id": batch_id, "product_id": product_id}
    products = conn.execute(query("enrichment", "products"), params).fetchall()
    if not products:
        return []
    by_product: dict[UUID, list[Row]] = {}
    for row in conn.execute(query("enrichment", "attributes"), params).fetchall():
        by_product.setdefault(row["product_id"], []).append(row)
    paths = category_paths(conn)
    reviews: dict[UUID, int] = {}
    for row in conn.execute(query("validation", "open_reviews"), params).fetchall():
        reviews[row["product_id"]] = reviews.get(row["product_id"], 0) + 1
    changes: dict[UUID, list[Row]] = {}
    for row in conn.execute(query("enrichment", "changes"), params).fetchall():
        changes.setdefault(row["product_id"], []).append(row)
    out = []
    for product in products:
        category = {
            "path": paths.get(product["category_id"]) if product["category_id"] else None,
            "source": product["category_source"],
            "state": product["category_state"],
        }
        sheet = build_facts(by_product.get(product["id"], []), category, settings)
        written = product["generated_at"]
        since = [
            _describe(change)
            for change in changes.get(product["id"], [])
            if written is not None and change["created_at"] > written
        ]
        out.append(
            ProductFacts(
                product=product,
                sheet=sheet,
                open_reviews=reviews.get(product["id"], 0),
                changes=tuple(since),
            )
        )
    return out


def _describe(change: Row) -> str:
    """A decision in words, such as 'you set Capacity to "1.5 L"'."""
    if change["kind"] == "photo":
        return f"you confirmed the photo {change['what']}, which can add values"
    if change["kind"] == "category":
        return f'you chose the category "{change["value"]}"'
    value = change["value"]
    return f'you set {change["what"]} to "{value}"' if value else f"you settled {change['what']}"


def start_enrichment(
    conn: Connection[Any],
    owner: str,
    batch_id: UUID,
    product_ids: list[UUID],
    request_id: str | None,
    settings: QueueSettings,
) -> UUID:
    """Queue an enrich run for some of a batch's products. Uses no submission.

    Like photo values, the run isn't tied to the batch, so the batch's status is left alone.

    Raises:
        psycopg.errors.UniqueViolation: if the owner already has an active run.
    """
    payload = {"batch_id": str(batch_id), "product_ids": [str(p) for p in product_ids]}
    stages = [
        StageSpec(stage=stage["stage"], job_type=stage["job_type"], payload=payload)
        for stage in get_pipeline_config(ENRICH_PIPELINE)
    ]
    return queue.create_run(conn, ENRICH_PIPELINE, stages, owner, request_id, settings)


def latest_run(conn: Connection[Any], owner: str, batch_id: UUID) -> Row | None:
    """The batch's latest enrich run, with its job counts."""
    row: Row | None = conn.execute(
        query("enrichment", "latest_run"), {"owner": owner, "batch_id": batch_id}
    ).fetchone()
    return row


def current_content(conn: Connection[Any], owner: str, product_id: UUID) -> Row | None:
    """A product's current generated text, or None."""
    row: Row | None = conn.execute(
        query("enrichment", "current_content"), {"owner": owner, "product_id": product_id}
    ).fetchone()
    return row


def open_reviews(conn: Connection[Any], owner: str, batch_id: UUID) -> int:
    """How many conflict and category decisions the batch is waiting for."""
    row = conn.execute(
        query("enrichment", "open_reviews"), {"owner": owner, "batch_id": batch_id}
    ).fetchone()
    return int(row["count"]) if row else 0
