"""Reading products and where their values came from."""

from typing import Any
from uuid import UUID

from app import catalog, images, products
from app.api.services.common import database, not_found
from app.auth import Profile
from app.config import get_catalog_config, get_images_config
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
    """Return a product with its observations, or 404."""
    limit = get_catalog_config()["max_list_items"]
    try:
        with database("get_product") as conn:
            return products.get_product(conn, profile.user_id, product_id, limit)
    except products.ProductNotFoundError:
        not_found()


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
