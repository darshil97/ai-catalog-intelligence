"""PDFs: must open, must not need a password, and must be within the page limit."""

import logging
from pathlib import Path
from typing import Any

from app.files import reasons
from app.files.reasons import FileRejectedError
from app.ingest import signatures
from app.ingest.kinds.base import CheckLimits, register_kind


def _reject(rejection: reasons.Rejection) -> FileRejectedError:
    return FileRejectedError(rejection.code, rejection.message)


class PdfKind:
    """Counts pages with pypdf, which only parses the structure, not page contents."""

    name = "pdf"
    extensions = frozenset({"pdf"})

    def check(self, path: Path, extension: str, limits: CheckLimits) -> dict[str, Any]:
        """Confirm the PDF opens without a password and has few enough pages."""
        if not signatures.is_pdf(signatures.read_head(path)):
            raise _reject(reasons.wrong_type(extension))

        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        # pypdf logs every repair it makes to a damaged file; those aren't our errors.
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        try:
            reader = PdfReader(path, strict=False)
            if reader.is_encrypted:
                # Many PDFs are encrypted only to restrict printing, and open with no password.
                try:
                    opened = reader.decrypt("")
                except (PyPdfError, NotImplementedError) as exc:
                    raise _reject(reasons.encrypted_file()) from exc
                if not opened:
                    raise _reject(reasons.encrypted_file())
            pages = len(reader.pages)
        except FileRejectedError:
            raise
        except (PyPdfError, ValueError, KeyError, TypeError, OSError, RecursionError) as exc:
            raise _reject(reasons.corrupt_file()) from exc
        if pages == 0:
            raise _reject(reasons.corrupt_file())
        if limits.max_pdf_pages is not None and pages > limits.max_pdf_pages:
            raise _reject(reasons.too_many_pages(pages, limits.max_pdf_pages))
        return {"pages": pages}


register_kind(PdfKind())
