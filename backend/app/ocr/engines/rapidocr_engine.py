"""RapidOCR: PaddleOCR's models run with ONNX Runtime on the CPU. The models ship inside the
package, so nothing is downloaded at runtime.

Boxes come back as four corner points, possibly rotated; they are reduced to the rectangle
around them.
"""

import logging
from typing import Any

from app.ocr.base import OcrLine, OcrResult, reading_order, register_ocr_engine


class RapidOcrEngine:
    """The production engine (D7)."""

    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr import RapidOCR

        # RapidOCR logs every model it loads at INFO, on its own logger.
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        self._engine = RapidOCR()

    @property
    def version(self) -> str:
        """The package version and the models it uses."""
        import importlib.metadata

        return f"rapidocr {importlib.metadata.version('rapidocr')} PP-OCRv6 small"

    def read(self, image: Any, min_confidence: float) -> OcrResult:
        """Read a PIL image."""
        import numpy as np

        rgb = image.convert("RGB")
        # The full pipeline (detect, classify, recognise) returns all three of these.
        result: Any = self._engine(np.asarray(rgb))
        lines = []
        if result.txts is not None and result.boxes is not None:
            for text, box, score in zip(result.txts, result.boxes, result.scores, strict=True):
                if float(score) < min_confidence or not str(text).strip():
                    continue
                xs = [float(point[0]) for point in box]
                ys = [float(point[1]) for point in box]
                lines.append(
                    OcrLine(
                        text=str(text).strip(),
                        box=(min(xs), min(ys), max(xs), max(ys)),
                        confidence=float(score),
                    )
                )
        return OcrResult(lines=reading_order(lines), width=rgb.width, height=rgb.height)


register_ocr_engine(RapidOcrEngine.name, RapidOcrEngine)
