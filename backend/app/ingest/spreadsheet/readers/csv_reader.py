"""CSV files: one unnamed sheet of text cells.

The encoding is detected as the upload check does, and the delimiter is whichever of comma,
semicolon, tab or pipe splits the first lines into the most consistent columns.
"""

import csv
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.ingest import signatures
from app.ingest.kinds.spreadsheet import detect_encoding
from app.ingest.spreadsheet.base import (
    Cell,
    ReadLimits,
    Sheet,
    SheetRow,
    SpreadsheetUnreadableError,
    Workbook,
    register_reader,
)

_DELIMITERS = (",", ";", "\t", "|")
_SNIFF_LINES = 30
# utf-8-sig drops the byte order mark Excel adds, which would otherwise stick to the first header.
_CODECS = {"utf-8": "utf-8-sig", "utf-16": "utf-16", "cp1252": "cp1252"}


def _detect_delimiter(path: Path, codec: str) -> str:
    """Return the delimiter that gives the most rows the same number of columns."""
    with path.open(encoding=codec, errors="replace", newline="") as handle:
        lines = [line for _, line in zip(range(_SNIFF_LINES), handle, strict=False)]
    best, best_score = ",", (0, 0)
    for delimiter in _DELIMITERS:
        try:
            widths = Counter(len(row) for row in csv.reader(lines, delimiter=delimiter) if row)
        except csv.Error:
            continue
        if not widths:
            continue
        width, rows = max(widths.items(), key=lambda item: (item[1], item[0]))
        if width > 1 and (rows, width) > best_score:
            best, best_score = delimiter, (rows, width)
    return best


class _CsvSheet:
    index = 0
    name: str | None = None
    hidden = False

    def __init__(self, path: Path, codec: str, delimiter: str, limits: ReadLimits) -> None:
        self._path = path
        self._codec = codec
        self._delimiter = delimiter
        self._limits = limits

    def rows(self) -> Iterator[SheetRow]:
        """Yield every record. A record that can't be parsed is yielded with its error."""
        # A field may be as long as the file, so the row-size rule decides, not the parser.
        csv.field_size_limit(max(csv.field_size_limit(), self._limits.max_file_bytes))
        with self._path.open(encoding=self._codec, errors="replace", newline="") as handle:
            reader = csv.reader(handle, delimiter=self._delimiter)
            number = 0
            while True:
                number += 1
                try:
                    fields = next(reader)
                except StopIteration:
                    return
                except csv.Error as exc:
                    yield SheetRow(number=number, error=str(exc))
                    continue
                yield SheetRow(
                    number=number,
                    cells=tuple(
                        # Postgres text cannot hold NUL, and no real value contains one.
                        Cell(text=field.replace("\x00", ""))
                        for field in fields[: self._limits.max_columns]
                    ),
                )


class _CsvWorkbook:
    sheets_over_limit = 0

    def __init__(self, sheet: Sheet) -> None:
        self.sheets = [sheet]


class CsvReader:
    """Reads comma, semicolon, tab or pipe separated text in UTF-8, UTF-16 or Windows-1252."""

    name = "csv"
    extensions = frozenset({"csv"})

    @contextmanager
    def open(self, path: Path, limits: ReadLimits) -> Iterator[Workbook]:
        """Detect the encoding and delimiter, and expose the file as one sheet."""
        encoding = detect_encoding(signatures.read_head(path, limits.csv_sniff_bytes))
        if encoding is None:
            raise SpreadsheetUnreadableError("The file isn't text")
        codec = _CODECS[encoding]
        yield _CsvWorkbook(_CsvSheet(path, codec, _detect_delimiter(path, codec), limits))


register_reader(CsvReader())
