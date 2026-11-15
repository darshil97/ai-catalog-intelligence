"""Reading products and where their values came from."""

from typing import Any
from uuid import UUID

from app import catalog, images, products, review
from app.api.services.common import database, not_found
from app.auth import Profile
from app.config import get_catalog_config, get_images_config, get_normalization_config
from app.storage import StorageError, get_object_store
from app.utils.logger import get_logger

logger = get_logger(__name__)


def list_products(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return a batch's products, or 404."""
    limit = get_catalog_config()["max_list_items"]
    try:
        with database("list_products") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            items = products.list_products(conn, profile.user_id, batch_id, limit)
    except catalog.NotFoundError:
        not_found()
    return {"batch_id": batch_id, "items": items}


def get_product(profile: Profile, product_id: UUID) -> dict[str, Any]:
    """Return a product with its observations and accepted images, or 404."""
    from app.api.services.approval import product_approval
    from app.api.services.enrichment import product_content
    from app.api.services.review import thumbnail_url
    from app.api.services.validation import product_validation

    limit = get_catalog_config()["max_list_items"]
    try:
        with database("get_product") as conn:
            product = products.get_product(conn, profile.user_id, product_id, limit)
            pictures = review.product_images(conn, profile.user_id, product_id)
            values = products.catalog_values(
                conn, profile.user_id, product_id, get_normalization_config()["rule_version"]
            )
            category = products.category(conn, profile.user_id, product_id)
            generated = product_content(conn, profile, product)
            checks = product_validation(conn, profile.user_id, product_id, product["batch_id"])
            approval_state = product_approval(
                conn, profile.user_id, product_id, product["batch_id"]
            )
    except products.ProductNotFoundError:
        not_found()
    product["catalog_values"] = [
        {
            "code": row["code"],
            "name": row["name"],
            "state": row["state"] or "missing",
            "shown": row["shown"]
            or ((row["reviewer_value"] or {}).get("text") if row["reviewer_value"] else None),
            "decided_by": row["decided_by"],
            "values": row["values"],
        }
        for row in values
    ]
    product["category"] = _category(category)
    product["generated_content"] = generated
    product["validation"] = checks
    product["approval"] = approval_state
    product["images"] = [
        {
            "image_id": row["image_id"],
            "image_name": row["image_name"],
            "position": row["position"],
            "thumbnail_url": thumbnail_url(row["thumbnail_s3_key"]),
            "decision": row["decision"],
            "decided_by": row["decided_by"],
        }
        for row in pictures
    ]
    return product


def list_sheets(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return how each sheet of a batch was mapped, or 404."""
    limit = get_catalog_config()["max_list_items"]
    try:
        with database("list_sheets") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            items = products.list_sheets(conn, profile.user_id, batch_id, limit)
    except catalog.NotFoundError:
        not_found()
    return {"batch_id": batch_id, "items": items}


def list_documents(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return how each PDF and photo of a batch was read, or 404."""
    limit = get_catalog_config()["max_list_items"]
    try:
        with database("list_documents") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            items = products.list_documents(conn, profile.user_id, batch_id, limit)
    except catalog.NotFoundError:
        not_found()
    return {"batch_id": batch_id, "items": items}


def list_images(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return a batch's images with signed thumbnail links, or 404.

    A link that can't be signed is left out and logged; the list still loads.
    """
    limit = get_catalog_config()["max_list_items"]
    try:
        with database("list_images") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            rows = images.list_images(conn, profile.user_id, batch_id, limit)
    except catalog.NotFoundError:
        not_found()
    seconds = get_images_config()["thumbnail_url_seconds"]
    store = get_object_store()
    items = []
    for row in rows:
        url = None
        if row["thumbnail_s3_key"]:
            try:
                url = store.presign_download(row["thumbnail_s3_key"], seconds)
            except StorageError as exc:
                logger.warning("Thumbnail link not signed: image_id=%s error=%s", row["id"], exc)
        items.append(
            {
                **{k: v for k, v in row.items() if k != "thumbnail_s3_key"},
                "thumbnail_url": url,
                "group_size": row["group_size"] if row["duplicate_group_id"] else 1,
            }
        )
    return {"batch_id": batch_id, "items": items}


def _ref(row: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    if row.get(f"{prefix}_id") is None:
        return None
    return {
        "id": row[f"{prefix}_id"],
        "code": row[f"{prefix}_code"],
        "name": row[f"{prefix}_name"],
    }


def _category(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "state": row["category_state"],
        "source": row["category_source"],
        "confidence": float(row["category_confidence"])
        if row["category_confidence"] is not None
        else None,
        "reason": row["category_reason"],
        "category": _ref(row, "category"),
        "suggested": _ref(row, "suggested"),
    }


def list_categories() -> dict[str, Any]:
    """Return the category tree, with each category's full path."""
    with database("list_categories") as conn:
        rows = products.list_categories(conn)
    by_id = {row["id"]: row for row in rows}

    def path(row: dict[str, Any]) -> str:
        names: list[str] = []
        current: dict[str, Any] | None = row
        while current is not None:
            if current["code"] != "home-kitchen":
                names.append(current["name"])
            current = by_id.get(current["parent_id"])
        return " › ".join(reversed(names)) or row["name"]

    return {
        "items": [
            {"id": row["id"], "code": row["code"], "name": row["name"], "path": path(row)}
            for row in rows
            if row["code"] != "home-kitchen"
        ]
    }


def list_variants(profile: Profile, batch_id: UUID) -> dict[str, Any]:
    """Return a batch's possible variant pairs, or 404."""
    limit = get_catalog_config()["max_list_items"]
    try:
        with database("list_variants") as conn:
            catalog.get_batch(conn, profile.user_id, batch_id)
            rows = products.list_variants(conn, profile.user_id, batch_id, limit)
    except catalog.NotFoundError:
        not_found()
    return {
        "batch_id": batch_id,
        "items": [{**row, "reasons": row["reason"]} for row in rows],
    }
