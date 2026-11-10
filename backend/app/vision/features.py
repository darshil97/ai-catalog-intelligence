"""What is computed from an image's pixels: a perceptual hash, a colour signature, a thumbnail.

Pillow and OpenCV load lazily, so importing this costs nothing until an image is processed.
"""

import io
from pathlib import Path
from typing import Any

_PHASH_SIZE = 32
_PHASH_BITS = 8
_HUE_BINS = 12


class ImageUnreadableError(Exception):
    """The image couldn't be decoded."""


def load_rgb(path: Path) -> Any:
    """Open an image upright (phones record rotation instead of applying it), as RGB.

    Transparent areas become white, so a cut-out product reads the same as on a white page.

    Raises:
        ImageUnreadableError: if the file can't be decoded.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageUnreadableError(f"{type(exc).__name__}: {exc}") from exc
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def phash(image: Any) -> int:
    """Return a 64-bit perceptual hash, as a signed integer so it fits a BIGINT.

    The image is shrunk to 32x32 grey, transformed to frequencies, and the lowest 8x8 are
    compared with their median. Resizing and re-compressing change it very little.
    """
    import cv2
    import numpy as np

    grey = np.asarray(image.convert("L").resize((_PHASH_SIZE, _PHASH_SIZE)), dtype=np.float32)
    low = cv2.dct(grey)[:_PHASH_BITS, :_PHASH_BITS].flatten()
    # The first value is the overall brightness, which says nothing about the picture.
    median = float(np.median(low[1:]))
    value = 0
    for bit in low > median:
        value = (value << 1) | int(bit)
    return value - (1 << 64) if value >= 1 << 63 else value


def hamming(a: int, b: int) -> int:
    """Return how many of the 64 bits differ between two hashes."""
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")


def colour_signature(image: Any) -> list[float]:
    """Return how much of each hue the image holds: 12 numbers that add up to at most 1.

    Each pixel adds its saturation times brightness to the bin of its hue, as a share of all
    pixels. Brightness changes, crops and mirroring barely move it, while the same product in
    another colour moves it a lot. Grey and white areas add almost nothing.
    """
    import numpy as np

    hsv = np.asarray(image.resize((128, 128)).convert("HSV"), dtype=np.float32) / 255
    weights = hsv[..., 1] * hsv[..., 2]
    histogram, _ = np.histogram(hsv[..., 0], bins=_HUE_BINS, range=(0, 1), weights=weights)
    return [round(float(value), 5) for value in histogram / weights.size]


def colour_distance(a: list[float], b: list[float]) -> float:
    """Return how different two colour signatures are, from 0 (the same) to 2."""
    return float(sum(abs(x - y) for x, y in zip(a, b, strict=True)))


def thumbnail(image: Any, side: int, quality: int) -> bytes:
    """Return a JPEG no larger than `side` on its longest edge."""
    copy = image.copy()
    copy.thumbnail((side, side))
    out = io.BytesIO()
    copy.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def for_embedding(image: Any, max_side: int) -> bytes:
    """Return a JPEG no larger than `max_side`, the size embedding models accept."""
    copy = image.copy()
    copy.thumbnail((max_side, max_side))
    out = io.BytesIO()
    copy.save(out, format="JPEG", quality=90)
    return out.getvalue()
