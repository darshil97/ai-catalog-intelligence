"""Reading a batch's checks, and setting warnings aside with an audit record. Shared."""

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.db import query
from app.validation.base import WARNING
from app.validation.context import load_batch
from app.validation.engine import BatchReport, evaluate

AUDIT_ENTITY = "validation_issue"


class NotFoundError(Exception):
    """The owner has no such batch or product."""


class NotAWarningError(Exception):
    """Only a current warning can be set aside."""


def batch_report(conn: Connection[Any], owner: str, batch_id: UUID) -> BatchReport:
    """Check a batch under its profile.

    Raises:
        NotFoundError: if the owner has no such batch.
    """
    batch = conn.execute(
        query("validation", "batch"), {"owner": owner, "batch_id": batch_id}
    ).fetchone()
    if batch is None:
        raise NotFoundError(str(batch_id))
    ctx = load_batch(conn, owner, batch_id)
    overrides = conn.execute(
        query("validation", "overrides"), {"owner": owner, "batch_id": batch_id}
    ).fetchall()
    return evaluate(ctx, batch["target_marketplace"], overrides)


def product_batch(conn: Connection[Any], owner: str, product_id: UUID) -> UUID:
    """The batch a product of the owner's belongs to.

    Raises:
        NotFoundError: if the owner has no such product.
    """
    row = conn.execute(
        query("validation", "product_batch"), {"owner": owner, "product_id": product_id}
    ).fetchone()
    if row is None:
        raise NotFoundError(str(product_id))
    batch_id: UUID = row["batch_id"]
    return batch_id


def set_profile(conn: Connection[Any], owner: str, batch_id: UUID, profile: str) -> None:
    """Change a batch's marketplace profile. The caller checks the profile exists.

    Raises:
        NotFoundError: if the owner has no such batch.
    """
    row = conn.execute(
        query("validation", "set_profile"),
        {"owner": owner, "batch_id": batch_id, "profile": profile},
    ).fetchone()
    if row is None:
        raise NotFoundError(str(batch_id))


def _event(conn: Connection[Any], owner: str, batch_id: UUID, product_id: UUID,
           action: str, before: dict[str, Any], after: dict[str, Any]) -> None:  # fmt: skip
    conn.execute(
        query("matching", "insert_event"),
        {
            "actor": owner,
            "batch_id": batch_id,
            "entity_type": AUDIT_ENTITY,
            "entity_id": product_id,
            "action": action,
            "before": Jsonb(before),
            "after": Jsonb(after),
        },
    )


def set_aside(
    conn: Connection[Any], owner: str, product_id: UUID, rule: str, subject: str, reason: str
) -> None:
    """Set a current warning aside, recorded in the audit trail.

    Raises:
        NotFoundError: if the owner has no such product.
        NotAWarningError: if the product has no such warning now.
    """
    batch_id = product_batch(conn, owner, product_id)
    report = batch_report(conn, owner, batch_id).product(product_id)
    issue = next(
        (i for i in (report.issues if report else []) if i.rule == rule and i.subject == subject),
        None,
    )
    if issue is None or issue.severity != WARNING:
        raise NotAWarningError(rule)
    conn.execute(
        query("validation", "upsert_override"),
        {
            "product_id": product_id,
            "batch_id": batch_id,
            "owner": owner,
            "rule_code": rule,
            "subject": subject,
            "fingerprint": issue.fingerprint,
            "reason": reason,
        },
    )
    _event(
        conn, owner, batch_id, product_id, "set_aside",
        {"rule": rule, "subject": subject, "message": issue.message, "set_aside": False},
        {"rule": rule, "subject": subject, "reason": reason, "set_aside": True},
    )  # fmt: skip


def undo_set_aside(
    conn: Connection[Any], owner: str, product_id: UUID, rule: str, subject: str
) -> bool:
    """Bring a set-aside warning back. Returns False if it wasn't set aside.

    Raises:
        NotFoundError: if the owner has no such product.
    """
    batch_id = product_batch(conn, owner, product_id)
    row = conn.execute(
        query("validation", "delete_override"),
        {"owner": owner, "product_id": product_id, "rule_code": rule, "subject": subject},
    ).fetchone()
    if row is None:
        return False
    _event(
        conn, owner, batch_id, product_id, "undo_set_aside",
        {"rule": rule, "subject": subject, "reason": row["reason"], "set_aside": True},
        {"rule": rule, "subject": subject, "set_aside": False},
    )  # fmt: skip
    return True
