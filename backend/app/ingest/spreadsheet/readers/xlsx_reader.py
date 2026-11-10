"""XLSX workbooks, read as a stream so a large sheet doesn't have to fit in memory.

The workbook is opened twice: once for the values Excel last saved, and once for formulas, so a
formula that was never calculated can be told apart from an empty cell. Merged ranges aren't
available when streaming, so they are read from the sheet's XML, and each range's first value is
shown in all of its cells.
"""

import re
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.ingest.spreadsheet.base import (
    EMPTY_CELL,
    ERROR_VALUE,
    FORMULA_WITHOUT_VALUE,
    PRECISION_LOST,
    Cell,
    ReadLimits,
    Sheet,
    SheetRow,
    SpreadsheetUnreadableError,
    Workbook,
    column_index,
    column_letter,
    register_reader,
)
from app.ingest.spreadsheet.render import PRECISION_LIMIT, render_value
from app.utils.logger import get_logger

logger = get_logger(__name__)

_RANGE = re.compile(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$")
_MERGE_TAG = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}mergeCell"
_GENERAL = "General"

# First row, first column, last row, last column. Rows count from 1, and columns from 0.
MergedRange = tuple[int, int, int, int]


def _merged_ranges(path: Path, member: str, limit: int) -> list[MergedRange]:
    """Return the sheet's merged ranges, or none if there are too many to be a real layout."""
    from defusedxml.ElementTree import iterparse

    ranges: list[MergedRange] = []
    with zipfile.ZipFile(path) as archive, archive.open(member) as sheet_xml:
        for _event, element in iterparse(sheet_xml):
            if element.tag == _MERGE_TAG:
                match = _RANGE.match(element.get("ref", ""))
                if match:
                    ranges.append(
                        (
                            int(match.group(2)),
                            column_index(match.group(1)),
                            int(match.group(4)),
                            column_index(match.group(3)),
                        )
                    )
                if len(ranges) > limit:
                    logger.warning("Merged ranges ignored: member=%s limit=%d", member, limit)
                    return []
            # Rows are discarded as they pass, so a large sheet isn't held in memory.
            element.clear()
    return ranges


class MergeFiller:
    """Shows each merged range's first value in all of the range's cells, row by row.

    Rows must be passed in order. Only ranges that reach the current row are held.
    """

    def __init__(self, ranges: list[MergedRange], max_columns: int) -> None:
        self._starting: dict[int, list[MergedRange]] = defaultdict(list)
        for merged in ranges:
            self._starting[merged[0]].append(merged)
        self._active: list[tuple[MergedRange, Cell, str]] = []
        self._max_columns = max_columns

    def fill(self, number: int, cells: list[Cell]) -> list[Cell]:
        """Return the row with merged cells filled in. `cells` is changed in place."""
        for merged in self._starting.pop(number, ()):
            first_column = merged[1]
            anchor = cells[first_column] if first_column < len(cells) else EMPTY_CELL
            if not anchor.empty:
                self._active.append((merged, anchor, f"{column_letter(first_column)}{number}"))
        self._active = [item for item in self._active if item[0][2] >= number]
        for (first_row, first_column, _last_row, last_column), anchor, ref in self._active:
            last = min(last_column, self._max_columns - 1)
            if len(cells) <= last:
                cells.extend([EMPTY_CELL] * (last + 1 - len(cells)))
            for column in range(first_column, last + 1):
                if (number, column) != (first_row, first_column) and cells[column].empty:
                    cells[column] = replace(anchor, merged_from=ref)
        return cells


def _cell(value_cell: Any, formula_cell: Any) -> Cell:
    value = value_cell.value
    if formula_cell is not None and formula_cell.data_type == "f" and value is None:
        return Cell(flags=(FORMULA_WITHOUT_VALUE,))
    if value is None:
        return EMPTY_CELL
    number_format = getattr(value_cell, "number_format", None)
    text = render_value(value, number_format)
    if value_cell.data_type == "e":
        return Cell(text=text, flags=(ERROR_VALUE,))
    is_number = isinstance(value, int | float) and not isinstance(value, bool)
    flags = (PRECISION_LOST,) if is_number and abs(value) >= PRECISION_LIMIT else ()
    shown_format = number_format if is_number and number_format != _GENERAL else None
    return Cell(text=text, flags=flags, number_format=shown_format)


class _XlsxSheet:
    def __init__(
        self, path: Path, index: int, values: Any, formulas: Any, limits: ReadLimits
    ) -> None:
        self._path = path
        self._values = values
        self._formulas = formulas
        self._limits = limits
        self._ranges: list[MergedRange] | None = None
        self.index = index
        self.name: str | None = values.title
        self.hidden = values.sheet_state != "visible"

    def rows(self) -> Iterator[SheetRow]:
        """Yield every row from the top, with merged ranges filled in.

        Raises:
            SpreadsheetUnreadableError: if the sheet's contents can't be parsed.
        """
        try:
            if self._ranges is None:
                self._ranges = _merged_ranges(
                    self._path, self._values._worksheet_path, self._limits.max_merged_ranges
                )
            filler = MergeFiller(self._ranges, self._limits.max_columns)
            # The size a file declares for a sheet can be wrong, so the real cells decide.
            self._values.reset_dimensions()
            self._formulas.reset_dimensions()
            pairs = zip(self._values.iter_rows(), self._formulas.iter_rows(), strict=False)
            for number, (value_row, formula_row) in enumerate(pairs, start=1):
                cells = [
                    _cell(value_cell, formula_row[column] if column < len(formula_row) else None)
                    for column, value_cell in enumerate(value_row[: self._limits.max_columns])
                ]
                yield SheetRow(number=number, cells=tuple(filler.fill(number, cells)))
        except SpreadsheetUnreadableError:
            raise
        except Exception as exc:  # noqa: BLE001 - any parser failure means the same thing here
            raise SpreadsheetUnreadableError(f"{type(exc).__name__}: {exc}") from exc


class _XlsxWorkbook:
    def __init__(self, sheets: list[Sheet], sheets_over_limit: int) -> None:
        self.sheets = sheets
        self.sheets_over_limit = sheets_over_limit


class XlsxReader:
    """Reads Excel workbooks. Chart sheets are ignored, and hidden sheets are marked."""

    name = "xlsx"
    extensions = frozenset({"xlsx"})

    @contextmanager
    def open(self, path: Path, limits: ReadLimits) -> Iterator[Workbook]:
        """Open the workbook for streaming, and close it afterwards."""
        import openpyxl

        try:
            values = openpyxl.load_workbook(path, read_only=True, data_only=True)
            formulas = openpyxl.load_workbook(path, read_only=True, data_only=False)
        except Exception as exc:  # noqa: BLE001 - any parser failure means the same thing here
            raise SpreadsheetUnreadableError(f"{type(exc).__name__}: {exc}") from exc
        try:
            pairs = list(zip(values.worksheets, formulas.worksheets, strict=True))
            sheets: list[Sheet] = [
                _XlsxSheet(path, index, value_sheet, formula_sheet, limits)
                for index, (value_sheet, formula_sheet) in enumerate(pairs[: limits.max_sheets])
            ]
            yield _XlsxWorkbook(sheets, max(0, len(pairs) - limits.max_sheets))
        finally:
            values.close()
            formulas.close()


register_reader(XlsxReader())
