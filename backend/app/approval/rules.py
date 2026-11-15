"""When a product can be approved, and whether an approval still holds (D40). Pure.

An approval stores a fingerprint of the product as approved: its values, category, copy, photos
and the batch's marketplace profile. It holds only while the product still has that
fingerprint, so any later change makes it "changed since approval" without anything having to
remember to clear it.
"""

import hashlib
import json
from typing import Any

from app.validation.base import ERROR, ProductData
from app.validation.engine import ProductReport

DRAFT = "draft"
APPROVED = "approved"
CHANGED = "changed_since_approval"
REJECTED = "rejected"


def fingerprint(product: ProductData, profile: str) -> str:
    """A hash of everything an approval vouches for."""
    row = product.row
    payload: list[Any] = [
        profile,
        sorted((a.code, a.state, a.shown or "") for a in product.attributes.values()),
        str(row.get("category_id") or ""),
        row.get("content_version"),
        row.get("content_status"),
        row.get("input_fingerprint"),
        sorted(str(photo["image_id"]) for photo in product.photos),
    ]
    return hashlib.sha256(json.dumps(payload, default=str).encode()).hexdigest()


def blockers(product: ProductData, report: ProductReport | None) -> list[str]:
    """Why a product can't be approved now, in words. Empty means it can be."""
    out: list[str] = []
    if product.rejected:
        return ["It's rejected. Restore it to draft first."]
    errors = report.count(ERROR) if report else 0
    if errors:
        out.append(f"{errors} {'error' if errors == 1 else 'errors'} to fix in its checks.")
    if product.text_state != "generated":
        out.append(
            "Its copy is out of date; regenerate or edit it."
            if product.text_state == "out_of_date"
            else "It has no copy yet."
        )
    if product.open_reviews:
        count = len(product.open_reviews)
        out.append(f"{count} review {'item is' if count == 1 else 'items are'} still open.")
    return out


def state(product: ProductData, profile: str) -> str:
    """draft, approved, changed_since_approval or rejected."""
    status = product.row.get("status")
    if status == REJECTED:
        return REJECTED
    if status == APPROVED:
        same = product.row.get("approval_fingerprint") == fingerprint(product, profile)
        return APPROVED if same else CHANGED
    return DRAFT
