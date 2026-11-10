"""A live check of image embeddings against Titan on Bedrock (D34).

Skipped unless LIVE_EMBEDDINGS_TEST=1, with the Bedrock API key in backend/.env. It makes four
calls, costing $0.00032. It proves what the fake embedder can't: that the configured model is
reachable with this dimension, and that the near-duplicate threshold separates a re-saved copy
from a different product.
"""

import math
import os

import pytest

import samples
from app.config import get_config, get_embeddings_config, get_images_config
from app.vision import for_embedding
from app.vision.embedders.bedrock_titan import TitanEmbedder

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE_EMBEDDINGS_TEST") != "1",
    reason="set LIVE_EMBEDDINGS_TEST=1 to call the real model",
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_titan_separates_copies_from_other_products() -> None:
    get_config()["embeddings"]["backend"] = "bedrock"
    titan = TitanEmbedder()
    threshold = get_images_config()["duplicate_min_similarity"]
    mug = samples.product_photo((190, 30, 40), "mug")
    copy = mug.resize((300, 240))
    box = samples.product_photo((190, 30, 40), "box", label="KT-2001")
    ball = samples.product_photo((20, 140, 60), "ball", label="ZZ-9")
    vectors = {
        name: titan.embed(for_embedding(image, 2048))
        for name, image in {"mug": mug, "copy": copy, "box": box, "ball": ball}.items()
    }
    assert len(vectors["mug"]) == get_embeddings_config()["dimension"]
    assert _cosine(vectors["mug"], vectors["copy"]) >= threshold
    assert _cosine(vectors["mug"], vectors["box"]) < threshold
    assert _cosine(vectors["mug"], vectors["ball"]) < threshold
