"""Image features and embeddings. Worker only: Pillow, OpenCV and boto3 load on first use."""

from app.vision.embedders.base import EmbeddingUnavailableError, ImageEmbedder, get_embedder
from app.vision.features import (
    ImageUnreadableError,
    colour_distance,
    colour_signature,
    for_embedding,
    hamming,
    load_rgb,
    phash,
    thumbnail,
)

__all__ = [
    "EmbeddingUnavailableError",
    "ImageEmbedder",
    "ImageUnreadableError",
    "colour_distance",
    "colour_signature",
    "for_embedding",
    "get_embedder",
    "hamming",
    "load_rgb",
    "phash",
    "thumbnail",
]
