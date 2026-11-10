"""CSV and XLSX. Their rows are read by app.ingest.spreadsheet; this only confirms what they are."""

import codecs
import zipfile
from pathlib import Path
from typing import Any

from app.files import reasons
from app.files.reasons import FileRejectedError
from app.ingest import signatures
from app.ingest.kinds.base import CheckLimits, register_kind

_UTF16_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
# The name of the stream holding a password-protected workbook's key, in UTF-16.
_ENCRYPTION_INFO = "EncryptionInfo".encode("utf-16-le")
# Share of control characters above which "text" is really binary.
_MAX_CONTROL_SHARE = 0.01
_ALLOWED_CONTROLS = {"\t", "\n", "\r", "\f"}


def _reject(rejection: reasons.Rejection) -> FileRejectedError:
    return FileRejectedError(rejection.code, rejection.message)


def detect_encoding(head: bytes) -> str | None:
    """Return the text encoding a CSV seems to use (utf-8, utf-16 or cp1252), or None if it
    isn't text. `head` is the start of the file."""
    if head.startswith(_UTF16_BOMS):
        try:
            codecs.getincrementaldecoder("utf-16")().decode(head, final=False)
        except UnicodeDecodeError:
            return None
        return "utf-16"
    if b"\x00" in head:
        return None
    try:
        # Not final: the sample may end partway through a character.
        text = codecs.getincrementaldecoder("utf-8-sig")().decode(head, final=False)
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = head.decode("cp1252", errors="replace")
        encoding = "cp1252"
    controls = sum(1 for char in text if ord(char) < 32 and char not in _ALLOWED_CONTROLS)
    if text and controls / len(text) > _MAX_CONTROL_SHARE:
        return None
    return encoding


class CsvKind:
    """Plain text with no binary signature. The encoding is recorded with the file."""

    name = "csv"
    extensions = frozenset({"csv"})

    def check(self, path: Path, extension: str, limits: CheckLimits) -> dict[str, Any]:
        """Confirm the file is text."""
        head = signatures.read_head(path, limits.csv_sniff_bytes)
        if signatures.looks_binary(head):
            raise _reject(reasons.wrong_type(extension))
        encoding = detect_encoding(head)
        if encoding is None:
            raise _reject(reasons.wrong_type(extension))
        return {"encoding": encoding}


class XlsxKind:
    """An Office Open XML workbook: a ZIP containing xl/workbook.xml."""

    name = "xlsx"
    extensions = frozenset({"xlsx"})

    def check(self, path: Path, extension: str, limits: CheckLimits) -> dict[str, Any]:
        """Confirm the file is an unencrypted workbook that doesn't expand absurdly."""
        head = signatures.read_head(path)
        if signatures.is_ole(head):
            if _ENCRYPTION_INFO in path.read_bytes():
                raise _reject(reasons.encrypted_file())
            raise _reject(reasons.wrong_type(extension))
        if not signatures.is_zip(head):
            raise _reject(reasons.wrong_type(extension))
        try:
            with zipfile.ZipFile(path) as workbook:
                infos = workbook.infolist()
        except (zipfile.BadZipFile, OSError) as exc:
            raise _reject(reasons.corrupt_file()) from exc
        names = {info.filename for info in infos}
        if "xl/workbook.xml" not in names or "[Content_Types].xml" not in names:
            raise _reject(reasons.wrong_type(extension))
        # Declared sizes are enough here: the sheets are read later with their own limits.
        if sum(info.file_size for info in infos) > limits.zip_max_total_bytes:
            raise _reject(reasons.zip_too_large(limits.zip_max_total_bytes))
        sheets = sum(
            1 for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        return {"sheets": sheets}


register_kind(CsvKind())
register_kind(XlsxKind())
