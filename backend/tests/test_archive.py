"""Covers ZIP expansion: zip slip, zip bombs, nesting, and per-entry rejections."""

import stat
import zipfile
from pathlib import Path

import pytest

import samples
from app.config import get_uploads_config
from app.files.reasons import FileRejectedError
from app.ingest.archive import ArchiveContents, ArchiveLimits, expand
from app.ingest.kinds.base import CheckLimits

MB = 1024 * 1024
LIMITS = ArchiveLimits(
    max_entries=200,
    max_total_bytes=100 * MB,
    max_entry_bytes=10 * MB,
    max_ratio=100,
    ratio_min_bytes=MB,
)
CHECKS = CheckLimits(10, 50_000_000, 65536, 100 * MB)


def _expand(tmp_path: Path, data: bytes, limits: ArchiveLimits = LIMITS) -> ArchiveContents:
    source = samples.write(tmp_path, "upload.zip", data)
    workdir = tmp_path / "entries"
    workdir.mkdir()
    return expand(source, workdir, get_uploads_config()["types"], limits, CHECKS, 255)


def _rejected(tmp_path: Path, data: bytes, limits: ArchiveLimits = LIMITS) -> str:
    with pytest.raises(FileRejectedError) as caught:
        _expand(tmp_path, data, limits)
    return caught.value.rejection.code


def test_supported_entries_are_extracted_in_order(tmp_path: Path) -> None:
    contents = _expand(
        tmp_path,
        samples.zip_of(
            {
                "photos/front.png": samples.png(),
                "photos/back.jpg": samples.jpeg(),
                "specs.pdf": samples.pdf(2),
                "prices.csv": samples.csv(),
            }
        ),
    )
    assert [entry.name for entry in contents.entries] == [
        "front.png",
        "back.jpg",
        "specs.pdf",
        "prices.csv",
    ]
    assert all(entry.rejection is None and entry.path for entry in contents.entries)
    assert contents.entries[2].details == {"pages": 2}
    # Extracted under the work folder by index, never by the member's path.
    assert {entry.path.name for entry in contents.entries if entry.path} == {
        "entry-1",
        "entry-2",
        "entry-3",
        "entry-4",
    }


@pytest.mark.parametrize(
    "member",
    ["../evil.csv", "a/../../evil.csv", "/etc/evil.csv", "C:/evil.csv", "a\\..\\..\\evil.csv"],
)
def test_zip_slip_rejects_the_whole_archive(tmp_path: Path, member: str) -> None:
    data = samples.zip_with_infos(
        [(zipfile.ZipInfo("ok.csv"), samples.csv()), (zipfile.ZipInfo(member), samples.csv())]
    )
    assert _rejected(tmp_path, data) == "zip_unsafe_path"
    assert not list((tmp_path / "entries").iterdir())


def test_symlink_rejects_the_whole_archive(tmp_path: Path) -> None:
    link = zipfile.ZipInfo("link.csv")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert (
        _rejected(tmp_path, samples.zip_with_infos([(link, b"/etc/passwd")])) == "zip_unsafe_path"
    )


def test_too_many_entries(tmp_path: Path) -> None:
    data = samples.zip_of({f"f{i}.csv": samples.csv() for i in range(6)})
    limits = ArchiveLimits(5, 100 * MB, 10 * MB, 100, MB)
    assert _rejected(tmp_path, data, limits) == "zip_too_many_entries"


def test_zip_bomb_by_compression_ratio(tmp_path: Path) -> None:
    # 5 MB of zeros compresses to a few kilobytes: a ratio far beyond any real file.
    data = samples.zip_of({"zeros.csv": b"0" * (5 * MB)})
    assert _rejected(tmp_path, data) == "zip_bomb"


def test_zip_bomb_by_total_size(tmp_path: Path) -> None:
    data = samples.zip_of({f"part{i}.csv": samples.csv(2000) for i in range(3)})
    limits = ArchiveLimits(200, 100_000, 10 * MB, 100, MB)
    assert _rejected(tmp_path, data, limits) == "zip_too_large"


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_lying_headers_never_expand_past_the_declared_size(
    tmp_path: Path, compression: int
) -> None:
    data = bytearray(samples.zip_of({"data.csv": samples.csv(5000)}, compression))
    # Rewrite the declared size in both headers to claim the entry is 10 bytes.
    for header, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        position = data.find(header)
        data[position + offset : position + offset + 4] = (10).to_bytes(4, "little")
    contents = _expand(tmp_path, bytes(data))
    # Reading stops at the declared size, so the checksum fails rather than anything expanding.
    assert contents.entries[0].rejection is not None
    assert contents.entries[0].rejection.code == "corrupt_file"
    assert not list((tmp_path / "entries").iterdir())


def test_small_repetitive_files_are_not_mistaken_for_bombs(tmp_path: Path) -> None:
    contents = _expand(tmp_path, samples.zip_of({"blank.csv": b"a," * 20_000}))
    assert contents.entries[0].rejection is None


def test_entry_problems_are_reported_without_rejecting_the_archive(tmp_path: Path) -> None:
    data = samples.zip_of(
        {
            "inner.zip": samples.zip_of({"a.csv": samples.csv()}),
            "notes.docx": b"docx",
            "empty.csv": b"",
            "fake.png": b"not an image",
            "good.png": samples.png(),
        }
    )
    contents = _expand(tmp_path, data)
    codes = {
        entry.name: entry.rejection.code if entry.rejection else None for entry in contents.entries
    }
    assert codes == {
        "inner.zip": "nested_archive",
        "notes.docx": "unsupported_type",
        "empty.csv": "empty_file",
        "fake.png": "wrong_type",
        "good.png": None,
    }


def test_entry_over_the_size_limit_is_rejected_alone(tmp_path: Path) -> None:
    data = samples.zip_of({"big.csv": samples.csv(3000), "small.csv": samples.csv()})
    limits = ArchiveLimits(200, 100 * MB, 50_000, 100, MB)
    contents = _expand(tmp_path, data, limits)
    assert [entry.rejection.code if entry.rejection else None for entry in contents.entries] == [
        "file_too_large",
        None,
    ]


def test_system_files_are_skipped_and_counted(tmp_path: Path) -> None:
    data = samples.zip_of(
        {
            "__MACOSX/._photo.png": b"junk",
            ".DS_Store": b"junk",
            "folder/.hidden.csv": b"junk",
            "Thumbs.db": b"junk",
            "photo.png": samples.png(),
        }
    )
    contents = _expand(tmp_path, data)
    assert [entry.name for entry in contents.entries] == ["photo.png"]
    assert contents.skipped_system_files == 4


def test_damaged_archive_is_rejected(tmp_path: Path) -> None:
    assert _rejected(tmp_path, b"PK\x03\x04 not really a zip") == "corrupt_file"
