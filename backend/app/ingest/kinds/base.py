"""The FileKind contract and its registry, keyed by extension."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CheckLimits:
    """Limits a check applies. Per-user values are already resolved; None means unlimited."""

    max_pdf_pages: int | None
    max_image_pixels: int
    csv_sniff_bytes: int
    zip_max_total_bytes: int


class FileKind(Protocol):
    """Checks one family of files. Must not trust the extension or the browser's type."""

    name: str
    extensions: frozenset[str]

    def check(self, path: Path, extension: str, limits: CheckLimits) -> dict[str, Any]:
        """Check the file's contents match its extension and are usable.

        Returns:
            What the check found, stored as the file's details (such as a page count).

        Raises:
            app.files.reasons.FileRejectedError: if the file can't be used.
        """
        ...


_REGISTRY: dict[str, FileKind] = {}


def register_kind(kind: FileKind) -> FileKind:
    """Make a kind responsible for its extensions."""
    for extension in kind.extensions:
        _REGISTRY[extension] = kind
    logger.info("Registered file kind: name=%s extensions=%s", kind.name, sorted(kind.extensions))
    return kind


def get_kind(extension: str) -> FileKind:
    """Return the kind that checks this extension.

    Raises:
        ValueError: if no kind handles it, naming the extensions that are handled.
    """
    import app.ingest.kinds  # noqa: F401 - registers the kinds

    if extension not in _REGISTRY:
        raise ValueError(
            f"No file kind for .{extension}. Registered: {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[extension]


def registered_extensions() -> set[str]:
    """Return every extension a kind handles."""
    import app.ingest.kinds  # noqa: F401

    return set(_REGISTRY)
