"""Making, listing and downloading exports, and deciding variant pairs (D41). Shared.

Exports are built by the API on request: a batch holds a few products, so a file is ready at
once, and the API's key can sign photo links for S3's 7-day maximum.
"""

import hashlib
import re
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.db import query
from app.export import formats
from app.export.data import load
from app.storage import StorageError, export_key, get_object_store

Row = dict[str, Any]

LINK_SECONDS = 7 * 24 * 3600  # S3's longest signed link
DOWNLOAD_SECONDS = 15 * 60
FORMATS = {
    "csv": ("csv", "text/csv"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "shopify_csv": ("csv", "text/csv"),
}
_SUFFIX = {"csv": "catalog", "xlsx": "catalog", "shopify_csv": "shopify"}


class NotFoundError(Exception):
    """The owner has no such batch, export or variant pair."""


class NothingApprovedError(Exception):
    """No product of the batch is approved and unchanged since."""

    def __init__(self, left_out: list[Row]) -> None:
        super().__init__("nothing approved")
        self.left_out = left_out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "batch"


def create(conn: Connection[Any], owner: str, batch_id: UUID, fmt: str) -> Row:
    """Build an export from the batch's approved products, store it, and record it.

    Raises:
        NotFoundError: if the owner has no such batch.
        NothingApprovedError: if no product qualifies.
        StorageError: if the file can't be stored or photo links signed.
    """
    data = load(conn, owner, batch_id)
    if data is None:
        raise NotFoundError(str(batch_id))
    if not data.items:
        raise NothingApprovedError(data.left_out)
    store = get_object_store()
    links = {
        photo["image_id"]: store.presign_download(photo["s3_key"], LINK_SECONDS)
        for item in data.items
        for photo in item.photos
    }
    made_at = datetime.now(UTC)
    if fmt == "csv":
        body, notes = formats.build_csv(data, links)
    elif fmt == "xlsx":
        body, notes = formats.build_xlsx(data, links, made_at)
    else:
        body, notes = formats.build_shopify(data, links)

    export_id = uuid.uuid4()
    extension, content_type = FORMATS[fmt]
    filename = f"{_slug(data.batch['name'])}-{_SUFFIX[fmt]}.{extension}"
    key = export_key(owner, batch_id, export_id, filename)
    sha256 = hashlib.sha256(body).hexdigest()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / filename
        path.write_bytes(body)
        store.upload(key, path, content_type, sha256)
    row: Row | None = conn.execute(
        query("export", "insert_export"),
        {
            "id": export_id,
            "batch_id": batch_id,
            "owner": owner,
            "format": fmt,
            "s3_key": key,
            "profile": data.profile,
            "product_ids": [str(item.id) for item in data.items],
            "product_count": len(data.items),
            "left_out_count": len(data.left_out),
            "links_expire_at": made_at + timedelta(seconds=LINK_SECONDS) if links else None,
            "size_bytes": len(body),
            "sha256": sha256,
            "notes": Jsonb(notes),
        },
    ).fetchone()
    assert row is not None  # noqa: S101
    return {
        **row,
        "left_out": data.left_out,
        "download_url": store.presign_download(key, DOWNLOAD_SECONDS),
    }


def list_exports(conn: Connection[Any], owner: str, batch_id: UUID) -> list[Row]:
    """A batch's exports, newest first."""
    return list(
        conn.execute(
            query("export", "list_exports"), {"owner": owner, "batch_id": batch_id}
        ).fetchall()
    )


def download_url(conn: Connection[Any], owner: str, export_id: UUID) -> str:
    """A fresh 15-minute link to an export.

    Raises:
        NotFoundError: if the owner has no such export.
        StorageError: if the link can't be signed.
    """
    row = conn.execute(
        query("export", "get_export"), {"owner": owner, "export_id": export_id}
    ).fetchone()
    if row is None:
        raise NotFoundError(str(export_id))
    return get_object_store().presign_download(row["s3_key"], DOWNLOAD_SECONDS)


def decide_variant(conn: Connection[Any], owner: str, variant_id: UUID, decision: str) -> Row:
    """Confirm or dismiss a possible variant pair, recorded on both products.

    A decision changes both products, so an approval of either needs giving again.

    Raises:
        NotFoundError: if the owner has no such pair.
    """
    pair = conn.execute(
        query("export", "lock_variant"), {"owner": owner, "variant_id": variant_id}
    ).fetchone()
    if pair is None:
        raise NotFoundError(str(variant_id))
    conn.execute(query("export", "set_variant"), {"variant_id": variant_id, "status": decision})
    for this, other in ((pair["product_a_id"], pair["product_b_id"]),
                        (pair["product_b_id"], pair["product_a_id"])):  # fmt: skip
        conn.execute(
            query("matching", "insert_event"),
            {
                "actor": owner,
                "batch_id": pair["batch_id"],
                "entity_type": "variant",
                "entity_id": this,
                "action": decision,
                "before": Jsonb({"status": pair["status"]}),
                "after": Jsonb({"status": decision, "other": str(other)}),
            },
        )
        conn.execute(query("approval", "bump_revision"), {"product_id": this})
    return {"id": variant_id, "status": decision}


__all__ = ["StorageError"]
