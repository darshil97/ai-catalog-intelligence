"""Why a file was refused or left out: a stable code for the frontend, and a message for people.

Messages are stored with the file, so they read the same later even if the wording here changes.
"""

from dataclasses import dataclass

SUPPORTED_TYPES_TEXT = "CSV, XLSX, PDF, JPEG, PNG, WebP or ZIP"


@dataclass(frozen=True)
class Rejection:
    """A refusal, with the code the frontend branches on and the message it shows."""

    code: str
    message: str


class FileRejectedError(Exception):
    """A file can't be used. Carries the reason to record against it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.rejection = Rejection(code, message)


def format_bytes(size: int) -> str:
    """Return a size the way people read it, such as "10 MB" or "512 KB"."""
    if size >= 1024 * 1024:
        value = size / (1024 * 1024)
        return f"{value:.0f} MB" if value >= 10 or value == int(value) else f"{value:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} bytes"


def unsupported_type(extension: str) -> Rejection:
    shown = f".{extension} files" if extension else "files without an extension"
    return Rejection("unsupported_type", f"We can't use {shown}. Upload {SUPPORTED_TYPES_TEXT}.")


def empty_file() -> Rejection:
    return Rejection("empty_file", "This file is empty.")


def file_too_large(size: int, limit: int) -> Rejection:
    return Rejection(
        "file_too_large",
        f"This file is {format_bytes(size)}, over the {format_bytes(limit)} limit.",
    )


def too_many_files(limit: int) -> Rejection:
    return Rejection("too_many_files", f"A batch can have at most {limit} files.")


def submission_too_large(total: int, limit: int) -> Rejection:
    return Rejection(
        "submission_too_large",
        f"These files add up to {format_bytes(total)}, over the {format_bytes(limit)} limit "
        "for one batch.",
    )


def duplicate_file(existing_name: str) -> Rejection:
    return Rejection("duplicate_file", f"This batch already has this file, as {existing_name}.")


def upload_not_completed() -> Rejection:
    return Rejection("upload_not_completed", "The upload didn't finish. Upload the file again.")


def upload_mismatch() -> Rejection:
    return Rejection(
        "upload_mismatch",
        "The uploaded file didn't match what was registered. Upload it again.",
    )


def wrong_type(extension: str) -> Rejection:
    return Rejection("wrong_type", f"This file's contents don't match its .{extension} extension.")


def corrupt_file() -> Rejection:
    return Rejection("corrupt_file", "This file is damaged and can't be opened.")


def encrypted_file() -> Rejection:
    return Rejection(
        "encrypted_file",
        "This file is password-protected. Remove the password and upload it again.",
    )


def too_many_pages(pages: int, limit: int) -> Rejection:
    return Rejection("too_many_pages", f"This PDF has {pages} pages, over the {limit}-page limit.")


def image_too_large(pixels: int | None = None) -> Rejection:
    detail = f" ({pixels:,} pixels)" if pixels else ""
    return Rejection("image_too_large", f"This image is too large to process{detail}.")


def zip_unsafe_path(member: str) -> Rejection:
    return Rejection(
        "zip_unsafe_path",
        f"This ZIP contains an unsafe path ({member[:100]}), so none of it was used.",
    )


def zip_too_many_entries(entries: int, limit: int) -> Rejection:
    return Rejection(
        "zip_too_many_entries",
        f"This ZIP has {entries} entries, over the {limit} limit, so none of it was used.",
    )


def zip_too_large(limit: int) -> Rejection:
    return Rejection(
        "zip_too_large",
        f"This ZIP expands to more than {format_bytes(limit)}, so none of it was used.",
    )


def zip_bomb(member: str) -> Rejection:
    return Rejection(
        "zip_bomb",
        f"This ZIP is compressed far more than real files are ({member[:100]}), so none of it "
        "was used.",
    )


def nested_archive() -> Rejection:
    return Rejection(
        "nested_archive", "ZIPs inside a ZIP aren't opened. Upload this one on its own."
    )


def image_limit(limit: int) -> Rejection:
    return Rejection("image_limit", f"Left out: only the first {limit} images in a batch are used.")
