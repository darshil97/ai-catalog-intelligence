"""Reading text from images, used by the worker only. Engines load their models on first use."""

from app.ocr.base import (
    OcrEngine,
    OcrLine,
    OcrResult,
    get_ocr_engine,
    get_registered_ocr_engine_names,
    reading_order,
    register_ocr_engine,
)

__all__ = [
    "OcrEngine",
    "OcrLine",
    "OcrResult",
    "get_ocr_engine",
    "get_registered_ocr_engine_names",
    "reading_order",
    "register_ocr_engine",
]
