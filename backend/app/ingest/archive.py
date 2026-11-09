"""Expanding uploaded ZIPs safely.

An archive is rejected whole if it is unsafe: too many entries, a path that could escape its
folder (zip slip), a symlink, a total expanded size over the limit, or an entry compressed far
more than real files are (a zip bomb). Otherwise each entry is kept or rejected on its own,
and every rejection is reported. Sizes in ZIP headers can lie, so bytes are counted while
extracting, and extraction stops the moment a limit is passed.
"""

import hashlib
import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.files import reasons
from app.files.names import clean_name, extension_of
from app.files.reasons import FileRejectedError, Rejection
from app.ingest.kinds.base import CheckLimits, get_kind

_CHUNK_BYTES = 1024 * 1024
_SYMLINK_MODE = 0o120000
_FILE_TYPE_MASK = 0o170000
# Files operating systems add to archives. Skipped, and only counted.
_SYSTEM_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


@dataclass(frozen=True)
class ArchiveLimits:
    """Safety limits for one archive."""

    max_entries: int
    max_total_bytes: int
    max_entry_bytes: int
    max_ratio: int
    ratio_min_bytes: int


@dataclass
class ArchiveEntry:
    """One file found in an archive.

    Attributes:
        member: Its full path inside the archive.
        index: Its position in the archive, from 1. Orders entries for the image limit.
        name: Its cleaned file name, without folders.
        extension: Lowercase extension.
        size_bytes: Its size: actual if extracted, otherwise as the header declared.
        path: Where it was extracted, for entries that passed every check.
        sha256: Hex SHA-256, for extracted entries.
        details: What its check found.
        rejection: Why it was rejected, if it was.
    """

    member: str
    index: int
    name: str
    extension: str
    size_bytes: int
    path: Path | None = None
    sha256: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    rejection: Rejection | None = None


@dataclass
class ArchiveContents:
    """What an archive held."""

    entries: list[ArchiveEntry]
    skipped_system_files: int


def _reject(rejection: Rejection) -> FileRejectedError:
    return FileRejectedError(rejection.code, rejection.message)


def _is_unsafe_path(member: str) -> bool:
    """True for a path that could land outside the extraction folder."""
    if "\x00" in member:
        return True
    path = member.replace("\\", "/")
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return True
    return ".." in path.split("/")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & _FILE_TYPE_MASK == _SYMLINK_MODE


def _is_system_file(member: str) -> bool:
    parts = [part for part in member.replace("\\", "/").split("/") if part]
    if not parts:
        return False
    if any(part == "__MACOSX" for part in parts):
        return True
    base = parts[-1]
    return base.startswith(".") or base.lower() in _SYSTEM_NAMES


def _screen(infos: list[zipfile.ZipInfo], limits: ArchiveLimits) -> None:
    """Reject the whole archive for anything unsafe, before extracting a single byte."""
    if len(infos) > limits.max_entries:
        raise _reject(reasons.zip_too_many_entries(len(infos), limits.max_entries))
    declared_total = 0
    for info in infos:
        if _is_unsafe_path(info.filename) or _is_symlink(info):
            raise _reject(reasons.zip_unsafe_path(info.filename))
        declared_total += info.file_size
        if declared_total > limits.max_total_bytes:
            raise _reject(reasons.zip_too_large(limits.max_total_bytes))
        if info.file_size >= limits.ratio_min_bytes and info.file_size > limits.max_ratio * max(
            info.compress_size, 1
        ):
            raise _reject(reasons.zip_bomb(info.filename))


class _TotalExceededError(Exception):
    """The archive expanded past its total limit while extracting."""


def _extract(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
    limits: ArchiveLimits,
    total_so_far: int,
) -> tuple[int, str]:
    """Write one entry to disk, counting real bytes. Returns its size and SHA-256.

    Raises:
        FileRejectedError: if the entry passes the per-entry limit or its compression ratio.
        _TotalExceededError: if the archive passes its total limit.
        zipfile.BadZipFile: if the data is damaged (including a wrong CRC).
    """
    digest = hashlib.sha256()
    written = 0
    with archive.open(info) as source, destination.open("wb") as out:
        while chunk := source.read(_CHUNK_BYTES):
            written += len(chunk)
            if total_so_far + written > limits.max_total_bytes:
                raise _TotalExceededError
            if written > limits.max_entry_bytes:
                raise _reject(reasons.file_too_large(written, limits.max_entry_bytes))
            if written >= limits.ratio_min_bytes and written > limits.max_ratio * max(
                info.compress_size, 1
            ):
                raise _reject(reasons.zip_bomb(info.filename))
            digest.update(chunk)
            out.write(chunk)
    return written, digest.hexdigest()


def expand(
    path: Path,
    workdir: Path,
    supported: dict[str, dict[str, str]],
    limits: ArchiveLimits,
    check_limits: CheckLimits,
    max_name_length: int,
) -> ArchiveContents:
    """Extract and check every entry of a ZIP.

    Args:
        path: The ZIP.
        workdir: An empty folder for extracted entries, named by index, never by member path.
        supported: Extension to type spec, as in uploads.types.
        limits: Archive safety limits.
        check_limits: Limits for checking each extracted file.
        max_name_length: Longest name kept for an entry.

    Raises:
        FileRejectedError: if the archive is damaged or unsafe, so none of it can be used.
    """
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise _reject(reasons.corrupt_file()) from exc

    with archive:
        infos = archive.infolist()
        _screen(infos, limits)
        entries: list[ArchiveEntry] = []
        skipped = 0
        total = 0
        for index, info in enumerate(infos, start=1):
            if info.is_dir():
                continue
            if _is_system_file(info.filename):
                skipped += 1
                continue
            name = clean_name(posixpath.basename(info.filename), max_name_length)
            entry = ArchiveEntry(
                member=info.filename,
                index=index,
                name=name,
                extension=extension_of(name),
                size_bytes=info.file_size,
            )
            entries.append(entry)
            entry.rejection = _entry_rejection(info, entry.extension, supported, limits)
            if entry.rejection is not None:
                continue

            destination = workdir / f"entry-{index}"
            try:
                entry.size_bytes, entry.sha256 = _extract(archive, info, destination, limits, total)
            except _TotalExceededError as exc:
                raise _reject(reasons.zip_too_large(limits.max_total_bytes)) from exc
            except FileRejectedError as exc:
                destination.unlink(missing_ok=True)
                if exc.rejection.code == "zip_bomb":
                    raise
                entry.rejection = exc.rejection
                continue
            except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError):
                destination.unlink(missing_ok=True)
                entry.rejection = reasons.corrupt_file()
                continue
            total += entry.size_bytes
            if entry.size_bytes == 0:
                destination.unlink(missing_ok=True)
                entry.rejection = reasons.empty_file()
                continue

            try:
                entry.details = get_kind(entry.extension).check(
                    destination, entry.extension, check_limits
                )
            except FileRejectedError as exc:
                destination.unlink(missing_ok=True)
                entry.rejection = exc.rejection
                continue
            entry.path = destination
    return ArchiveContents(entries=entries, skipped_system_files=skipped)


def _entry_rejection(
    info: zipfile.ZipInfo,
    extension: str,
    supported: dict[str, dict[str, str]],
    limits: ArchiveLimits,
) -> Rejection | None:
    """Return why an entry can't be used, judged from its header alone, or None."""
    if extension == "zip":
        return reasons.nested_archive()
    if extension not in supported:
        return reasons.unsupported_type(extension)
    if info.flag_bits & 0x1:
        return reasons.encrypted_file()
    if info.file_size > limits.max_entry_bytes:
        return reasons.file_too_large(info.file_size, limits.max_entry_bytes)
    return None
