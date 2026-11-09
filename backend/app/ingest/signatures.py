"""File signatures (magic bytes): what a file's first bytes say it is, whatever its name."""

from pathlib import Path

PDF = b"%PDF-"
PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"
ZIP_LOCAL = b"PK\x03\x04"
ZIP_EMPTY = b"PK\x05\x06"
# Microsoft's compound file format: old .xls files, and password-protected .xlsx files.
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
GIF = (b"GIF87a", b"GIF89a")

# PDF allows junk before the header, and readers look this far for it.
_PDF_SEARCH_BYTES = 1024
HEAD_BYTES = 1024


def read_head(path: Path, size: int = HEAD_BYTES) -> bytes:
    """Return a file's first bytes."""
    with path.open("rb") as handle:
        return handle.read(size)


def is_pdf(head: bytes) -> bool:
    return PDF in head[:_PDF_SEARCH_BYTES]


def is_png(head: bytes) -> bool:
    return head.startswith(PNG)


def is_jpeg(head: bytes) -> bool:
    return head.startswith(JPEG)


def is_webp(head: bytes) -> bool:
    return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def is_zip(head: bytes) -> bool:
    return head.startswith((ZIP_LOCAL, ZIP_EMPTY))


def is_ole(head: bytes) -> bool:
    return head.startswith(OLE)


def looks_binary(head: bytes) -> bool:
    """True if the bytes start like a known binary format."""
    return (
        is_pdf(head[:8])
        or is_png(head)
        or is_jpeg(head)
        or is_webp(head)
        or is_zip(head)
        or is_ole(head)
        or head.startswith(GIF)
    )
