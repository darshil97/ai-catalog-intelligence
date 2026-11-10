"""Covers reading CSV and XLSX files into rows of cells. Needs no database, and nothing is
stubbed: the files are built here and read with the real parsers."""

import codecs
import datetime
from pathlib import Path

import pytest

import samples
from app.ingest.spreadsheet import ReadLimits, SheetRow, get_reader
from app.ingest.spreadsheet.base import (
    ERROR_VALUE,
    FORMULA_WITHOUT_VALUE,
    PRECISION_LOST,
    SpreadsheetUnreadableError,
    column_index,
    column_letter,
)
from app.ingest.spreadsheet.render import render_number, render_value

LIMITS = ReadLimits(
    max_columns=50,
    max_sheets=3,
    max_merged_ranges=100,
    csv_sniff_bytes=65536,
    max_file_bytes=10 * 1024 * 1024,
)


def _rows(tmp_path: Path, name: str, data: bytes, sheet: int = 0) -> list[SheetRow]:
    path = samples.write(tmp_path, name, data)
    with get_reader(name.rsplit(".", 1)[1]).open(path, LIMITS) as workbook:
        return list(workbook.sheets[sheet].rows())


def _texts(rows: list[SheetRow]) -> list[list[str]]:
    return [[cell.text for cell in row.cells] for row in rows]


def test_column_letters_round_trip() -> None:
    assert [column_letter(i) for i in (0, 25, 26, 27, 701, 702)] == [
        "A", "Z", "AA", "AB", "ZZ", "AAA",
    ]  # fmt: skip
    assert all(column_index(column_letter(i)) == i for i in range(1000))
    with pytest.raises(ValueError, match="Not a column"):
        column_index("A1")


@pytest.mark.parametrize(
    ("encoding", "prefix"),
    [
        ("utf-8", b""),
        ("utf-8", codecs.BOM_UTF8),
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("cp1252", b""),
    ],
)
def test_csv_encodings_keep_accents_and_drop_the_byte_order_mark(
    tmp_path: Path, encoding: str, prefix: bytes
) -> None:
    # The first header is where a byte order mark would stick if it weren't removed.
    data = prefix + samples.table_csv(
        [["sku", "name"], ["A-1", "Café crème"], ["A-2", "Grün"]], encoding=encoding
    )
    assert _texts(_rows(tmp_path, "list.csv", data)) == [
        ["sku", "name"],
        ["A-1", "Café crème"],
        ["A-2", "Grün"],
    ]


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_csv_delimiter_is_detected(tmp_path: Path, delimiter: str) -> None:
    # The decimal comma and the quoted delimiter must not be mistaken for separators.
    rows = [["sku", "name", "price"], ["A-1", f"Mug{delimiter} large", "4,50"], ["A-2", "Cup", "3"]]
    data = samples.table_csv(rows, delimiter=delimiter)
    assert _texts(_rows(tmp_path, "list.csv", data)) == rows


def test_csv_rows_are_numbered_like_a_spreadsheet_and_keep_text_exact(tmp_path: Path) -> None:
    data = b'sku,note\n"A-1","two\nlines"\n\n 007 ,x\n'
    rows = _rows(tmp_path, "list.csv", data)
    assert [row.number for row in rows] == [1, 2, 3, 4]
    assert rows[1].cells[1].text == "two\nlines"
    assert rows[2].empty
    # Spaces and leading zeros are the supplier's, and are kept as written.
    assert rows[3].cells[0].text == " 007 "


def test_csv_can_be_read_twice(tmp_path: Path) -> None:
    path = samples.write(tmp_path, "list.csv", samples.csv(3))
    with get_reader("csv").open(path, LIMITS) as workbook:
        sheet = workbook.sheets[0]
        assert sheet.name is None and not sheet.hidden
        assert _texts(list(sheet.rows())) == _texts(list(sheet.rows()))


def test_binary_file_named_csv_is_unreadable(tmp_path: Path) -> None:
    path = samples.write(tmp_path, "list.csv", b"\x00\x01\x02" * 100)
    with pytest.raises(SpreadsheetUnreadableError), get_reader("csv").open(path, LIMITS):
        pass


@pytest.mark.parametrize(
    ("value", "number_format", "shown"),
    [
        (12345.0, "General", "12345"),
        (123, "00000", "00123"),
        (12.5, "0.00", "12.50"),
        (2.675, "0.00", "2.68"),  # spreadsheets round half up; binary floats would give 2.67
        (1234.5, "#,##0.00", "1234.50"),
        (4.5, "0.0#", "4.5"),
        (0.15, "0%", "15%"),
        (0.125, "0.0%", "12.5%"),
        (8901234567890, "General", "8901234567890"),
        (0.1 + 0.2, "General", "0.3"),
        (19.99, "[$€-407] #,##0.00", "19.99"),
        (7.6, "0", "8"),
        (1.5e20, "General", "1.5e+20"),
    ],
)
def test_numbers_are_shown_as_the_spreadsheet_shows_them(
    value: float, number_format: str, shown: str
) -> None:
    assert render_number(value, number_format) == shown


def test_other_values_are_shown_plainly() -> None:
    assert render_value(None, None) == ""
    assert render_value(True, None) == "TRUE"
    assert render_value(datetime.datetime(2026, 3, 1), None) == "2026-03-01"
    assert render_value(datetime.datetime(2026, 3, 1, 14, 30), None) == "2026-03-01 14:30:00"


def test_xlsx_values_formats_and_flags(tmp_path: Path) -> None:
    data = samples.workbook(
        {
            "Products": [
                ["SKU", "Price", "Double", "Tripled", "Barcode", "Broken"],
                [123, 12.5, "=B2*2", "=B2*3", 12345678901234567890, "#N/A"],
            ]
        },
        formats={"Products": {"A2": "00000", "B2": "0.00"}},
        saved_results={"C2": "25"},
    )
    rows = _rows(tmp_path, "list.xlsx", data)
    sku, price, doubled, tripled, barcode, _broken = rows[1].cells
    assert (sku.text, sku.number_format) == ("00123", "00000")
    assert price.text == "12.50"
    # A formula's saved result is used. One that was never calculated has no value at all.
    assert (doubled.text, doubled.flags) == ("25", ())
    assert (tripled.text, tripled.flags) == ("", (FORMULA_WITHOUT_VALUE,))
    assert not tripled.usable
    assert PRECISION_LOST in barcode.flags


def test_xlsx_error_cells_are_flagged(tmp_path: Path) -> None:
    data = samples.workbook(
        {"Products": [["SKU", "Ratio"], ["A-1", "=1/0"]]}, saved_results={"B2": "#DIV/0!"}
    )
    path = samples.write(tmp_path, "list.xlsx", data)
    # The saved result is only an error once the cell is typed as one, as Excel writes it.
    import re
    import zipfile

    patched = tmp_path / "errors.xlsx"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(patched, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                content = re.sub(rb'<c r="B2"', b'<c r="B2" t="e"', content)
            target.writestr(info, content)
    with get_reader("xlsx").open(patched, LIMITS) as workbook:
        rows = list(workbook.sheets[0].rows())
    assert rows[1].cells[1].flags == (ERROR_VALUE,)
    assert not rows[1].cells[1].usable


def test_xlsx_merged_cells_show_their_value_across_the_range(tmp_path: Path) -> None:
    data = samples.workbook(
        {
            "Products": [
                ["SKU", "Dimensions", None, None, "Brand"],
                [None, "W", "H", "D", None],
                ["A-1", 1, 2, 3, "Acme"],
                ["A-2", 4, 5, 6, None],
                ["A-3", 7, 8, 9, None],
                ["A-4", 1, 1, 1, "Other"],
            ]
        },
        merges={"Products": ["B1:D1", "A1:A2", "E3:E5"]},
    )
    rows = _rows(tmp_path, "list.xlsx", data)
    assert [cell.text for cell in rows[0].cells] == [
        "SKU", "Dimensions", "Dimensions", "Dimensions", "Brand",
    ]  # fmt: skip
    assert rows[0].cells[2].merged_from == "B1" and rows[0].cells[1].merged_from is None
    assert (rows[1].cells[0].text, rows[1].cells[0].merged_from) == ("SKU", "A1")
    # A brand merged down three rows applies to each of them, and to no row after.
    assert [row.cell(4).text for row in rows[2:]] == ["Acme", "Acme", "Acme", "Other"]
    assert rows[3].cell(4).merged_from == "E3"


def test_xlsx_sheets_keep_their_order_and_hidden_state(tmp_path: Path) -> None:
    data = samples.workbook(
        {"First": [["a"]], "Secret": [["b"]], "Third": [["c"]], "Fourth": [["d"]]},
        hidden=("Secret",),
    )
    path = samples.write(tmp_path, "list.xlsx", data)
    with get_reader("xlsx").open(path, LIMITS) as workbook:
        assert [(s.index, s.name, s.hidden) for s in workbook.sheets] == [
            (0, "First", False),
            (1, "Secret", True),
            (2, "Third", False),
        ]
        # The fourth sheet is past the limit: counted, not opened.
        assert workbook.sheets_over_limit == 1
        first = workbook.sheets[0]
        assert _texts(list(first.rows())) == _texts(list(first.rows())) == [["a"]]


def test_xlsx_empty_rows_keep_row_numbers_in_step(tmp_path: Path) -> None:
    data = samples.workbook({"Products": [["Price list"], [], ["SKU", "Name"], ["A-1", "Mug"]]})
    rows = _rows(tmp_path, "list.xlsx", data)
    assert [row.number for row in rows] == [1, 2, 3, 4]
    assert rows[1].empty and not rows[2].empty


def test_columns_past_the_limit_are_not_read(tmp_path: Path) -> None:
    wide = [[f"c{i}" for i in range(60)]]
    assert len(_rows(tmp_path, "wide.xlsx", samples.workbook({"S": wide}))[0].cells) == 50
    assert len(_rows(tmp_path, "wide.csv", samples.table_csv(wide * 2))[0].cells) == 50


def test_file_that_only_looks_like_a_workbook_is_unreadable(tmp_path: Path) -> None:
    # This passes the upload check, which looks at structure only.
    path = samples.write(tmp_path, "list.xlsx", samples.xlsx())
    with pytest.raises(SpreadsheetUnreadableError), get_reader("xlsx").open(path, LIMITS) as book:
        for sheet in book.sheets:
            list(sheet.rows())
