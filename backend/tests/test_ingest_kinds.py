"""Covers the worker's file checks: signatures, encryption, page and pixel limits."""

from pathlib import Path

import pytest

import samples
from app.files.reasons import FileRejectedError
from app.ingest.kinds.base import CheckLimits, get_kind

LIMITS = CheckLimits(
    max_pdf_pages=10, max_image_pixels=50_000_000, csv_sniff_bytes=65536, zip_max_total_bytes=10**8
)


def _check(tmp_path: Path, name: str, data: bytes, limits: CheckLimits = LIMITS) -> dict:
    extension = name.rsplit(".", 1)[1]
    return get_kind(extension).check(samples.write(tmp_path, name, data), extension, limits)


def _rejected(tmp_path: Path, name: str, data: bytes, limits: CheckLimits = LIMITS) -> str:
    with pytest.raises(FileRejectedError) as caught:
        _check(tmp_path, name, data, limits)
    return caught.value.rejection.code


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("a.csv", samples.csv()),
        ("b.csv", "sku,name\nX1,Café crème\n".encode("utf-16")),
        ("c.csv", "sku,name\nX1,Caf\xe9\n".encode("cp1252")),
        ("d.xlsx", samples.xlsx()),
        ("e.pdf", samples.pdf(3)),
        ("f.png", samples.png()),
        ("g.jpg", samples.jpeg()),
        ("h.jpeg", samples.jpeg()),
        ("i.webp", samples.webp()),
    ],
)
def test_valid_files_pass(tmp_path: Path, name: str, data: bytes) -> None:
    assert isinstance(_check(tmp_path, name, data), dict)


def test_details_are_recorded(tmp_path: Path) -> None:
    assert _check(tmp_path, "a.pdf", samples.pdf(3)) == {"pages": 3}
    assert _check(tmp_path, "a.png", samples.png(5, 7))["width"] == 5
    assert _check(tmp_path, "a.csv", samples.csv())["encoding"] == "utf-8"
    assert _check(tmp_path, "a.xlsx", samples.xlsx())["sheets"] == 1


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("png-named.jpg", samples.png()),
        ("jpeg-named.png", samples.jpeg()),
        ("text-named.pdf", b"not really a pdf"),
        ("pdf-named.csv", samples.pdf()),
        ("png-named.csv", samples.png()),
        ("zip-named.csv", samples.zip_of({"a.txt": b"hi"})),
        ("binary.csv", b"\x01\x02\x03\x04" * 100),
        ("plain-zip.xlsx", samples.zip_of({"a.txt": b"hi"})),
        ("text.xlsx", b"sku,name\n"),
        ("old.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600),
        ("text.webp", b"RIFF0000WEBX"),
    ],
)
def test_contents_must_match_the_extension(tmp_path: Path, name: str, data: bytes) -> None:
    assert _rejected(tmp_path, name, data) == "wrong_type"


def test_pdf_over_the_page_limit_is_rejected(tmp_path: Path) -> None:
    assert _rejected(tmp_path, "long.pdf", samples.pdf(11)) == "too_many_pages"
    assert _check(tmp_path, "ten.pdf", samples.pdf(10)) == {"pages": 10}


def test_unlimited_pages_override(tmp_path: Path) -> None:
    limits = CheckLimits(None, 50_000_000, 65536, 10**8)
    assert _check(tmp_path, "long.pdf", samples.pdf(11), limits) == {"pages": 11}


def test_password_protected_pdf_is_rejected(tmp_path: Path) -> None:
    assert _rejected(tmp_path, "locked.pdf", samples.pdf(1, password="secret")) == "encrypted_file"


def test_damaged_pdf_is_rejected(tmp_path: Path) -> None:
    assert _rejected(tmp_path, "broken.pdf", b"%PDF-1.7\n garbage") == "corrupt_file"


def test_password_protected_xlsx_is_rejected(tmp_path: Path) -> None:
    data = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100 + "EncryptionInfo".encode("utf-16-le")
    )
    assert _rejected(tmp_path, "locked.xlsx", data) == "encrypted_file"


def test_damaged_image_is_rejected(tmp_path: Path) -> None:
    assert _rejected(tmp_path, "cut.png", samples.png(50, 50)[:60]) == "corrupt_file"


def test_image_bomb_is_rejected(tmp_path: Path) -> None:
    small = CheckLimits(10, 100, 65536, 10**8)
    assert _rejected(tmp_path, "big.png", samples.png(20, 20), small) == "image_too_large"
    # Pillow only warns between the limit and twice the limit; that's refused too.
    assert _rejected(tmp_path, "edge.png", samples.png(12, 12), small) == "image_too_large"


def test_xlsx_that_expands_too_far_is_rejected(tmp_path: Path) -> None:
    tiny = CheckLimits(10, 50_000_000, 65536, 10)
    assert _rejected(tmp_path, "big.xlsx", samples.xlsx(), tiny) == "zip_too_large"
