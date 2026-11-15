"""Review tasks and their decisions, with every change recorded in review_events.

Functions take an open connection and run inside the caller's transaction, so a decision, its
links and its audit record commit together. Every query is scoped to the owner.
"""

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.config import get_pipeline_config
from app.db import query
from app.jobs import queue
from app.jobs.models import QueueSettings, StageSpec
from app.matching import ACCEPTED, REJECTED

Row = dict[str, Any]

DECISION_ACCEPT = "accept"
DECISION_REJECT = "reject"


class TaskNotFoundError(Exception):
    """The owner has no such task."""


class TaskResolvedError(Exception):
    """The task was already decided."""


class NotACandidateError(Exception):
    """The product chosen isn't one of the image's candidates."""


def list_tasks(conn: Connection[Any], owner: str, batch_id: UUID, limit: int) -> list[Row]:
    """Return a batch's image-match tasks, open first, each with its candidates."""
    tasks = conn.execute(
        query("matching", "list_review_tasks"),
        {"owner": owner, "batch_id": batch_id, "limit": limit},
    ).fetchall()
    if not tasks:
        return []
    candidates = conn.execute(
        query("matching", "task_candidates"), {"image_ids": [task["image_id"] for task in tasks]}
    ).fetchall()
    by_image: dict[UUID, list[Row]] = {}
    for candidate in candidates:
        by_image.setdefault(candidate["image_id"], []).append(candidate)
    return [{**task, "candidates": by_image.get(task["image_id"], [])} for task in tasks]


def _event(
    conn: Connection[Any],
    actor: str,
    batch_id: UUID,
    candidate: Row,
    after: str,
) -> None:
    conn.execute(
        query("matching", "insert_event"),
        {
            "actor": actor,
            "batch_id": batch_id,
            "entity_type": "image_match_candidate",
            "entity_id": candidate["id"],
            "action": "decide",
            "before": Jsonb(
                {"decision": candidate["decision"], "decided_by": candidate["decided_by"]}
            ),
            "after": Jsonb({"decision": after, "decided_by": actor}),
        },
    )


def decide(
    conn: Connection[Any],
    owner: str,
    task_id: UUID,
    decision: str,
    product_id: UUID | None,
) -> Row:
    """Accept one product for the task's image, or reject every candidate.

    Accepting a product rejects the image's other candidates. Each change is recorded with its
    before and after values, and the image's accepted links are rebuilt.

    Returns:
        {"batch_id", "image_id", "accepted_product_id"}.

    Raises:
        TaskNotFoundError: if the owner has no such task.
        TaskResolvedError: if it was already decided.
        NotACandidateError: if the product isn't one of the image's candidates.
    """
    task: Row | None = conn.execute(
        query("matching", "lock_task"), {"task_id": task_id, "owner": owner}
    ).fetchone()
    if task is None:
        raise TaskNotFoundError(str(task_id))
    if task["status"] != "open":
        raise TaskResolvedError(str(task_id))
    candidates = conn.execute(
        query("matching", "lock_image_candidates"), {"image_id": task["image_id"]}
    ).fetchall()
    if decision == DECISION_ACCEPT and not any(c["product_id"] == product_id for c in candidates):
        raise NotACandidateError(str(product_id))

    for candidate in candidates:
        chosen = decision == DECISION_ACCEPT and candidate["product_id"] == product_id
        after = ACCEPTED if chosen else REJECTED
        if candidate["decision"] == after and candidate["decided_by"] == owner:
            continue
        conn.execute(
            query("matching", "set_decision"),
            {"decision": after, "actor": owner, "candidate_id": candidate["id"]},
        )
        _event(conn, owner, task["batch_id"], candidate, after)
    conn.execute(query("matching", "resolve_task"), {"task_id": task_id})
    conn.execute(query("matching", "clear_product_images"), {"batch_id": task["batch_id"]})
    conn.execute(query("matching", "rebuild_product_images"), {"batch_id": task["batch_id"]})
    return {
        "batch_id": task["batch_id"],
        "image_id": task["image_id"],
        "accepted_product_id": product_id if decision == DECISION_ACCEPT else None,
    }


def list_matches(conn: Connection[Any], owner: str, batch_id: UUID, limit: int) -> list[Row]:
    """Return every candidate of a batch, with its evidence. Empty if not the owner's."""
    return conn.execute(
        query("matching", "list_matches"), {"owner": owner, "batch_id": batch_id, "limit": limit}
    ).fetchall()


def product_images(conn: Connection[Any], owner: str, product_id: UUID) -> list[Row]:
    """Return a product's accepted images, in file order."""
    return conn.execute(
        query("matching", "product_images"), {"owner": owner, "product_id": product_id}
    ).fetchall()


def pending_photo_values(conn: Connection[Any], owner: str, batch_id: UUID) -> int:
    """Return how many accepted photos haven't been read for values yet."""
    row = conn.execute(
        query("matching", "pending_photo_values"), {"owner": owner, "batch_id": batch_id}
    ).fetchone()
    return int(row["pending"]) if row else 0


PHOTO_VALUES_PIPELINE = "photo_values"


def start_photo_values(
    conn: Connection[Any],
    owner: str,
    batch_id: UUID,
    request_id: str | None,
    settings: QueueSettings,
) -> UUID:
    """Queue a run that reads values from the batch's accepted photos. Uses no submission.

    The run isn't tied to the batch, so the batch's status and its processing run are left as
    they are, and a failure here can't mark the batch failed.

    Raises:
        psycopg.errors.UniqueViolation: if the owner already has an active run.
    """
    payload = {"batch_id": str(batch_id)}
    stages = [
        StageSpec(stage=stage["stage"], job_type=stage["job_type"], payload=payload)
        for stage in get_pipeline_config(PHOTO_VALUES_PIPELINE)
    ]
    return queue.create_run(conn, PHOTO_VALUES_PIPELINE, stages, owner, request_id, settings)


KIND_IMAGE_MATCH = "image_match"
KIND_ATTRIBUTE_CONFLICT = "attribute_conflict"
KIND_CATEGORY = "category"
DECISION_CHOOSE = "choose"
DECISION_SET = "set"


class NotAChoiceError(Exception):
    """The value or category chosen isn't one the task offers."""


def task_kind(conn: Connection[Any], owner: str, task_id: UUID) -> str:
    """Return a task's kind.

    Raises:
        TaskNotFoundError: if the owner has no such task.
    """
    row = conn.execute(
        query("normalization", "lock_other_task"), {"task_id": task_id, "owner": owner}
    ).fetchone()
    if row is None:
        raise TaskNotFoundError(str(task_id))
    return str(row["kind"])


def list_other_tasks(
    conn: Connection[Any], owner: str, batch_id: UUID, limit: int, rule_version: str
) -> list[Row]:
    """Return a batch's conflict and category tasks, each with what can be chosen."""
    tasks = conn.execute(
        query("normalization", "list_other_tasks"),
        {"owner": owner, "batch_id": batch_id, "limit": limit},
    ).fetchall()
    out = []
    for task in tasks:
        extra: Row = {"values": [], "category": None}
        if task["kind"] == KIND_ATTRIBUTE_CONFLICT:
            extra["values"] = [
                value
                for value in conn.execute(
                    query("normalization", "product_value_list"),
                    {
                        "owner": owner,
                        "product_id": task["product_id"],
                        "rule_version": rule_version,
                    },
                ).fetchall()
                if value["attribute_id"] == task["attribute_id"]
            ]
        else:
            extra["category"] = conn.execute(
                query("normalization", "product_category"),
                {"owner": owner, "product_id": task["product_id"]},
            ).fetchone()
        out.append({**task, **extra})
    return out


def _lock_open(conn: Connection[Any], owner: str, task_id: UUID, kind: str) -> Row:
    task: Row | None = conn.execute(
        query("normalization", "lock_other_task"), {"task_id": task_id, "owner": owner}
    ).fetchone()
    if task is None or task["kind"] != kind:
        raise TaskNotFoundError(str(task_id))
    if task["status"] != "open":
        raise TaskResolvedError(str(task_id))
    return task


def _record(
    conn: Connection[Any], actor: str, batch_id: UUID, entity: str, entity_id: UUID,
    before: Row, after: Row,
) -> None:  # fmt: skip
    conn.execute(
        query("matching", "insert_event"),
        {
            "actor": actor,
            "batch_id": batch_id,
            "entity_type": entity,
            "entity_id": entity_id,
            "action": "decide",
            "before": Jsonb(before),
            "after": Jsonb(after),
        },
    )


def decide_attribute(
    conn: Connection[Any],
    owner: str,
    task_id: UUID,
    decision: str,
    value_id: UUID | None,
    value_text: str | None,
) -> Row:
    """Settle a conflict: choose one source's value, or set your own.

    Raises:
        TaskNotFoundError: if the owner has no such conflict task.
        TaskResolvedError: if it was already decided.
        NotAChoiceError: if the value isn't one of this attribute's values.
    """
    task = _lock_open(conn, owner, task_id, KIND_ATTRIBUTE_CONFLICT)
    current: Row | None = conn.execute(
        query("normalization", "lock_product_attribute"),
        {"product_id": task["product_id"], "attribute_id": task["attribute_id"]},
    ).fetchone()
    if current is None:
        raise TaskNotFoundError(str(task_id))
    selected, reviewer, shown = None, None, value_text
    if decision == DECISION_CHOOSE:
        chosen = conn.execute(
            query("normalization", "value_for_attribute"),
            {
                "value_id": value_id,
                "product_id": task["product_id"],
                "attribute_id": task["attribute_id"],
            },
        ).fetchone()
        if chosen is None:
            raise NotAChoiceError(str(value_id))
        selected, shown = chosen["id"], chosen["display"]
    else:
        reviewer = {"text": value_text}
    conn.execute(
        query("normalization", "set_attribute_by_person"),
        {
            "id": current["id"],
            "selected_value_id": selected,
            "reviewer_value": Jsonb(reviewer) if reviewer is not None else None,
            "actor": owner,
        },
    )
    _record(
        conn,
        owner,
        task["batch_id"],
        "product_attribute",
        current["id"],
        {"state": current["state"], "selected_value_id": str(current["selected_value_id"] or "")},
        {"state": "reviewer_set", "selected_value_id": str(selected or ""), "value": shown},
    )
    conn.execute(query("matching", "resolve_task"), {"task_id": task_id})
    return {"product_id": task["product_id"], "shown": shown, "batch_id": task["batch_id"]}


def decide_category(conn: Connection[Any], owner: str, task_id: UUID, category_id: UUID) -> Row:
    """Set a product's category.

    Raises:
        TaskNotFoundError: if the owner has no such category task.
        TaskResolvedError: if it was already decided.
        NotAChoiceError: if the category doesn't exist or is the tree's root.
    """
    task = _lock_open(conn, owner, task_id, KIND_CATEGORY)
    categories = {
        row["id"]: row for row in conn.execute(query("normalization", "categories")).fetchall()
    }
    chosen = categories.get(category_id)
    if chosen is None or chosen["parent_id"] is None and chosen["code"] != "other":
        raise NotAChoiceError(str(category_id))
    current = conn.execute(
        query("normalization", "lock_product_category"), {"product_id": task["product_id"]}
    ).fetchone()
    assert current is not None  # noqa: S101
    conn.execute(
        query("normalization", "set_category"),
        {
            "product_id": task["product_id"],
            "category_id": category_id,
            "suggested_id": None,
            "state": "assigned",
            "source": "person",
            "confidence": None,
            "reason": None,
        },
    )
    _record(
        conn,
        owner,
        task["batch_id"],
        "product_category",
        task["product_id"],
        {"category_id": str(current["category_id"] or ""), "state": current["category_state"]},
        {"category_id": str(category_id), "state": "assigned", "name": chosen["name"]},
    )
    conn.execute(query("matching", "resolve_task"), {"task_id": task_id})
    return {
        "product_id": task["product_id"],
        "shown": chosen["name"],
        "batch_id": task["batch_id"],
    }
