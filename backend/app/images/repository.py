"""Data access for images, their embeddings and their near-duplicate groups.

Every similarity query names the batch or images, the owner and the embedding model, so
vectors from different models, or from other users, can never be compared.
"""

from typing import Any
from uuid import UUID

from psycopg import Connection

from app.db import query

Row = dict[str, Any]


class ComparisonRefusedError(Exception):
    """The images don't both belong to the owner, or don't both have this model's embedding."""


def similar_pairs(
    conn: Connection[Any], owner: str, batch_id: UUID, model_id: UUID, min_similarity: float
) -> list[Row]:
    """Return every pair of the batch's images at least this similar under one model."""
    return conn.execute(
        query("images", "similar_pairs"),
        {
            "owner": owner,
            "batch_id": batch_id,
            "model_id": model_id,
            "min_similarity": min_similarity,
        },
    ).fetchall()


def compare(conn: Connection[Any], owner: str, a: UUID, b: UUID, model_id: UUID) -> float:
    """Return two images' similarity under one model.

    Raises:
        ComparisonRefusedError: if either image isn't the owner's, or either lacks an
            embedding from this model.
    """
    row = conn.execute(
        query("images", "compare_two"), {"owner": owner, "a": a, "b": b, "model_id": model_id}
    ).fetchone()
    if row is None:
        raise ComparisonRefusedError(f"Images {a} and {b} can't be compared under {model_id}")
    return float(row["similarity"])


def list_images(conn: Connection[Any], owner: str, batch_id: UUID, limit: int) -> list[Row]:
    """Return a batch's images in file order. Empty if the batch isn't the owner's."""
    return conn.execute(
        query("images", "list_images"), {"owner": owner, "batch_id": batch_id, "limit": limit}
    ).fetchall()
