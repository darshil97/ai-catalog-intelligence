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


def text_image(lines: list[str], width: int = 900, line_height: int = 70) -> bytes:
    """A JPEG photo-like image with the given lines of black text on white, for OCR."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (width, line_height * (len(lines) + 1)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)
    for index, line in enumerate(lines):
        draw.text((30, 30 + index * line_height), line, fill="black", font=font)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=95)
    return out.getvalue()


def text_pdf(
    pages: list[list[str]],
    *,
    images: dict[int, list[bytes]] | None = None,
    scanned: dict[int, list[str]] | None = None,
) -> bytes:
    """A real PDF built by hand: each page's lines as text, top to bottom.

    Args:
        images: Page index to JPEG images drawn on that page (embedded images).
        scanned: Page index to lines drawn as a full-page picture instead of text, as a scanner
            would produce. Those pages hold no text of their own.
    """
    from PIL import Image

    images = dict(images or {})
    for index, lines in (scanned or {}).items():
        images[index] = [text_image(lines, width=1200, line_height=80), *images.get(index, [])]

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    # The page tree's number is only known at the end, so pages point at a placeholder for it.
    page_entries: list[tuple[bytes, list[tuple[int, int, int]]]] = []
    for index, lines in enumerate(pages):
        ops = [b"BT /F1 12 Tf 14 TL 50 760 Td"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({escaped}) Tj T*".encode("latin-1"))
        ops.append(b"ET")
        placed = []
        for number, jpeg in enumerate(images.get(index, [])):
            picture = Image.open(io.BytesIO(jpeg))
            xobject = add(
                f"<< /Type /XObject /Subtype /Image /Width {picture.width} /Height "
                f"{picture.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter "
                f"/DCTDecode /Length {len(jpeg)} >>\nstream\n".encode()
                + jpeg
                + b"\nendstream"
            )
            placed.append((xobject, picture.width, picture.height))
            draw_w = 500
            draw_h = int(500 * picture.height / picture.width)
            y = 700 - number * (draw_h + 20) if index not in (scanned or {}) else 300
            ops.append(f"q {draw_w} 0 0 {draw_h} 50 {y} cm /Im{number} Do Q".encode())
        page_entries.append((b"\n".join(ops), placed))
    for content, placed in page_entries:
        stream = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        xobjects = " ".join(f"/Im{n} {obj} 0 R" for n, (obj, _, _) in enumerate(placed))
        page_ids.append(
            add(
                f"<< /Type /Page /Parent PAGES 0 R /MediaBox [0 0 612 792] /Contents {stream} 0 R "
                f"/Resources << /Font << /F1 {font} 0 R >> /XObject << {xobjects} >> >> >>".encode()
            )
        )
    pages_id = add(
        f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] "
        f"/Count {len(page_ids)} >>".encode()
    )
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    objects = [body.replace(b"PAGES 0 R", f"{pages_id} 0 R".encode()) for body in objects]

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\n"
    out.write(f"{trailer}startxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def product_photo(
    colour: tuple[int, int, int] = (190, 30, 40), shape: str = "mug", label: str = "AK-1003"
) -> "Image.Image":  # noqa: F821 - PIL loads lazily
    """A simple product shot: a coloured object on a light background, with a label."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (600, 480), (236, 236, 232))
    draw = ImageDraw.Draw(image)
    if shape == "mug":
        draw.rounded_rectangle((170, 110, 400, 400), radius=30, fill=colour)
        draw.ellipse((380, 180, 480, 320), outline=colour, width=22)
    elif shape == "box":
        draw.rectangle((120, 60, 480, 430), fill=colour)
        draw.rectangle((150, 300, 450, 400), fill=(250, 250, 250))
    else:
        draw.ellipse((150, 90, 450, 390), fill=colour)
        draw.polygon([(300, 40), (220, 160), (380, 160)], fill=(40, 40, 40))
    draw.text((190, 420), label, fill=(20, 20, 20), font=ImageFont.load_default(size=28))
    return image


def jpeg_of(image: "Image.Image", quality: int = 90) -> bytes:  # noqa: F821
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=quality)
    return out.getvalue()
