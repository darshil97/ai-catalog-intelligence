"""The SpreadsheetReader contract, its registry, and the shapes readers produce.

A reader turns a file into sheets of rows of cells. Every cell's text is what the file shows,
and nothing is interpreted here: deciding what a column means belongs to mapping.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Cell flags. A flagged cell holds no usable value, and is reported instead of recorded.
FORMULA_WITHOUT_VALUE = "formula_without_value"
ERROR_VALUE = "error_value"
# The file stores this number with more digits than a spreadsheet keeps, so some were lost.
PRECISION_LOST = "precision_lost"
UNUSABLE_FLAGS = frozenset({FORMULA_WITHOUT_VALUE, ERROR_VALUE})


@dataclass(frozen=True)
class Cell:
    """One cell.

    Attributes:
        text: The value as shown, or "" for an empty cell.
        flags: Problems with the cell, from the constants in this module.
        merged_from: The cell that holds this value, such as "B3", when this cell is part of a
            merged range and isn't its first cell.
        number_format: The spreadsheet's display format, when the file stores a number.
    """

    text: str = ""
    flags: tuple[str, ...] = ()
    merged_from: str | None = None
    number_format: str | None = None

    @property
    def empty(self) -> bool:
        """True if the cell shows nothing."""
        return not self.text.strip()

    @property
    def usable(self) -> bool:
        """True if the cell holds a value that can be recorded."""
        return not self.empty and not UNUSABLE_FLAGS.intersection(self.flags)


EMPTY_CELL = Cell()


@dataclass(frozen=True)
class SheetRow:
    """One row. `error` is set, and `cells` is empty, when the row couldn't be read."""

    number: int
    cells: tuple[Cell, ...] = ()
    error: str | None = None

    @property
    def empty(self) -> bool:
        """True if every cell is empty."""
        return self.error is None and all(cell.empty for cell in self.cells)

    def cell(self, index: int) -> Cell:
        """Return the cell in a column, counting from 0. Missing cells are empty."""
        return self.cells[index] if index < len(self.cells) else EMPTY_CELL


@dataclass(frozen=True)
class ReadLimits:
    """Bounds on what is read from one file."""

    max_columns: int
    max_sheets: int
    max_merged_ranges: int
    csv_sniff_bytes: int
    max_file_bytes: int


class Sheet(Protocol):
    """One sheet. Row numbers start at 1 and match what a spreadsheet program shows."""

    index: int
    # None for a CSV, which has no sheets.
    name: str | None
    hidden: bool

    def rows(self) -> Iterator[SheetRow]:
        """Yield every row from the top, including empty ones. Can be called again."""
        ...


class Workbook(Protocol):
    """An open file's sheets, in the file's order."""

    sheets: list[Sheet]
    # Sheets beyond the limit, which weren't opened.
    sheets_over_limit: int


class SpreadsheetReader(Protocol):
    """Opens one family of spreadsheet files."""

    name: str
    extensions: frozenset[str]

    def open(self, path: Path, limits: ReadLimits) -> AbstractContextManager[Workbook]:
        """Open the file. Leaving the context closes it.

        Raises:
            SpreadsheetUnreadableError: if the file can't be read as this kind of spreadsheet.
        """
        ...


class SpreadsheetUnreadableError(Exception):
    """A file or sheet that passed the upload checks still couldn't be read."""


_REGISTRY: dict[str, SpreadsheetReader] = {}


def register_reader(reader: SpreadsheetReader) -> SpreadsheetReader:
    """Make a reader responsible for its extensions."""
    for extension in reader.extensions:
        _REGISTRY[extension] = reader
    logger.info(
        "Registered spreadsheet reader: name=%s extensions=%s",
        reader.name,
        sorted(reader.extensions),
    )
    return reader


def get_reader(extension: str) -> SpreadsheetReader:
    """Return the reader for an extension.

    Raises:
        ValueError: if no reader handles it, naming the extensions that are handled.
    """
    import app.ingest.spreadsheet.readers  # noqa: F401 - registers the readers

    if extension not in _REGISTRY:
        raise ValueError(
            f"No spreadsheet reader for .{extension}. Registered: {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[extension]


def column_letter(index: int) -> str:
    """Return a column's letters, counting from 0: 0 is A, 25 is Z, 26 is AA."""
    letters = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def column_index(letters: str) -> int:
    """Return a column's position, counting from 0, from its letters.

    Raises:
        ValueError: if the text isn't column letters.
    """
    if not letters.isascii() or not letters.isalpha():
        raise ValueError(f"Not a column: {letters!r}")
    number = 0
    for letter in letters.upper():
        number = number * 26 + (ord(letter) - ord("A") + 1)
    return number - 1
