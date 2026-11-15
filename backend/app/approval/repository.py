"""A person's edits, approvals, rejections and duplicate decisions (D40). Shared.

Every write locks the product, refuses a stale revision, records one event in `review_events`
with its before and after, and increments the product's revision.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.approval import rules
from app.config import get_enrichment_config, get_normalization_config
from app.db import query
from app.enrichment import build_facts
from app.enrichment.repository import category_paths
from app.normalization import Rules, normalize
from app.validation.base import BatchContext, ProductData
from app.validation.context import load_batch
from app.validation.engine import BatchReport, evaluate

Row = dict[str, Any]

# The product's own code can't be edited: it's what makes the product.
LOCKED_ATTRIBUTES = frozenset({"sku"})
NOTE_MAX = 500

_UNUSABLE = {
    "unit_missing": "Add a unit, such as 1.5 L, 350 ml, 640 g or 24 cm.",
    "range": "Give one value, not a range.",
    "approximate": "Give an exact value, without “approx”.",
    "two_values": "Give one value.",
    "wrong_unit": "That unit doesn't fit this attribute.",
    "not_a_number": "That isn't a number this attribute can use.",
    "currency_unknown": "Name the currency, such as ₹ or INR.",
    "not_a_whole_number": "Give a whole number.",
}


class NotFoundError(Exception):
    """The owner has no such product or attribute."""


class StaleError(Exception):
    """The product changed since the person loaded it."""


class InvalidError(Exception):
    """The request can't be done as asked. The message says why, for people."""


class NotReadyError(Exception):
    """The product can't be approved yet."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass(frozen=True)
class Batch:
    """A batch's checks and products, as approval sees them."""

    profile: str
    ctx: BatchContext
    report: BatchReport

    def product(self, product_id: UUID) -> ProductData | None:
        return next((p for p in self.ctx.products if p.id == product_id), None)


def load(conn: Connection[Any], owner: str, batch_id: UUID) -> Batch:
    """Everything approval reads about a batch."""
    batch = conn.execute(
        query("validation", "batch"), {"owner": owner, "batch_id": batch_id}
    ).fetchone()
    if batch is None:
        raise NotFoundError(str(batch_id))
    ctx = load_batch(conn, owner, batch_id)
    overrides = conn.execute(
        query("validation", "overrides"), {"owner": owner, "batch_id": batch_id}
    ).fetchall()
    report = evaluate(ctx, batch["target_marketplace"], overrides)
    return Batch(profile=report.profile, ctx=ctx, report=report)


def view(batch: Batch, product: ProductData) -> Row:
    """A product's approval for API responses."""
    report = batch.report.product(product.id)
    state = rules.state(product, batch.profile)
    blocking = rules.blockers(product, report)
    return {
        "product_id": product.id,
        "product_key": product.key,
        "state": state,
        "revision": product.row.get("revision", 0),
        "can_approve": state != rules.APPROVED and not blocking,
        "blockers": blocking,
        "approved_at": product.row.get("approved_at") if state != rules.DRAFT else None,
        "approved_by": product.row.get("approved_by") if state != rules.DRAFT else None,
        "rejection_reason": product.row.get("rejection_reason"),
        "duplicate_of": product.row.get("duplicate_of"),
    }


def approved_products(conn: Connection[Any], owner: str, batch_id: UUID) -> list[UUID]:
    """The products export may include (M10): approved, and unchanged since."""
    batch = load(conn, owner, batch_id)
    return [p.id for p in batch.ctx.products if rules.state(p, batch.profile) == rules.APPROVED]


# Writes -----------------------------------------------------------------------------------


def _lock(conn: Connection[Any], owner: str, product_id: UUID, revision: int | None) -> Row:
    row: Row | None = conn.execute(
        query("approval", "lock_product"), {"owner": owner, "product_id": product_id}
    ).fetchone()
    if row is None:
        raise NotFoundError(str(product_id))
    if revision is not None and row["revision"] != revision:
        raise StaleError(str(product_id))
    return row


def _event(conn: Connection[Any], owner: str, batch_id: UUID, entity: str, entity_id: UUID,
           action: str, before: Row, after: Row) -> None:  # fmt: skip
    conn.execute(
        query("matching", "insert_event"),
        {
            "actor": owner,
            "batch_id": batch_id,
            "entity_type": entity,
            "entity_id": entity_id,
            "action": action,
            "before": Jsonb(before),
            "after": Jsonb(after),
        },
    )


def _bump(conn: Connection[Any], product_id: UUID) -> int:
    row = conn.execute(query("approval", "bump_revision"), {"product_id": product_id}).fetchone()
    return int(row["revision"]) if row else 0


def standard_text(attribute: Row, value: str) -> str:
    """A person's value in standard form, read with M6's rules.

    Raises:
        InvalidError: if it can't be relied on, with what to change.
    """
    text = " ".join(value.split())
    if not text:
        raise InvalidError("Type a value, or clear it instead.")
    found = normalize(Rules(get_normalization_config()), attribute, text)
    if len(found) != 1:
        raise InvalidError("Give one value for this attribute.")
    normal = found[0]
    if not normal.usable:
        flag = next((f for f in normal.flags if f in _UNUSABLE), "not_a_number")
        raise InvalidError(_UNUSABLE[flag])
    return normal.display


def set_value(
    conn: Connection[Any],
    owner: str,
    product_id: UUID,
    code: str,
    value: str | None,
    note: str | None,
    revision: int | None,
) -> Row:
    """Set (or, with None, clear) one of a product's values, as a person's decision.

    Processing never overwrites it. An open conflict on the same attribute is resolved too.

    Raises:
        NotFoundError, StaleError, InvalidError.
    """
    product = _lock(conn, owner, product_id, revision)
    attribute: Row | None = conn.execute(query("approval", "attribute"), {"code": code}).fetchone()
    if attribute is None:
        raise NotFoundError(code)
    if code in LOCKED_ATTRIBUTES:
        raise InvalidError("The product's own code can't be edited.")
    shown = standard_text(attribute, value) if value is not None else None
    params = {"product_id": product_id, "attribute_id": attribute["id"]}
    current = conn.execute(query("approval", "current_value"), params).fetchone()
    row = conn.execute(
        query("approval", "set_value"),
        {
            **params,
            "reviewer_value": Jsonb({"text": shown}) if shown is not None else None,
            "state": "reviewer_set" if shown is not None else "missing",
            "actor": owner,
        },
    ).fetchone()
    assert row is not None  # noqa: S101
    resolved = conn.execute(query("approval", "resolve_conflict"), params).fetchall()
    _event(
        conn, owner, product["batch_id"], "product_attribute", row["id"],
        "edit" if shown is not None else "clear",
        {"state": current["state"] if current else "missing",
         "value": current["shown"] if current else None},
        {"state": "reviewer_set" if shown is not None else "missing", "value": shown,
         "note": (note or "").strip()[:NOTE_MAX] or None,
         "resolved_review": bool(resolved)},
    )  # fmt: skip
    return {"batch_id": product["batch_id"], "shown": shown, "revision": _bump(conn, product_id)}


def edit_copy(
    conn: Connection[Any],
    owner: str,
    product_id: UUID,
    title: str,
    description: str,
    bullets: list[str],
    revision: int | None,
) -> Row:
    """Save copy a person wrote, as a new version that automatic writing never replaces.

    Raises:
        NotFoundError, StaleError, InvalidError.
    """
    product = _lock(conn, owner, product_id, revision)
    settings = get_enrichment_config()
    title = " ".join(title.split())
    description = " ".join(description.split())
    bullets = [" ".join(b.split()) for b in bullets if b.strip()]
    if not title:
        raise InvalidError("The title can't be empty.")
    if len(title) > settings["title_max_chars"] * 2:
        raise InvalidError(f"The title is too long ({len(title)} characters).")
    if len(description) > settings["description_max_chars"] * 2:
        raise InvalidError(f"The description is too long ({len(description)} characters).")
    if len(bullets) > 10 or any(len(b) > settings["bullet_max_chars"] * 2 for b in bullets):
        raise InvalidError("Keep to 10 bullets, each a sentence or so.")

    # The facts it was written against, so it shows as out of date when they change.
    ctx = load_batch(conn, owner, product["batch_id"])
    item = next((p for p in ctx.products if p.id == product_id), None)
    assert item is not None  # noqa: S101
    category = {
        "path": category_paths(conn).get(item.row["category_id"])
        if item.row["category_id"]
        else None,
        "source": item.row["category_source"],
        "state": item.row["category_state"],
    }
    attribute_rows = conn.execute(
        query("enrichment", "attributes"),
        {"owner": owner, "batch_id": product["batch_id"], "product_id": product_id},
    ).fetchall()
    sheet = build_facts(attribute_rows, category, settings)

    # Only one version is current: the new one.
    conn.execute(query("enrichment", "clear_current"), {"product_id": product_id})
    stored = conn.execute(
        query("approval", "insert_edited_copy"),
        {
            "product_id": product_id,
            "batch_id": product["batch_id"],
            "owner": owner,
            "title": title,
            "description": description,
            "bullets": bullets,
            "fingerprint": sheet.fingerprint,
        },
    ).fetchone()
    assert stored is not None  # noqa: S101
    conn.execute(query("approval", "copy_written"), {"product_id": product_id})
    _event(
        conn, owner, product["batch_id"], "generated_content", product_id, "edit",
        {"title": item.row.get("generated_title"), "version": item.row.get("content_version")},
        {"title": title, "version": stored["version"]},
    )  # fmt: skip
    return {"version": stored["version"], "revision": _bump(conn, product_id)}


def _set_status(conn: Connection[Any], owner: str, product: Row, status: str,
                fingerprint: str | None, reason: str | None,
                duplicate_of: UUID | None) -> int:  # fmt: skip
    row = conn.execute(
        query("approval", "set_status"),
        {
            "product_id": product["id"],
            "status": status,
            "actor": owner,
            "fingerprint": fingerprint,
            "reason": reason,
            "duplicate_of": duplicate_of,
        },
    ).fetchone()
    return int(row["revision"]) if row else 0


def approve(conn: Connection[Any], owner: str, product_id: UUID, revision: int | None) -> int:
    """Approve a product, recording what it was when approved.

    Raises:
        NotFoundError, StaleError, NotReadyError.
    """
    product = _lock(conn, owner, product_id, revision)
    batch = load(conn, owner, product["batch_id"])
    item = batch.product(product_id)
    assert item is not None  # noqa: S101
    reasons = rules.blockers(item, batch.report.product(product_id))
    if reasons:
        raise NotReadyError(reasons)
    new = _set_status(conn, owner, product, rules.APPROVED,
                      rules.fingerprint(item, batch.profile), None, None)  # fmt: skip
    _event(
        conn, owner, product["batch_id"], "product_status", product_id, "approve",
        {"status": product["status"]}, {"status": rules.APPROVED, "profile": batch.profile},
    )  # fmt: skip
    return new


def reject(conn: Connection[Any], owner: str, product_id: UUID, reason: str,
           revision: int | None, duplicate_of: UUID | None = None) -> int:  # fmt: skip
    """Reject a product with a reason. It leaves checks, approval and export.

    Raises:
        NotFoundError, StaleError, InvalidError.
    """
    product = _lock(conn, owner, product_id, revision)
    reason = " ".join(reason.split())[:NOTE_MAX]
    if not reason:
        raise InvalidError("Give a reason for rejecting it.")
    new = _set_status(conn, owner, product, rules.REJECTED, None, reason, duplicate_of)
    _event(conn, owner, product["batch_id"], "product_status", product_id, "reject",
           {"status": product["status"]},
           {"status": rules.REJECTED, "reason": reason,
            "duplicate_of": str(duplicate_of) if duplicate_of else None})  # fmt: skip
    return new


def restore(conn: Connection[Any], owner: str, product_id: UUID, revision: int | None) -> int:
    """Put an approved or rejected product back to draft.

    Raises:
        NotFoundError, StaleError, InvalidError.
    """
    product = _lock(conn, owner, product_id, revision)
    if product["status"] == rules.DRAFT:
        raise InvalidError("It's already a draft.")
    new = _set_status(conn, owner, product, rules.DRAFT, None, None, None)
    _event(conn, owner, product["batch_id"], "product_status", product_id, "restore",
           {"status": product["status"]}, {"status": rules.DRAFT})  # fmt: skip
    return new


def mark_duplicate(conn: Connection[Any], owner: str, product_id: UUID, other_id: UUID,
                   keep: str, revision: int | None) -> Row:  # fmt: skip
    """Settle a flagged duplicate pair: keep this one, keep the other, or they're different.

    Raises:
        NotFoundError, StaleError, InvalidError.
    """
    product = _lock(conn, owner, product_id, revision)
    other = _lock(conn, owner, other_id, None)
    if other["batch_id"] != product["batch_id"] or other_id == product_id:
        raise InvalidError("Those two aren't a pair in the same batch.")
    batch = load(conn, owner, product["batch_id"])
    found = batch.report.product(product_id)
    pair = [
        issue
        for issue in (found.issues if found else [])
        if issue.rule.startswith("duplicate_") and issue.other_product_id == other_id
    ]
    if not pair:
        raise InvalidError("Those two aren't flagged as duplicates.")
    if keep == "different":
        if any(issue.rule == "duplicate_gtin" for issue in pair):
            raise InvalidError(
                "Two products can't share a barcode. Edit the wrong one's barcode instead."
            )
        from app.validation import set_aside

        for this, that in ((product_id, other_id), (other_id, product_id)):
            for issue in pair:
                try:
                    set_aside(conn, owner, this, issue.rule, str(that), "Different products.")
                except Exception:  # noqa: BLE001, S112 - one side may already be aside
                    continue
        _event(conn, owner, product["batch_id"], "duplicate", product_id, "different",
               {}, {"other": str(other_id), "keep": "both"})  # fmt: skip
        return {"revision": _bump(conn, product_id)}
    kept, dropped = (product, other) if keep == "this" else (other, product)
    _event(conn, owner, product["batch_id"], "duplicate", product_id, "same_product",
           {}, {"other": str(other_id), "kept": str(kept["id"])})  # fmt: skip
    reject(conn, owner, dropped["id"], f"Duplicate of {kept['product_key']}.", None,
           duplicate_of=kept["id"])  # fmt: skip
    return {"revision": _bump(conn, product_id)}


def approve_batch(conn: Connection[Any], owner: str, batch_id: UUID,
                  product_ids: list[UUID] | None) -> Row:  # fmt: skip
    """Approve every ready product of a batch (or those listed), and say why others weren't.

    Raises:
        NotFoundError.
    """
    batch = load(conn, owner, batch_id)
    wanted = set(product_ids) if product_ids else None
    approved, skipped = [], []
    for item in batch.ctx.products:
        if wanted is not None and item.id not in wanted:
            continue
        if rules.state(item, batch.profile) == rules.APPROVED or item.rejected:
            continue
        try:
            approve(conn, owner, item.id, None)
            approved.append(item.id)
        except NotReadyError as exc:
            skipped.append({"product_id": item.id, "product_key": item.key,
                            "reasons": exc.reasons})  # fmt: skip
    return {"approved": approved, "skipped": skipped}


def reject_batch(conn: Connection[Any], owner: str, batch_id: UUID, product_ids: list[UUID],
                 reason: str) -> list[UUID]:  # fmt: skip
    """Reject the listed products of a batch.

    Raises:
        NotFoundError, InvalidError.
    """
    batch = load(conn, owner, batch_id)
    ours = {item.id for item in batch.ctx.products}
    if not set(product_ids) <= ours:
        raise InvalidError("Some of those products aren't in this batch.")
    for product_id in product_ids:
        reject(conn, owner, product_id, reason, None)
    return product_ids


def review_summary(conn: Connection[Any], owner: str) -> Row:
    """Open review items across all of the owner's batches."""
    rows = conn.execute(query("approval", "review_summary"), {"owner": owner}).fetchall()
    return {"open": sum(int(r["open"]) for r in rows), "batches": rows}
