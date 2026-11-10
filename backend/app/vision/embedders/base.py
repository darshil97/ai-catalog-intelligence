"""The ImageEmbedder contract and its registry (D34).

An embedder turns one image into a list of numbers; images that look alike get lists pointing
the same way. Which embedder runs is set in config, so tests never call a real model.
"""

from collections.abc import Callable
from typing import Protocol

from app.config import get_embeddings_config
from app.utils.logger import get_logger

logger = get_logger(__name__)


class EmbeddingUnavailableError(Exception):
    """The model couldn't be reached, was throttled, or rejected the image."""


class ImageEmbedder(Protocol):
    """Embeds images. One instance is shared, so it must keep no per-image state."""

    name: str

    def embed(self, jpeg: bytes) -> list[float]:
        """Return the image's embedding, of the configured dimension.

        Raises:
            EmbeddingUnavailableError: if no embedding could be obtained.
        """
        ...


_REGISTRY: dict[str, Callable[[], ImageEmbedder]] = {}
_INSTANCES: dict[str, ImageEmbedder] = {}


def register_embedder(name: str, factory: Callable[[], ImageEmbedder]) -> None:
    """Make an embedder available under a name. It is created on first use."""
    _REGISTRY[name] = factory


def get_embedder() -> ImageEmbedder:
    """Return the configured embedder, created once.

    Raises:
        ValueError: if embeddings.backend names no registered embedder.
    """
    import app.vision.embedders  # noqa: F401 - registers the embedders

    name = get_embeddings_config()["backend"]
    if name not in _REGISTRY:
        raise ValueError(f"Unknown embedder: {name!r}. Registered: {', '.join(sorted(_REGISTRY))}")
    if name not in _INSTANCES:
        _INSTANCES[name] = _REGISTRY[name]()
        logger.info("Embedder ready: backend=%s", name)
    return _INSTANCES[name]
