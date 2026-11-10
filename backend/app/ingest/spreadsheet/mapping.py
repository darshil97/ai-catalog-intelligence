"""Decides what each column of a sheet means.

Known column names are matched first, for free and the same way every time. If that leaves a
named column unplaced, or no header row was found, a mapping saved for this supplier's layout
is reused, and failing that the LLM is asked once for the sheet. The LLM only says what columns
mean: values are always read from the cells by code.

This module doesn't touch the database or the model. The caller passes in how to find a saved
mapping and how to ask the LLM, which keeps the rules here testable on their own.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.ingest.spreadsheet.base import SheetRow, column_letter
from app.ingest.spreadsheet.headers import (
    HeaderGuess,
    combine_headers,
    detect_header,
    header_fingerprint,
    match_known,
    normalize_header,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# How a sheet's columns were mapped. Stored on the sheet and shown to the user.
SOURCE_NONE = "none"
SOURCE_KNOWN_NAMES = "known_names"
SOURCE_SAVED = "saved"
SOURCE_LLM = "llm"
SOURCE_LLM_FAILED = "llm_failed"
SOURCE_LLM_CAPPED = "llm_capped"
SOURCE_LLM_BUDGET = "llm_budget"

# How one column was mapped.
COLUMN_KNOWN_NAME = "known_name"
COLUMN_LLM = "llm"

_MAX_REASON_LENGTH = 200


@dataclass(frozen=True)
class AttributeDef:
    """An attribute columns can be mapped to."""

    id: UUID
    code: str
    name: str
    data_type: str
    description: str


@dataclass(frozen=True)
class MappedColumn:
    """One column and what it was mapped to.

    Attributes:
        index: Position, counting from 0.
        letter: The column's letters, such as "C".
        header: Its name as written, "" if it has none.
        attribute: The attribute code, or None if unmapped.
        source: COLUMN_KNOWN_NAME or COLUMN_LLM, or None if unmapped.
        confidence: 1.0 for a known name, the LLM's own figure otherwise.
        reason: The LLM's few words on why.
        suggestion: An attribute the LLM proposed without enough confidence to use.
        model_run_id: The LLM call that mapped or suggested it.
    """

    index: int
    letter: str
    header: str
    attribute: str | None = None
    source: str | None = None
    confidence: float | None = None
    reason: str | None = None
    suggestion: str | None = None
    model_run_id: str | None = None


@dataclass(frozen=True)
class SheetMapping:
    """The outcome for one sheet.

    Attributes:
        header_row: The header row's number, or None if none was found.
        header_rows: 1 or 2, or None if no header was found.
        columns: Every column, mapped or not.
        source: One of the SOURCE_ constants.
        message: Why the LLM wasn't used when it was needed, for the user.
        llm_calls: Calls made to the model for this sheet.
        save_as: The fingerprint to save this mapping under, or None if it shouldn't be saved.
    """

    header_row: int | None
    header_rows: int | None
    columns: tuple[MappedColumn, ...]
    source: str
    message: str | None = None
    llm_calls: int = 0
    save_as: str | None = None

    def attribute_columns(self, attribute: str) -> list[MappedColumn]:
        """Return the columns mapped to an attribute, leftmost first."""
        return [column for column in self.columns if column.attribute == attribute]

    def to_saved(self) -> dict[str, Any]:
        """Return what is stored for reuse with the same layout."""
        return {
            "header_row": self.header_row,
            "header_rows": self.header_rows,
            "headers": [normalize_header(column.header) for column in self.columns],
            "columns": [asdict(column) for column in self.columns],
        }


class ColumnAnswer(BaseModel):
    """The LLM's decision for one column."""

    column: str = Field(..., min_length=1, max_length=3, description="Column letter.")
    attribute: str | None = Field(None, description="Attribute code, or null.")
    confidence: float = Field(..., ge=0, le=1, description="How sure the model is.")
    reason: str = Field("", description="A few words on why.")


class MappingAnswer(BaseModel):
    """The LLM's reply for one sheet."""

    header_row: int = Field(..., ge=1, description="The row holding the column names.")
    columns: list[ColumnAnswer] = Field(..., description="One entry per column asked about.")


@dataclass(frozen=True)
class LlmAnswer:
    """A checked reply and the record of the call that produced it."""

    answer: MappingAnswer
    model_run_id: UUID
    calls: int


class LlmMappingError(Exception):
    """The LLM gave no usable mapping. `calls` is how many calls were made trying.

    `budget` is True when no call was made because the monthly LLM spend cap was reached.
    """

    def __init__(self, message: str, calls: int, *, budget: bool = False) -> None:
        super().__init__(message)
        self.calls = calls
        self.budget = budget


def make_parser(
    attribute_codes: Sequence[str], row_numbers: Sequence[int]
) -> Callable[[dict[str, Any]], MappingAnswer]:
    """Return a parser that accepts only replies using the given attributes and rows."""
    allowed = set(attribute_codes)
    shown = set(row_numbers)

    def parse(data: dict[str, Any]) -> MappingAnswer:
        answer = MappingAnswer.model_validate(data)
        if answer.header_row not in shown:
            raise ValueError(f"header_row {answer.header_row} is not one of the rows shown")
        for column in answer.columns:
            if column.attribute is not None and column.attribute not in allowed:
                raise ValueError(
                    f"{column.attribute!r} is not an attribute code. "
                    f"Use one of: {', '.join(sorted(allowed))}, or null"
                )
        return answer

    return parse


def _width(rows: Sequence[SheetRow]) -> int:
    widths = [
        max((index + 1 for index, cell in enumerate(row.cells) if not cell.empty), default=0)
        for row in rows
    ]
    return max(widths, default=0)


def _known_columns(guess: HeaderGuess, width: int, known: Mapping[str, str]) -> list[MappedColumn]:
    columns = []
    for index in range(max(width, len(guess.headers))):
        header = guess.headers[index] if index < len(guess.headers) else ""
        attribute = match_known(header, known) if header else None
        columns.append(
            MappedColumn(
                index=index,
                letter=column_letter(index),
                header=header,
                attribute=attribute,
                source=COLUMN_KNOWN_NAME if attribute else None,
                confidence=1.0 if attribute else None,
            )
        )
    return columns


def _preview(
    rows: Sequence[SheetRow], header_row: int | None, sample_rows: int, cell_chars: int
) -> list[dict[str, Any]]:
    """Return the rows shown to the LLM: everything down to the header, then a few data rows."""
    shown, after = [], 0
    for row in rows:
        if row.error is not None or row.empty:
            continue
        if header_row is not None and row.number > header_row:
            after += 1
            if after > sample_rows:
                break
        cells = [
            {"letter": column_letter(index), "text": cell.text.strip()[:cell_chars]}
            for index, cell in enumerate(row.cells)
            if not cell.empty
        ]
        shown.append({"number": row.number, "cells": cells})
        if header_row is None and len(shown) >= sample_rows * 2:
            break
    return shown


def _from_saved(
    saved: Mapping[str, Any],
    rows: Sequence[SheetRow],
    merged: bool,
    attribute_codes: set[str],
) -> SheetMapping | None:
    """Rebuild a saved mapping, or return None if it no longer fits this sheet."""
    header_row = saved.get("header_row")
    if not isinstance(header_row, int) or header_row not in {row.number for row in rows}:
        return None
    guess = combine_headers(rows, header_row, merged)
    columns = []
    for entry in saved.get("columns", []):
        try:
            column = MappedColumn(**entry)
        except TypeError:
            return None
        if column.attribute is not None and column.attribute not in attribute_codes:
            return None
        columns.append(column)
    headers = [normalize_header(name) for name in guess.headers]
    saved_headers = [normalize_header(column.header) for column in columns]
    if headers != saved_headers[: len(headers)] or any(saved_headers[len(headers) :]):
        return None
    return SheetMapping(
        header_row=guess.row,
        header_rows=guess.rows,
        columns=tuple(columns),
        source=SOURCE_SAVED,
    )


def map_sheet(
    top_rows: Sequence[SheetRow],
    *,
    merged: bool,
    attributes: Sequence[AttributeDef],
    known: Mapping[str, str],
    settings: Mapping[str, Any],
    find_saved: Callable[[str], Mapping[str, Any] | None],
    ask_llm: Callable[[dict[str, Any], Callable[[dict[str, Any]], MappingAnswer]], LlmAnswer]
    | None,
) -> SheetMapping:
    """Map one sheet's columns.

    Args:
        top_rows: The sheet's first rows: at least the header scan window plus the sample rows.
        merged: True if the file can merge cells (a workbook), False for a CSV.
        attributes: Every attribute a column may be mapped to.
        known: Normalized column name to attribute code, from build_known_names.
        settings: The extraction config section.
        find_saved: Returns the mapping saved under a fingerprint, or None.
        ask_llm: Sends the prompt variables to the LLM with the given parser, and raises
            LlmMappingError if no usable reply comes back. None means the run has used up its
            LLM calls.
    """
    codes = [attribute.code for attribute in attributes]
    scan = [row for row in top_rows if row.number <= settings["header_scan_rows"]]
    width = _width(top_rows)
    guess = detect_header(scan, known, merged)
    columns = _known_columns(guess, width, known) if guess else []
    unplaced = [column for column in columns if column.header and column.attribute is None]

    if guess is not None and guess.confident and not unplaced:
        return SheetMapping(guess.row, guess.rows, tuple(columns), SOURCE_KNOWN_NAMES)

    fingerprint = (
        header_fingerprint(guess.headers, codes, settings["mapping_version"]) if guess else None
    )
    if fingerprint is not None:
        saved = find_saved(fingerprint)
        restored = _from_saved(saved, scan, merged, set(codes)) if saved else None
        if restored is not None:
            return restored

    def without_llm(source: str, message: str, calls: int = 0) -> SheetMapping:
        if guess is None:
            return SheetMapping(None, None, (), source, message, calls)
        return SheetMapping(guess.row, guess.rows, tuple(columns), source, message, calls)

    if ask_llm is None:
        return without_llm(
            SOURCE_LLM_CAPPED,
            "This run had already used its LLM calls, so only known column names were used.",
        )

    preview = _preview(
        top_rows,
        guess.row if guess else None,
        settings["sample_rows"],
        settings["sample_cell_chars"],
    )
    to_map = (
        [column.letter for column in unplaced]
        if guess
        else [column_letter(index) for index in range(width)]
    )
    variables = {
        "attributes": [asdict(attribute) for attribute in attributes],
        "guessed_header_row": guess.row if guess else None,
        "known_columns": [
            {"letter": column.letter, "attribute": column.attribute}
            for column in columns
            if column.attribute
        ],
        "columns_to_map": to_map,
        "rows": preview,
    }
    parser = make_parser(codes, [row["number"] for row in preview])
    try:
        reply = ask_llm(variables, parser)
    except LlmMappingError as exc:
        logger.warning("Column mapping fell back to known names: error=%s", exc)
        if exc.budget:
            return without_llm(
                SOURCE_LLM_BUDGET,
                "This month's LLM budget has been used, so only known column names were used.",
            )
        return without_llm(
            SOURCE_LLM_FAILED,
            "The LLM couldn't be used for this sheet, so only known column names were used.",
            exc.calls,
        )

    answer = reply.answer
    header = next((row for row in scan if row.number == answer.header_row), None)
    if (
        header is not None
        and not header.empty
        and (guess is None or answer.header_row != guess.row)
    ):
        # The LLM placed the header elsewhere, so the names are read again from its row.
        guess = combine_headers(scan, answer.header_row, merged)
        columns = _known_columns(guess, width, known)
    if guess is None:
        return without_llm(
            SOURCE_LLM_FAILED, "No header row could be found in this sheet.", reply.calls
        )

    by_letter = {item.column.upper(): item for item in answer.columns}
    minimum = settings["min_llm_confidence"]
    final = []
    for column in columns:
        item = by_letter.get(column.letter)
        if column.attribute is not None or not column.header or item is None:
            final.append(column)
            continue
        accepted = item.attribute is not None and item.confidence >= minimum
        final.append(
            MappedColumn(
                index=column.index,
                letter=column.letter,
                header=column.header,
                attribute=item.attribute if accepted else None,
                source=COLUMN_LLM if accepted else None,
                confidence=item.confidence if item.attribute is not None else None,
                reason=item.reason.strip()[:_MAX_REASON_LENGTH] or None,
                suggestion=None if accepted else item.attribute,
                model_run_id=str(reply.model_run_id),
            )
        )
    return SheetMapping(
        header_row=guess.row,
        header_rows=guess.rows,
        columns=tuple(final),
        source=SOURCE_LLM,
        llm_calls=reply.calls,
        save_as=fingerprint,
    )
