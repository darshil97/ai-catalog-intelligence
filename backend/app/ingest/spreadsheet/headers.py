"""Finds a sheet's header row and matches its columns to known names.

Everything here is deterministic. Columns it can't place are left for the LLM, and a sheet
whose header row it can't find is too.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.ingest.spreadsheet.base import SheetRow

_NOT_ALPHANUMERIC = re.compile(r"[^0-9a-z]+")
_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_NUMERIC = re.compile(r"^[\s\d.,+\-%€$£¥₹]+$")
_CELL_REF = re.compile(r"^[A-Z]+(\d+)$")
GROUP_SEPARATOR = " / "


def normalize_header(text: str) -> str:
    """Return a header reduced to lowercase words, so "Art.-Nr." and "art nr" compare equal."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    # Accents are dropped only when a plain letter remains, so non-Latin headers survive.
    decomposed = unicodedata.normalize("NFKD", folded)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NOT_ALPHANUMERIC.sub(" ", plain).strip()


def build_known_names(header_names: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """Return normalized column name to attribute code.

    Raises:
        ValueError: if two attributes claim the same name, naming it.
    """
    known: dict[str, str] = {}
    for code, names in header_names.items():
        for name in names:
            key = normalize_header(name)
            if known.get(key, code) != code:
                raise ValueError(
                    f"Column name {name!r} is listed for both {known[key]!r} and {code!r}"
                )
            known[key] = code
    return known


def match_known(header: str, known: Mapping[str, str]) -> str | None:
    """Return the attribute a header names, or None.

    A unit or note in brackets is ignored, so "Weight (g)" matches "weight". A header under a
    group, such as "Dimensions / Width", only matches as a whole: "Gross / Weight" must not
    become a plain weight.
    """
    full = normalize_header(header)
    if full in known:
        return known[full]
    if GROUP_SEPARATOR in header:
        return None
    return known.get(normalize_header(_BRACKETED.sub(" ", header)))


@dataclass(frozen=True)
class HeaderGuess:
    """Where the header seems to be.

    Attributes:
        row: The header row's number. With a two-row header, the lower row.
        rows: 1, or 2 when the row above groups columns under shared names.
        headers: One name per column, "" for a column with none.
        confident: False when no known name was found, so the row is only a guess by shape.
    """

    row: int
    rows: int
    headers: tuple[str, ...]
    confident: bool


def _anchor_row(ref: str | None) -> int | None:
    """Return the row number in a cell reference such as "B3", or None."""
    match = _CELL_REF.match(ref or "")
    return int(match.group(1)) if match else None


def _texts(row: SheetRow) -> list[str]:
    return [cell.text.strip() for cell in row.cells]


def _group_row(above: SheetRow, header: SheetRow, merged: bool) -> list[str] | None:
    """Return the group name over each column, if the row above is a group row.

    In a workbook, a group is a merged cell over several columns. A CSV can't merge, so there a
    group row has at least two names, each followed by empty cells: a lone title isn't one.
    """
    names = _texts(above)
    filled = [name for name in names if name]
    if not filled or any(_NUMERIC.match(name) for name in filled):
        return None
    header_columns = [index for index, name in enumerate(_texts(header)) if name]
    if merged:
        # Merged across, within this row: a cell merged down from a row above isn't a group.
        across = any(_anchor_row(cell.merged_from) == above.number for cell in above.cells)
        covered = {names[index] if index < len(names) else "" for index in header_columns}
        # One name over every column is a title for the table, not a group of columns.
        return names if across and covered != {filled[0]} else None
    if len(filled) < 2 or len(filled) >= len(header_columns):
        return None
    groups, current = [], ""
    for name in names:
        current = name or current
        groups.append(current)
    return groups


def combine_headers(rows: Sequence[SheetRow], header_row: int, merged: bool) -> HeaderGuess:
    """Return the column names for a header row, joined with a group row above it if any.

    Args:
        rows: The top rows of the sheet, in order.
        header_row: The number of the row holding the column names.
        merged: True if the file can merge cells (a workbook), False for a CSV.
    """
    by_number = {row.number: row for row in rows}
    header = by_number[header_row]
    names = _texts(header)
    above = by_number.get(header_row - 1)
    groups = _group_row(above, header, merged) if above is not None and not above.empty else None
    if groups is None:
        return HeaderGuess(header_row, 1, tuple(names), confident=True)
    combined = []
    for index, name in enumerate(names):
        group = groups[index] if index < len(groups) else ""
        if header.cell(index).merged_from or not group or not name or group == name:
            # A cell merged down through both rows already holds the whole name.
            combined.append(name or group)
        else:
            combined.append(f"{group}{GROUP_SEPARATOR}{name}")
    combined.extend(groups[len(names) :])
    return HeaderGuess(header_row, 2, tuple(combined), confident=True)


def detect_header(
    rows: Sequence[SheetRow], known: Mapping[str, str], merged: bool
) -> HeaderGuess | None:
    """Find the header row among a sheet's top rows.

    The row with the most known column names wins. If no row has any, the first row that looks
    like a header by shape is returned as an unconfident guess.

    Returns:
        The guess, or None if no row could be a header.
    """
    readable = [row for row in rows if row.error is None and not row.empty]
    best: tuple[tuple[int, int], HeaderGuess] | None = None
    for row in readable:
        guess = combine_headers(rows, row.number, merged)
        names = {name for name in guess.headers if name}
        matched = len({match_known(name, known) for name in names} - {None})
        # Between a group row and the names under it, the row with more distinct names is the
        # header: "Dimensions" repeated across three columns is one name.
        score = (matched, len(names))
        if matched and (best is None or score > best[0]):
            best = (score, guess)
    if best is not None:
        return best[1]

    widest = max((sum(1 for text in _texts(row) if text) for row in readable), default=0)
    for row in readable:
        texts = [text for text in _texts(row) if text]
        # A header is a row of labels: about as wide as the table, and not numbers.
        if len(texts) >= max(2, (widest + 1) // 2) and not any(_NUMERIC.match(t) for t in texts):
            guess = combine_headers(rows, row.number, merged)
            return HeaderGuess(guess.row, guess.rows, guess.headers, confident=False)
    return None


def header_fingerprint(
    headers: Sequence[str], attribute_codes: Sequence[str], mapping_version: int
) -> str:
    """Identify a header layout, so a mapping saved for it can be found again.

    The attribute list and the mapping version are part of it: when either changes, saved
    mappings stop matching and are worked out afresh.
    """
    payload = json.dumps(
        [mapping_version, sorted(attribute_codes), [normalize_header(name) for name in headers]],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()
