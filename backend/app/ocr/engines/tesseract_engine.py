"""Tesseract, for the comparison with RapidOCR only (D7). It isn't installed on the worker: it
needs the tesseract program (installed in the local conda environment) and pytesseract (a dev
dependency).

Tesseract reports words; words are joined into its own lines, and a line's confidence is the
mean of its words'.
"""

from collections import defaultdict
from typing import Any

from app.ocr.base import OcrLine, OcrResult, reading_order, register_ocr_engine


class TesseractEngine:
    """Tesseract 5, English, automatic page layout."""

    name = "tesseract"

    @property
    def version(self) -> str:
        """The tesseract program's version."""
        import pytesseract

        return f"tesseract {pytesseract.get_tesseract_version()}"

    def read(self, image: Any, min_confidence: float) -> OcrResult:
        """Read a PIL image."""
        import pytesseract

        rgb = image.convert("RGB")
        data = pytesseract.image_to_data(rgb, output_type=pytesseract.Output.DICT)
        words: dict[tuple[int, int, int], list[tuple[str, float, tuple[int, int, int, int]]]]
        words = defaultdict(list)
        for index, text in enumerate(data["text"]):
            confidence = float(data["conf"][index])
            if not str(text).strip() or confidence < 0:
                continue
            key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
            box = (
                data["left"][index],
                data["top"][index],
                data["left"][index] + data["width"][index],
                data["top"][index] + data["height"][index],
            )
            words[key].append((str(text), confidence / 100, box))
        lines = []
        for entries in words.values():
            confidence = sum(entry[1] for entry in entries) / len(entries)
            if confidence < min_confidence:
                continue
            lines.append(
                OcrLine(
                    text=" ".join(entry[0] for entry in entries),
                    box=(
                        float(min(entry[2][0] for entry in entries)),
                        float(min(entry[2][1] for entry in entries)),
                        float(max(entry[2][2] for entry in entries)),
                        float(max(entry[2][3] for entry in entries)),
                    ),
                    confidence=confidence,
                )
            )
        return OcrResult(lines=reading_order(lines), width=rgb.width, height=rgb.height)


register_ocr_engine(TesseractEngine.name, TesseractEngine)
