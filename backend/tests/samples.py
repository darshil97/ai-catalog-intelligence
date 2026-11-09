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
