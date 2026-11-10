"""Covers what is computed from pixels: hashes, colour signatures, thumbnails, and the fake
embedder tests rely on. Needs no database and no network; images are generated here."""

import io
import math
from pathlib import Path

import pytest
from PIL import Image, ImageEnhance, ImageOps

import samples
from app.vision import (
    ImageUnreadableError,
    colour_distance,
    colour_signature,
    for_embedding,
    get_embedder,
    hamming,
    load_rgb,
    phash,
    thumbnail,
)
from app.vision.embedders.base import EmbeddingUnavailableError
from app.vision.embedders.fake import FakeEmbedder

COPY_LIMIT = 0.04  # duplicate_max_colour_distance in config.yaml


def _reload(image: Image.Image, quality: int) -> Image.Image:
    return Image.open(io.BytesIO(samples.jpeg_of(image, quality))).convert("RGB")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


@pytest.fixture
def mug() -> Image.Image:
    return samples.product_photo((190, 30, 40), "mug")


def _copies(image: Image.Image) -> dict[str, Image.Image]:
    w, h = image.size
    return {
        "resized": image.resize((w // 2, h // 2)),
        "jpeg 30": _reload(image, 30),
        "cropped 5%": image.crop((int(w * 0.05), int(h * 0.05), int(w * 0.95), int(h * 0.95))),
        "brighter": ImageEnhance.Brightness(image).enhance(1.15),
    }


def test_copies_keep_their_hash_and_colours(mug: Image.Image) -> None:
    base_hash, base_colour = phash(mug), colour_signature(mug)
    for name, copy in _copies(mug).items():
        assert hamming(base_hash, phash(copy)) <= 12, name
        assert colour_distance(base_colour, colour_signature(copy)) <= COPY_LIMIT, name


def test_a_colour_variant_is_far_apart_in_colour(mug: Image.Image) -> None:
    blue = samples.product_photo((30, 40, 190), "mug")
    # The grey picture is nearly the same, which is why colour is checked separately.
    assert hamming(phash(mug), phash(blue)) <= 12
    assert colour_distance(colour_signature(mug), colour_signature(blue)) > COPY_LIMIT * 3


def test_a_different_product_has_a_different_hash(mug: Image.Image) -> None:
    box = samples.product_photo((190, 30, 40), "box")
    assert hamming(phash(mug), phash(box)) > 12


def test_hash_fits_a_signed_bigint(mug: Image.Image) -> None:
    for image in (mug, ImageOps.invert(mug)):
        value = phash(image)
        assert -(2**63) <= value < 2**63
    assert hamming(-1, 0) == 64 and hamming(5, 5) == 0


def test_phone_rotation_is_applied(tmp_path: Path, mug: Image.Image) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # stored sideways; shown turned 90 degrees clockwise
    path = tmp_path / "phone.jpg"
    mug.save(path, format="JPEG", exif=exif)
    upright = load_rgb(path)
    assert upright.size == (mug.height, mug.width)


def test_transparency_becomes_white(tmp_path: Path) -> None:
    clear = Image.new("RGBA", (40, 40), (255, 0, 0, 0))
    path = tmp_path / "cut-out.png"
    clear.save(path)
    assert load_rgb(path).getpixel((5, 5)) == (255, 255, 255)


def test_broken_image_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
    with pytest.raises(ImageUnreadableError):
        load_rgb(path)


def test_thumbnails_and_embedding_inputs_are_bounded(mug: Image.Image) -> None:
    small = Image.open(io.BytesIO(thumbnail(mug, 320, 82)))
    assert max(small.size) == 320 and small.format == "JPEG"
    big = samples.product_photo().resize((3000, 2400))
    assert max(Image.open(io.BytesIO(for_embedding(big, 2048))).size) == 2048


def test_fake_embedder_behaves_like_a_real_one(mug: Image.Image) -> None:
    embedder = get_embedder()
    assert isinstance(embedder, FakeEmbedder)
    base = embedder.embed(for_embedding(mug, 2048))
    assert len(base) == 1024
    for name, copy in _copies(mug).items():
        if name != "cropped 5%":
            assert _cosine(base, embedder.embed(for_embedding(copy, 2048))) >= 0.9, name
    other = embedder.embed(for_embedding(samples.product_photo(shape="ball"), 2048))
    assert _cosine(base, other) < 0.9
    embedder.fail_next = 1
    with pytest.raises(EmbeddingUnavailableError):
        embedder.embed(for_embedding(mug, 2048))
