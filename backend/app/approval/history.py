"""A product's history in words, from the append-only review_events (D40)."""

from typing import Any
from uuid import UUID

from psycopg import Connection

from app.db import query

Row = dict[str, Any]


def _quote(value: Any) -> str:
    return f"“{value}”" if value not in (None, "") else "nothing"


def describe(event: Row) -> str:
    """One recorded change in plain words."""
    kind, action = event["entity_type"], event["action"]
    before, after = event["before"] or {}, event["after"] or {}
    attribute = event.get("attribute") or "a value"
    if kind == "product_attribute":
        if action == "clear":
            return f"Cleared {attribute} (was {_quote(before.get('value'))})"
        was = f" (was {_quote(before.get('value'))})" if before.get("value") else ""
        verb = "Set" if action == "edit" else "Chose"
        return f"{verb} {attribute} to {_quote(after.get('value'))}{was}"
    if kind == "product_category":
        return f"Chose the category {_quote(after.get('name'))}"
    if kind == "image_match_candidate":
        photo = event.get("image") or "a photo"
        if after.get("decision") == "accepted":
            return f"Confirmed the photo {photo}"
        return f"Said the photo {photo} doesn't show this product"
    if kind == "validation_issue":
        rule = str(after.get("rule") or before.get("rule") or "").replace("_", " ")
        if action == "set_aside":
            return f"Set aside a warning ({rule}): {_quote(after.get('reason'))}"
        return f"Brought back a warning ({rule})"
    if kind == "generated_content":
        return f"Edited the copy by hand (version {after.get('version')})"
    if kind == "product_status":
        if action == "approve":
            return f"Approved it for {after.get('profile') or 'export'}"
        if action == "reject":
            return f"Rejected it: {_quote(after.get('reason'))}"
        return "Put it back to draft"
    if kind == "variant":
        if action == "confirmed":
            return "Confirmed it and another product as variants of one product"
        return "Said it and another product aren't variants"
    if kind == "duplicate":
        if action == "different":
            return "Marked it and its look-alike as different products"
        return "Marked it and another product as the same product"
    return f"{action} {kind.replace('_', ' ')}"


def product_history(conn: Connection[Any], owner: str, product: Row) -> list[Row]:
    """Every recorded change to a product, newest first, with processing steps."""
    params = {"batch_id": product["batch_id"], "product_id": product["id"]}
    entries = [
        {
            "at": row["created_at"],
            "by": "you" if row["actor_user_id"] == owner else row["actor_user_id"],
            "kind": row["entity_type"],
            "action": row["action"],
            "text": describe(row),
            "before": row["before"],
            "after": row["after"],
        }
        for row in conn.execute(query("approval", "history"), params).fetchall()
    ]
    for version in conn.execute(
        query("approval", "copy_versions"), {"product_id": product["id"]}
    ).fetchall():
        if version["created_by"] == "system":
            entries.append(
                {
                    "at": version["created_at"],
                    "by": "system",
                    "kind": "generated_content",
                    "action": "write",
                    "text": f"Copy written from settled values (version {version['version']})",
                    "before": None,
                    "after": None,
                }
            )
    entries.append(
        {
            "at": product["created_at"],
            "by": "system",
            "kind": "product",
            "action": "create",
            "text": "Read from the supplier's files",
            "before": None,
            "after": None,
        }
    )
    entries.sort(key=lambda entry: entry["at"], reverse=True)
    return entries


def product_row(conn: Connection[Any], owner: str, product_id: UUID) -> Row | None:
    """The product's batch and creation time, or None if it isn't the owner's."""
    row: Row | None = conn.execute(
        query("products", "get_product"), {"owner": owner, "product_id": product_id}
    ).fetchone()
    return row
