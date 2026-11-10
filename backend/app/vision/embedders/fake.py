"""A stand-in embedder for tests: the image's grey pixels at 32x32, centred.

It needs no network and behaves like a real one where tests need it to: a resized or
re-compressed copy points the same way, and a different picture doesn't. `fail_next` makes the
next call fail, as an unreachable model would.
"""

import io

from app.config import get_embeddings_config
from app.vision.embedders.base import EmbeddingUnavailableError, register_embedder


class FakeEmbedder:
    """Deterministic embeddings from pixels."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.fail_next = 0

    def embed(self, jpeg: bytes) -> list[float]:
        """Return the centred grey pixels, padded to the configured dimension."""
        from PIL import Image

        self.calls += 1
        if self.fail_next:
            self.fail_next -= 1
            raise EmbeddingUnavailableError("Simulated outage")
        grey = Image.open(io.BytesIO(jpeg)).convert("L").resize((32, 32))
        pixels = [float(value) for value in grey.tobytes()]
        mean = sum(pixels) / len(pixels)
        vector = [value - mean for value in pixels]
        dimension = get_embeddings_config()["dimension"]
        return (vector + [0.0] * dimension)[:dimension]


register_embedder(FakeEmbedder.name, FakeEmbedder)
