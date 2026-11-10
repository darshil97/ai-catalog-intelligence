"""Builds small sample files for ingest tests, so no binary fixtures live in the repository."""

import io
import zipfile
from pathlib import Path


def png(width: int = 4, height: int = 4, color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


def jpeg(width: int = 4, height: int = 4, color: tuple[int, int, int] = (30, 200, 30)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="JPEG")
    return out.getvalue()


def webp(width: int = 4, height: int = 4) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), (10, 10, 200)).save(out, format="WEBP")
    return out.getvalue()


def pdf(pages: int = 1, password: str | None = None) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if password:
        writer.encrypt(user_password=password, owner_password=password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def csv(rows: int = 3) -> bytes:
    lines = ["sku,name,price"] + [f"SKU-{i},Mug {i},{i}.50" for i in range(rows)]
    return ("\n".join(lines) + "\n").encode()


def xlsx() -> bytes:
    """The smallest structure that counts as a workbook for the M2 check."""
    return zip_of(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "xl/worksheets/sheet1.xml": b"<worksheet/>",
        }
    )


def zip_of(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def zip_with_infos(infos: list[tuple[zipfile.ZipInfo, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, data in infos:
            archive.writestr(info, data)
    return out.getvalue()


def write(directory: Path, name: str, data: bytes) -> Path:
    path = directory / name
    path.write_bytes(data)
    return path


def table_csv(rows: list[list[str]], delimiter: str = ",", encoding: str = "utf-8") -> bytes:
    """A CSV built from rows of text, quoted as a spreadsheet program would."""
    import csv as csv_module

    out = io.StringIO()
    csv_module.writer(out, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return out.getvalue().encode(encoding)


def workbook(
    sheets: dict[str, list[list[object]]],
    *,
    merges: dict[str, list[str]] | None = None,
    formats: dict[str, dict[str, str]] | None = None,
    hidden: tuple[str, ...] = (),
    saved_results: dict[str, str] | None = None,
) -> bytes:
    """A real workbook. Each sheet is rows of values; a string starting with "=" is a formula.

    Args:
        merges: Sheet name to merged ranges, such as {"Products": ["B1:D1"]}.
        formats: Sheet name to cell to number format, such as {"Products": {"A2": "00000"}}.
        hidden: Names of sheets to hide.
        saved_results: Cell to the result Excel would have saved for its formula, on the first
            sheet. A formula written here has none otherwise, as when a file was never opened
            in a spreadsheet program.
    """
    import re

    import openpyxl

    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
        for cell_range in (merges or {}).get(name, []):
            sheet.merge_cells(cell_range)
        for cell, number_format in (formats or {}).get(name, {}).items():
            sheet[cell].number_format = number_format
        if name in hidden:
            sheet.sheet_state = "hidden"
    out = io.BytesIO()
    book.save(out)
    if not saved_results:
        return out.getvalue()

    patched = io.BytesIO()
    with (
        zipfile.ZipFile(out) as source,
        zipfile.ZipFile(patched, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                text = data.decode()
                for cell, result in saved_results.items():
                    text, count = re.subn(
                        rf'(<c r="{cell}"[^>]*><f>[^<]*</f>)<v ?/>', rf"\1<v>{result}</v>", text
                    )
                    assert count == 1, f"no formula found in {cell}"
                data = text.encode()
            target.writestr(info, data)
    return patched.getvalue()
