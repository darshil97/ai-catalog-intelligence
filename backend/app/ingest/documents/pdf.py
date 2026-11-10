"""Reading PDFs: text with positions, scanned pages rendered for OCR, and embedded images.

pdfplumber reads the words a PDF holds, with their positions in points. pypdfium2 renders a
page to pixels when it holds no usable text, and extracts embedded images. Both load lazily.
"""

import hashlib
import io
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ingest.documents.pages import METHOD_OCR, METHOD_TEXT, Line, Page
from app.ocr import OcrEngine

_POINTS_PER_INCH = 72


class PdfUnreadableError(Exception):
    """The PDF, or one page of it, couldn't be read."""


@contextmanager
def open_pdf(path: Path) -> Iterator[Any]:
    """Open a PDF with pdfplumber, closing it afterwards.

    Raises:
        PdfUnreadableError: if it can't be opened.
    """
    import pdfplumber

    # pdfminer logs every oddity it repairs in a damaged file; those aren't our errors.
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    try:
        document = pdfplumber.open(path)
    except Exception as exc:  # noqa: BLE001 - any parser failure means the same thing here
        raise PdfUnreadableError(f"{type(exc).__name__}: {exc}") from exc
    try:
        yield document
    finally:
        document.close()


def text_page(plumber_page: Any, number: int) -> Page:
    """Return a page's own text, line by line, with positions in points.

    Raises:
        PdfUnreadableError: if the page can't be parsed.
    """
    try:
        raw_lines = plumber_page.extract_text_lines(strip=True, return_chars=False)
    except Exception as exc:  # noqa: BLE001 - pdfminer raises many types on bad pages
        raise PdfUnreadableError(f"{type(exc).__name__}: {exc}") from exc
    lines = [
        Line(
            # Postgres text can't hold NUL, and no real value contains one.
            text=str(raw["text"]).replace("\x00", ""),
            box=(float(raw["x0"]), float(raw["top"]), float(raw["x1"]), float(raw["bottom"])),
        )
        for raw in raw_lines
        if str(raw.get("text", "")).strip()
    ]
    return Page(
        number=number,
        method=METHOD_TEXT,
        lines=lines,
        width=float(plumber_page.width),
        height=float(plumber_page.height),
    )


def ocr_page(
    path: Path, number: int, engine: OcrEngine, dpi: int, min_confidence: float
) -> tuple[Page, float]:
    """Render a page and read it with OCR. Positions are converted back to points.

    Returns:
        The page, and its mean line confidence (0 if nothing was read).

    Raises:
        PdfUnreadableError: if the page can't be rendered.
    """
    import pypdfium2 as pdfium

    scale = dpi / _POINTS_PER_INCH
    try:
        document = pdfium.PdfDocument(path)
        try:
            page = document[number - 1]
            width, height = page.get_size()
            image = page.render(scale=scale).to_pil()
        finally:
            document.close()
    except Exception as exc:  # noqa: BLE001 - pdfium raises its own and OS errors
        raise PdfUnreadableError(f"{type(exc).__name__}: {exc}") from exc
    result = engine.read(image, min_confidence)
    lines = [
        Line(text=line.text, box=tuple(value / scale for value in line.box))  # type: ignore[arg-type]
        for line in result.lines
    ]
    confidence = (
        sum(line.confidence for line in result.lines) / len(result.lines) if result.lines else 0.0
    )
    page_out = Page(
        number=number,
        method=METHOD_OCR,
        lines=lines,
        width=float(width),
        height=float(height),
        confidence=confidence,
    )
    return page_out, confidence


@dataclass(frozen=True)
class EmbeddedImage:
    """An image found inside a PDF, encoded as a file of its own."""

    page: int
    index: int
    data: bytes
    extension: str
    width: int
    height: int
    sha256: str

    @property
    def name(self) -> str:
        """A file name that says where it came from."""
        return f"page-{self.page}-image-{self.index}.{self.extension}"


def embedded_images(path: Path, min_side: int, max_images: int) -> tuple[list[EmbeddedImage], int]:
    """Extract a PDF's images in page order, skipping small ones and repeats.

    Returns:
        The images kept, and how many more were found past `max_images`.

    Raises:
        PdfUnreadableError: if the PDF can't be opened.
    """
    import pypdfium2 as pdfium

    kept: list[EmbeddedImage] = []
    seen: set[str] = set()
    over = 0
    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:  # noqa: BLE001
        raise PdfUnreadableError(f"{type(exc).__name__}: {exc}") from exc
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            index = 0
            for obj in page.get_objects(filter=(pdfium.raw.FPDF_PAGEOBJ_IMAGE,), max_depth=3):
                try:
                    width, height = obj.get_px_size()
                    if min(width, height) < min_side:
                        continue
                    image = obj.get_bitmap(render=False).to_pil()
                except Exception:  # noqa: BLE001, S112 - an image pdfium can't decode is skipped
                    continue
                data, extension = _encode(image)
                digest = hashlib.sha256(data).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                if len(kept) >= max_images:
                    over += 1
                    continue
                index += 1
                kept.append(
                    EmbeddedImage(
                        page=page_index + 1,
                        index=index,
                        data=data,
                        extension=extension,
                        width=image.width,
                        height=image.height,
                        sha256=digest,
                    )
                )
    finally:
        document.close()
    return kept, over


def _encode(image: Any) -> tuple[bytes, str]:
    """Encode an image as JPEG if it is a plain colour photo, otherwise as PNG."""
    out = io.BytesIO()
    if image.mode in ("RGB", "L", "CMYK"):
        image.convert("RGB").save(out, format="JPEG", quality=90)
        return out.getvalue(), "jpg"
    image.convert("RGBA").save(out, format="PNG")
    return out.getvalue(), "png"
