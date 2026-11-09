"""JPEG, PNG and WebP: the format must match the extension, and the image must decode safely."""

import warnings
from pathlib import Path
from typing import Any

from app.files import reasons
from app.files.reasons import FileRejectedError
from app.ingest import signatures
from app.ingest.kinds.base import CheckLimits, register_kind

_SIGNATURES = {
    "jpg": signatures.is_jpeg,
    "jpeg": signatures.is_jpeg,
    "png": signatures.is_png,
    "webp": signatures.is_webp,
}
_PILLOW_FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}


def _reject(rejection: reasons.Rejection) -> FileRejectedError:
    return FileRejectedError(rejection.code, rejection.message)


class ImageKind:
    """Checks the header, then has Pillow verify the whole file, with a pixel limit."""

    name = "image"
    extensions = frozenset(_SIGNATURES)

    def check(self, path: Path, extension: str, limits: CheckLimits) -> dict[str, Any]:
        """Confirm the image is what its extension says and isn't an image bomb."""
        if not _SIGNATURES[extension](signatures.read_head(path)):
            raise _reject(reasons.wrong_type(extension))

        from PIL import Image, UnidentifiedImageError

        Image.MAX_IMAGE_PIXELS = limits.max_image_pixels
        try:
            with warnings.catch_warnings():
                # Pillow only warns between the limit and twice the limit; refuse those too.
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    width, height = image.size
                    image_format = image.format
                    if width * height > limits.max_image_pixels:
                        raise _reject(reasons.image_too_large(width * height))
                    image.verify()
        except FileRejectedError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise _reject(reasons.image_too_large()) from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise _reject(reasons.corrupt_file()) from exc
        if image_format != _PILLOW_FORMATS[extension]:
            raise _reject(reasons.wrong_type(extension))
        return {"width": width, "height": height, "format": image_format}


register_kind(ImageKind())
